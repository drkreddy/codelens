"""Persist a parsed workspace in an embedded graph database (Kuzu) and load it back.

The in-memory CodeGraph stays the source of truth for the code. The store is a
saved copy of it that can also be queried directly with Cypher (`codelens query`).
Classified outbound calls are written for querying but are not loaded back: they
are recomputed from the stored method attrs and config on every load, so edits to
systems.toml apply without re-parsing.

Config values in the store are the already-masked ones from config_files.
Kuzu extensions (which download from the network) are never used; JSON is kept
in plain STRING columns.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

from codelens import __version__
from codelens.config_files import config_paths
from codelens.discovery import DiscoveryConfig, discover
from codelens.frontends import build_frontends
from codelens.graph import CodeGraph
from codelens.journeys import Outbound, Repo, Workspace, find_repos
from codelens.model import Confidence, Edge, EdgeKind, Location, NodeKind, Symbol
from codelens.systems import Catalog

SCHEMA_VERSION = "1"
DEFAULT_DB = Path(".codelens") / "graph.kuzu"
BATCH = 5000

DDL = [
    "CREATE NODE TABLE Meta(k STRING PRIMARY KEY, v STRING)",
    "CREATE NODE TABLE Repo(name STRING PRIMARY KEY, dir STRING, service STRING, config STRING,"
    " stats STRING, errors STRING, skipped STRING)",
    "CREATE NODE TABLE Symbol(uid STRING PRIMARY KEY, repo STRING, seq INT64, id STRING, kind STRING,"
    " name STRING, language STRING, file STRING, line INT64, col INT64, attrs STRING)",
    "CREATE REL TABLE Edge(FROM Symbol TO Symbol, repo STRING, seq INT64, kind STRING,"
    " confidence STRING, file STRING, line INT64, col INT64, attrs STRING)",
    "CREATE NODE TABLE Outbound(uid STRING PRIMARY KEY, repo STRING, method STRING,"
    " protocol STRING, client STRING, operation STRING, url STRING, host STRING,"
    " system STRING, category STRING, via STRING, reason STRING, line INT64,"
    " confidence STRING, target_repo STRING, target_endpoint STRING)",
    "CREATE REL TABLE HAS_OUTBOUND(FROM Symbol TO Outbound)",
]


def _kuzu():
    try:
        import kuzu
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("the graph store needs kuzu: pip install 'codelens[store]'") from exc
    return kuzu


def _dumps(value: Any) -> str:
    return json.dumps(value, default=sorted)


def _batches(rows: list[dict]) -> Iterable[list[dict]]:
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


# -- freshness ---------------------------------------------------------------
def fingerprint(root: Path) -> str:
    """Hash of everything the parse depends on: sources, config files and codelens itself."""
    h = hashlib.sha256(f"{SCHEMA_VERSION}|{__version__}".encode())
    discovery = DiscoveryConfig(extensions=frozenset(build_frontends(None)))
    for repo in find_repos(root):
        h.update(b"\0repo\0" + repo.name.encode())
        paths = [(f.rel_path, f.path) for f in discover(repo, discovery).files]
        paths += [(p.relative_to(repo).as_posix(), p) for p in config_paths(repo)]
        for rel, path in sorted(paths):
            h.update(b"\0" + rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def stored_fingerprint(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    try:
        db, conn = _open(db_path, read_only=True)
        rows = conn.execute("MATCH (m:Meta {k: 'fingerprint'}) RETURN m.v").get_all()
        return rows[0][0] if rows else None
    except Exception:  # unreadable or foreign file: treat as stale
        return None


def is_fresh(root: Path, db_path: Path) -> bool:
    return stored_fingerprint(db_path) == fingerprint(root)


# -- save --------------------------------------------------------------------
def _open(db_path: Path, read_only: bool):
    kuzu = _kuzu()
    db = kuzu.Database(str(db_path), read_only=read_only)
    return db, kuzu.Connection(db)


def save(ws: Workspace, db_path: Path) -> None:
    """Write the workspace to db_path, replacing any previous store atomically."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    _remove(tmp)
    db, conn = _open(tmp, read_only=False)
    try:
        for stmt in DDL:
            conn.execute(stmt)
        conn.execute("CREATE (:Meta {k: 'schema', v: $v})", {"v": SCHEMA_VERSION})
        conn.execute("CREATE (:Meta {k: 'fingerprint', v: $v})", {"v": fingerprint(ws.root)})
        for repo in ws.repos.values():
            _save_repo(conn, repo)
    finally:
        conn.close()
        db.close()
    _remove(db_path)
    os.replace(tmp, db_path)


