"""API journeys across a workspace of repos.

A journey starts at an entry point (REST endpoint or message listener), walks
calls through the layers of one repo, and ends at an outbound call to a named
system. When the outbound call is HTTP to another repo in the workspace, the
journey continues inside that repo from the matching endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from codelens.config_files import load_repo_config, resolve_placeholders
from codelens.graph import CodeGraph
from codelens.model import Confidence, EdgeKind, NodeKind
from codelens.pipeline import analyze
from codelens.systems import Catalog

BUILD_MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "pyproject.toml",
                 "setup.py", "src")
CONF_ORDER = [Confidence.CERTAIN, Confidence.LIKELY, Confidence.POSSIBLE]
HTTP_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
MAX_DEPTH = 25


def weakest(*confs: Confidence) -> Confidence:
    return max(confs, key=CONF_ORDER.index)


@dataclass
class Outbound:
    repo: str
    method: str
    protocol: str
    client: str
    operation: str
    url: str | None
    host: str | None
    system: str
    category: str
    via: str | None
    reason: str | None
    line: int
    confidence: Confidence
    target_repo: str | None = None
    target_endpoint: str | None = None

    def label(self) -> str:
        via = f" via {self.via}" if self.via else ""
        return f"{self.system}{via} · {self.protocol} {self.operation}"


@dataclass
class Hop:
    repo: str
    symbol: str
    name: str
    layer: str
    dispatch: bool = False        # reached through interface -> implementation
    outbound: Outbound | None = None  # set on a cross-repo hop


@dataclass
class Journey:
    entry_repo: str
    entry: str
    hops: list[Hop]
    outbound: Outbound
    confidence: Confidence

    def repos(self) -> list[str]:
        seen: list[str] = []
        for h in self.hops:
            if h.repo not in seen:
                seen.append(h.repo)
        return seen


@dataclass
class Repo:
    name: str
    path: Path
    service: str
    graph: CodeGraph
    config: dict[str, str]
    outbound: dict[str, list[Outbound]] = field(default_factory=dict)  # method id -> calls


def find_repos(root: Path) -> list[Path]:
    root = root.resolve()
    if any((root / m).exists() for m in BUILD_MARKERS):
        return [root]
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
                  and any((p / m).exists() for m in BUILD_MARKERS))


def short(symbol: str) -> str:
    q = symbol.split(":", 1)[-1]
    owner, _, member = q.partition("#")
    return f"{owner.rpartition('.')[2]}.{member}" if member else owner.rpartition(".")[2]


def http_verb(operation: str) -> str | None:
    for op in operation.split("."):
        low = op.lower()
        for prefix, verb in HTTP_VERBS.items():
            if low == prefix or low.startswith(prefix + "for"):
                return verb
    return None


def path_regex(path: str) -> re.Pattern:
    parts = [re.escape(p) if not (p.startswith("{") and p.endswith("}")) else "[^/]+"
             for p in path.strip("/").split("/")]
    return re.compile("^/" + "/".join(parts) + "/?$")


class Workspace:
    def __init__(self, root: Path, catalog: Catalog):
        self.root = root
        self.catalog = catalog
        self.repos: dict[str, Repo] = {}
        for path in find_repos(root):
            config = load_repo_config(path)
            graph = analyze(path)
            service = config.get("spring.application.name", path.name)
            self.repos[path.name] = Repo(path.name, path, service, graph, config)
        self.by_service = {r.service: r for r in self.repos.values()}
        for repo in self.repos.values():
            self._resolve_outbound(repo)

    # -- outbound classification ----------------------------------------
    def _resolve_outbound(self, repo: Repo) -> None:
        for sym in repo.graph.symbols.values():
            for rec in sym.attrs.get("outbound", []):
                repo.outbound.setdefault(sym.id, []).append(self._classify(repo, sym.id, rec))

    def _classify(self, repo: Repo, method: str, rec: dict) -> Outbound:
        url, keys = (resolve_placeholders(rec["target"], repo.config) if rec.get("target")
                     else (None, []))
        props = keys + [p for p in rec.get("class_properties", []) if p not in keys]
        if url is None:  # socket clients: look for host/port properties on the class
            host_key = next((p for p in props if p.endswith(".host")), None)
            if host_key and host_key in repo.config:
                port_key = host_key[:-5] + ".port"
                url = f"tcp://{repo.config[host_key]}:{repo.config.get(port_key, '?')}"
        host = urlparse(url).hostname if url and "://" in url and "${" not in url.split("/")[2] else None
        confidence = Confidence.CERTAIN if keys or (url and "${" not in url) else Confidence.LIKELY
        operation = rec["operation"]
        if rec["protocol"] == "HTTP" and rec.get("client") != "Feign":
            verb = http_verb(operation)
            path = urlparse(url).path if url and "://" in url else ""
            operation = f"{verb or operation} {path or ''}".strip()

        out = Outbound(repo.name, method, rec["protocol"], rec["client"], operation, url, host,
                       "unclassified", "unclassified", None, None, rec.get("line", 0), confidence)

        target = self._internal_repo(host, rec.get("service"))
        if target is not None and target is not repo:
            out.system, out.category, out.reason = target.service, "internal-service", "workspace repo"
            out.target_repo = target.name
            out.target_endpoint = self._match_endpoint(target, url, rec)
            return out
        texts = [t for t in [url, operation, *rec.get("evidence", [])] if t]
        dest, via, reason = self.catalog.classify(host, props, texts)
        if dest is not None:
            out.system, out.category, out.reason = dest.name, dest.category, reason
            out.via = via.name if via else None
        else:
            out.confidence = Confidence.POSSIBLE
        return out

    def _internal_repo(self, host: str | None, service: str | None) -> Repo | None:
        for name in filter(None, [service, host.split(".")[0] if host else None]):
            if name in self.by_service:
                return self.by_service[name]
        return None

    def _match_endpoint(self, target: Repo, url: str | None, rec: dict) -> str | None:
        path = rec.get("http", {}).get("path") or (urlparse(url).path if url and "://" in url else None)
        verb = rec.get("http", {}).get("method") or http_verb(rec["operation"])
        if not path:
            return None
        concrete = re.sub(r"\{[^/}]+\}", "x", path)
        for ep in target.graph.of_kind(NodeKind.ENDPOINT):
            ep_verb, ep_path = ep.attrs.get("method"), ep.attrs.get("path", "")
            if verb and ep_verb not in (verb, "ANY"):
                continue
            if path_regex(ep_path).match(concrete) or ep_path == path:
                return ep.id
        return None

    # -- journeys --------------------------------------------------------
    def layer(self, repo: Repo, symbol: str) -> str:
        owner = repo.graph.owner_type(symbol) or symbol
        sym = repo.graph.symbols.get(owner)
        qualified = owner.split(":", 1)[-1]
        return self.catalog.layer_of(qualified, sym.attrs.get("stereotype") if sym else None)

    def entries(self) -> list[tuple[Repo, str, str]]:
        """(repo, endpoint id, handler) for every entry point in every repo."""
        out = []
        for repo in self.repos.values():
            for ep in repo.graph.of_kind(NodeKind.ENDPOINT):
                for handler in repo.graph.handlers(ep.id):
                    out.append((repo, ep.id, handler))
        return out

    def journeys(self) -> list[Journey]:
        found: dict[tuple, Journey] = {}
        for repo, ep, handler in self.entries():
            self._walk(repo, handler, [], Confidence.CERTAIN, (repo.name, ep), found, set())
        return sorted(found.values(), key=lambda j: (j.entry_repo, j.entry, j.outbound.system,
                                                     [h.symbol for h in j.hops]))

    def _walk(self, repo: Repo, node: str, path: list[Hop], conf: Confidence,
              entry: tuple[str, str], found: dict, on_path: set, dispatch: bool = False) -> None:
        if (repo.name, node) in on_path or len(path) > MAX_DEPTH:
            return
        on_path = on_path | {(repo.name, node)}
        hops = path + [Hop(repo.name, node, short(node), self.layer(repo, node), dispatch)]
        for out in repo.outbound.get(node, []):
            target_handlers = []
            if out.target_repo and out.target_endpoint:
                target = self.repos[out.target_repo]
                target_handlers = target.graph.handlers(out.target_endpoint)
            if target_handlers:
                bridge = hops[:-1] + [replace(hops[-1], outbound=out)]
                for h in target_handlers:
                    self._walk(self.repos[out.target_repo], h, bridge, weakest(conf, out.confidence),
                               entry, found, on_path)
            else:
                j = Journey(entry[0], entry[1], hops, out, weakest(conf, out.confidence))
                found.setdefault((entry, tuple((h.repo, h.symbol) for h in hops), out.operation), j)
        for e in repo.graph.out_edges(node, {EdgeKind.CALLS}):
            dst = e.dst
            if dst and dst in repo.graph.symbols and repo.graph.symbols[dst].kind in (
                    NodeKind.METHOD, NodeKind.FUNCTION):
                self._walk(repo, dst, hops, weakest(conf, e.confidence), entry, found, on_path,
                           dispatch=bool(e.attrs.get("dispatch")))

    def unreached_outbound(self, journeys: list[Journey]) -> list[Outbound]:
        """Outbound calls no entry point leads to (scheduled jobs, dead code, missed links)."""
        reached = {(j.outbound.repo, j.outbound.method, j.outbound.operation) for j in journeys}
        reached |= {(h.outbound.repo, h.outbound.method, h.outbound.operation)
                    for j in journeys for h in j.hops if h.outbound}
        return [o for r in self.repos.values() for calls in r.outbound.values() for o in calls
                if (o.repo, o.method, o.operation) not in reached]
