"""codelens command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codelens import __version__
from codelens.pipeline import analyze
from codelens.reporters import RENDERERS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codelens", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze", help="analyse a repository")
    a.add_argument("root", type=Path)
    a.add_argument("-o", "--out", type=Path, default=Path("codelens-out"))
    a.add_argument("-f", "--format", default="json,md,dot",
                   help=f"comma-separated: {','.join(RENDERERS)}")
    a.add_argument("-l", "--lang", default=None, help="limit languages, e.g. java,py")
    j = sub.add_parser("journeys", help="map API journeys across a folder of repos")
    j.add_argument("root", type=Path, help="a repo, or a folder whose subfolders are repos")
    j.add_argument("-s", "--systems", type=Path, default=None,
                   help="system catalog (TOML); defaults to ROOT/systems.toml if present")
    j.add_argument("-o", "--out", type=Path, default=Path("codelens-out"))
    m = sub.add_parser("mcp", help="serve questions about a folder of repos over MCP (stdio)")
    m.add_argument("root", type=Path, help="a repo, or a folder whose subfolders are repos")
    m.add_argument("-s", "--systems", type=Path, default=None,
                   help="system catalog (TOML); defaults to ROOT/systems.toml if present")
    m.add_argument("--db", type=Path, default=None,
                   help="graph store; defaults to ROOT/.codelens/graph.kuzu")
    m.add_argument("--reindex", action="store_true", help="re-parse even if the store is fresh")
    i = sub.add_parser("index", help="parse a folder of repos and save the graph store")
    i.add_argument("root", type=Path, help="a repo, or a folder whose subfolders are repos")
    i.add_argument("-s", "--systems", type=Path, default=None,
                   help="system catalog (TOML); defaults to ROOT/systems.toml if present")
    i.add_argument("--db", type=Path, default=None,
                   help="graph store; defaults to ROOT/.codelens/graph.kuzu")
    q = sub.add_parser("query", help="run read-only Cypher against a graph store")
    q.add_argument("db", type=Path, help="store written by `codelens index`")
    q.add_argument("cypher", help='e.g. "MATCH (s:Symbol {kind: \'endpoint\'}) RETURN s.id"')
    args = parser.parse_args(argv)

    if args.command == "query":
        return run_query(args)
    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    if args.command == "index":
        return run_index(args)
    if args.command == "journeys":
        return run_journeys(args)
    if args.command == "mcp":
        from codelens.mcp_server import build_server
        build_server(args.root, args.systems, args.db, args.reindex).run("stdio")  # stdout belongs to the protocol
        return 0
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    unknown = [f for f in formats if f not in RENDERERS]
    if unknown:
        parser.error(f"unknown format(s): {', '.join(unknown)}")
    languages = {l.strip() for l in args.lang.split(",")} if args.lang else None

    graph = analyze(args.root, languages)
    args.out.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        render, filename = RENDERERS[fmt]
        (args.out / filename).write_text(render(graph), encoding="utf-8")
        print(f"wrote {args.out / filename}")
    s = graph.summary()
    print(f"{s.get('files_analyzed', 0)} files, {sum(s['nodes'].values())} nodes, "
          f"{len(graph.edges)} edges, {len(graph.errors)} errors", file=sys.stderr)
    return 0


def run_index(args) -> int:
    from codelens.journeys import Workspace
    from codelens.store import DEFAULT_DB, save
    from codelens.systems import Catalog

    systems = args.systems or (args.root / "systems.toml")
    ws = Workspace(args.root, Catalog.load(systems if systems.is_file() else None))
    db = args.db or (args.root / DEFAULT_DB)
    save(ws, db)
    symbols = sum(len(r.graph.symbols) for r in ws.repos.values())
    print(f"wrote {db}")
    print(f"{len(ws.repos)} repos, {symbols} symbols", file=sys.stderr)
    return 0


def run_query(args) -> int:
    import json
    from codelens.store import query

    if not args.db.is_file():
        print(f"codelens: no store at {args.db} (run `codelens index` first)", file=sys.stderr)
        return 2
    try:
        rows = query(args.db, args.cypher)
    except RuntimeError as exc:
        print(f"codelens: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(rows, indent=2, default=str))
    return 0


def run_journeys(args) -> int:
    from codelens.journeys import Workspace
    from codelens.reporters.journeys_out import render_journeys_json, render_journeys_markdown
    from codelens.systems import Catalog

    systems = args.systems or (args.root / "systems.toml")
    catalog = Catalog.load(systems if systems.is_file() else None)
    ws = Workspace(args.root, catalog)
    journeys = ws.journeys()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "journeys.md").write_text(render_journeys_markdown(ws, journeys), encoding="utf-8")
    (args.out / "journeys.json").write_text(render_journeys_json(ws, journeys), encoding="utf-8")
    print(f"wrote {args.out / 'journeys.md'}")
    print(f"wrote {args.out / 'journeys.json'}")
    print(f"{len(ws.repos)} repos, {len(journeys)} journeys, "
          f"{len({j.outbound.system for j in journeys})} systems", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