def _remove(path: Path) -> None:
    for p in (path, path.with_name(path.name + ".wal"), path.with_name(path.name + ".shadow")):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def _loc(loc: Location | None) -> tuple[str, int, int]:
    return (loc.path, loc.line, loc.column) if loc else ("", 0, 0)


def _save_repo(conn, repo: Repo) -> None:
    g = repo.graph
    conn.execute(
        "CREATE (:Repo {name: $name, dir: $dir, service: $service, config: $config,"
        " stats: $stats, errors: $errors, skipped: $skipped})",
        {"name": repo.name, "dir": str(repo.path), "service": repo.service,
         "config": _dumps(repo.config), "stats": _dumps(g.stats), "errors": _dumps(g.errors),
         "skipped": _dumps(g.skipped)})

    def uid(sid: str) -> str:
        return f"{repo.name}|{sid}"

    symbols = []
    for seq, s in enumerate(g.symbols.values()):
        file, line, col = _loc(s.location)
        symbols.append({"uid": uid(s.id), "repo": repo.name, "seq": seq, "id": s.id, "kind": s.kind.value,
                        "name": s.name, "language": s.language, "file": file, "line": line,
                        "col": col, "attrs": _dumps(s.attrs)})
    for batch in _batches(symbols):
        conn.execute(
            "UNWIND $rows AS r CREATE (:Symbol {uid: r.uid, repo: r.repo, seq: r.seq, id: r.id, kind: r.kind,"
            " name: r.name, language: r.language, file: r.file, line: r.line, col: r.col,"
            " attrs: r.attrs})", {"rows": batch})

    edges, dangling = [], 0
    for seq, e in enumerate(g.edges):
        if e.dst is None or e.dst not in g.symbols or e.src not in g.symbols:
            dangling += 1  # the linker resolves or drops edges, so this should stay 0
            continue
        file, line, col = _loc(e.location)
        edges.append({"s": uid(e.src), "d": uid(e.dst), "repo": repo.name, "seq": seq,
                      "kind": e.kind.value, "confidence": e.confidence.value, "file": file,
                      "line": line, "col": col, "attrs": _dumps(e.attrs)})
    for batch in _batches(edges):
        conn.execute(
            "UNWIND $rows AS r MATCH (a:Symbol {uid: r.s}), (b:Symbol {uid: r.d})"
            " CREATE (a)-[:Edge {repo: r.repo, seq: r.seq, kind: r.kind, confidence: r.confidence,"
            " file: r.file, line: r.line, col: r.col, attrs: r.attrs}]->(b)", {"rows": batch})
    if dangling:
        print(f"codelens store: {repo.name}: {dangling} edges without a target not stored",
              file=sys.stderr)

    outbound = []
    for method, calls in repo.outbound.items():
        for i, o in enumerate(calls):
            outbound.append({
                "uid": f"{repo.name}|{method}|{i}", "m": uid(method), "repo": repo.name,
                "method": method, "protocol": o.protocol, "client": o.client,
                "operation": o.operation, "url": o.url or "", "host": o.host or "",
                "system": o.system, "category": o.category, "via": o.via or "",
                "reason": o.reason or "", "line": o.line, "confidence": o.confidence.value,
                "target_repo": o.target_repo or "", "target_endpoint": o.target_endpoint or ""})
    for batch in _batches(outbound):
        conn.execute(
            "UNWIND $rows AS r MATCH (m:Symbol {uid: r.m})"
            " CREATE (m)-[:HAS_OUTBOUND]->(:Outbound {uid: r.uid, repo: r.repo, method: r.method,"
            " protocol: r.protocol, client: r.client, operation: r.operation, url: r.url,"
            " host: r.host, system: r.system, category: r.category, via: r.via, reason: r.reason,"
            " line: r.line, confidence: r.confidence, target_repo: r.target_repo,"
            " target_endpoint: r.target_endpoint})", {"rows": batch})


# -- load --------------------------------------------------------------------
def load(root: Path, catalog: Catalog, db_path: Path) -> Workspace:
    """Rebuild a Workspace from the store without parsing any source."""
    db, conn = _open(db_path, read_only=True)
    try:
        repos: dict[str, Repo] = {}
        for name, dir_, service, config, stats, errors, skipped in conn.execute(
                "MATCH (r:Repo) RETURN r.name, r.dir, r.service, r.config, r.stats, r.errors,"
                " r.skipped ORDER BY r.name").get_all():
            symbols = [
                Symbol(id=sid, kind=NodeKind(kind), name=sname, language=lang,
                       location=Location(file, line, col) if file else None,
                       attrs=json.loads(attrs))
                for sid, kind, sname, lang, file, line, col, attrs in conn.execute(
                    "MATCH (s:Symbol {repo: $r}) RETURN s.id, s.kind, s.name, s.language, s.file,"
                    " s.line, s.col, s.attrs ORDER BY s.seq", {"r": name}).get_all()]
            edges = [
                Edge(src=src, dst=dst, kind=EdgeKind(kind), confidence=Confidence(conf),
                     location=Location(file, line, col) if file else None,
                     attrs=json.loads(attrs))
                for src, dst, kind, conf, file, line, col, attrs in conn.execute(
                    "MATCH (a:Symbol)-[e:Edge {repo: $r}]->(b:Symbol) RETURN a.id, b.id, e.kind,"
                    " e.confidence, e.file, e.line, e.col, e.attrs ORDER BY e.seq",
                    {"r": name}).get_all()]
            graph = CodeGraph(symbols, edges)
            graph.stats = json.loads(stats)
            graph.errors = json.loads(errors)
            graph.skipped = [tuple(x) for x in json.loads(skipped)]
            repos[name] = Repo(name, Path(dir_), service, graph, json.loads(config))
    finally:
        conn.close()
        db.close()
    return Workspace(root, catalog, repos=repos)


def open_workspace(root: Path, catalog: Catalog, db_path: Path | None = None,
                   reindex: bool = False) -> Workspace:
    """Load from the store when it is fresh; otherwise parse, then save.

    A store that cannot be written (read-only mount) is not an error: the parsed
    workspace is returned and a warning goes to stderr (stdout belongs to MCP).
    """
    db_path = db_path or (root / DEFAULT_DB)
    if not reindex and is_fresh(root, db_path):
        return load(root, catalog, db_path)
    ws = Workspace(root, catalog)
    try:
        save(ws, db_path)
    except Exception as exc:
        print(f"codelens: could not write store {db_path}: {exc}", file=sys.stderr)
    return ws


def query(db_path: Path, cypher: str) -> list[dict[str, Any]]:
    """Run Cypher against a store. The database is opened read-only, so writes fail."""
    return query_limited(db_path, cypher)["rows"]


def query_limited(db_path: Path, cypher: str, limit: int | None = None,
                  timeout_ms: int | None = None) -> dict[str, Any]:
    """Like query(), but stops after `limit` rows and can abort slow queries.

    Returns {"columns", "rows", "truncated"}. Values are made JSON-safe.
    """
    db, conn = _open(db_path, read_only=True)
    try:
        if timeout_ms:
            conn.set_query_timeout(timeout_ms)
        result = conn.execute(cypher)
        cols = result.get_column_names()
        rows: list[dict[str, Any]] = []
        truncated = False
        while result.has_next():
            if limit is not None and len(rows) >= limit:
                truncated = True
                break
            rows.append(dict(zip(cols, result.get_next())))
        return {"columns": cols, "rows": json.loads(json.dumps(rows, default=str)),
                "truncated": truncated}
    finally:
        conn.close()
        db.close()


def describe(db_path: Path) -> dict[str, Any]:
    """Tables and columns of a store, for writing Cypher against it."""
    db, conn = _open(db_path, read_only=True)
    try:
        tables = []
        for _, name, kind, *_ in conn.execute("CALL show_tables() RETURN *").get_all():
            cols = [{"name": c[1], "type": c[2], "primary_key": bool(c[4])}
                    for c in conn.execute(f"CALL table_info('{name}') RETURN *").get_all()]
            entry: dict[str, Any] = {"name": name, "kind": kind.lower(), "columns": cols}
            if kind == "REL":
                conns = conn.execute(f"CALL show_connection('{name}') RETURN *").get_all()
                if conns:
                    entry["from"], entry["to"] = conns[0][0], conns[0][1]
            tables.append(entry)
        return {"tables": tables}
    finally:
        conn.close()
        db.close()
