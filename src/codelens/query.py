"""Questions about a workspace, answered from the graph.

Every answer is plain data (dicts and lists) carrying file:line locations and
confidence, so whatever presents it (the MCP server, a test) can cite the code
instead of paraphrasing it. Nothing here calls a model or the network.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from codelens.config_files import MASK
from codelens.discovery import DiscoveryConfig, discover
from codelens.journeys import Journey, Outbound, Repo, Workspace, short
from codelens.model import EdgeKind, NodeKind, Symbol

CODE_KINDS = {NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM, NodeKind.RECORD,
              NodeKind.METHOD, NodeKind.FUNCTION}
SOURCE_EXTS = frozenset({".java", ".py", ".yml", ".yaml", ".properties", ".xml"})
# `password = "x"`, `apiKey: x`, `.header("Authorization", "Bearer …")` and the like.
SECRET_ASSIGN = re.compile(
    r"""(?ix)(password|passwd|pwd|secret|token|api[-_]?key|license[-_]?key|private[-_]?key|credential|authorization)
        (["']?\s*[:=,]\s*["']?)([^"'\s,;)]+)""")
MAX_SOURCE_LINES = 200


def mask_line(line: str) -> str:
    return SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + MASK, line)


@dataclass
class Found:
    repo: Repo
    symbol: Symbol


class QueryEngine:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.journeys: list[Journey] = ws.journeys()
        self._callers: dict[str, dict[str, list[tuple[str, object]]]] = {}
        for repo in ws.repos.values():
            rev: dict[str, list] = defaultdict(list)
            for e in repo.graph.edges:
                if e.kind in (EdgeKind.CALLS, EdgeKind.INSTANTIATES) and e.dst:
                    rev[e.dst].append((e.src, e))
            self._callers[repo.name] = rev

    # -- helpers ---------------------------------------------------------
    def _loc(self, repo: Repo, sym: Symbol) -> str | None:
        if not sym.location:
            return None
        return f"{repo.name}/{sym.location.path}:{sym.location.line}"

    def _sym(self, repo: Repo, sid: str) -> dict:
        sym = repo.graph.symbols.get(sid)
        out = {"repo": repo.name, "symbol": short(sid), "id": sid.split(":", 1)[-1],
               "layer": self.ws.layer(repo, sid)}
        if sym is not None:
            out["kind"] = sym.kind.value
            out["at"] = self._loc(repo, sym)
        return out

    def find(self, query: str) -> list[Found]:
        """Resolve 'CardService.block', 'CardService#block', 'block', or a qualified name."""
        q = query.strip().removeprefix("java:").removeprefix("py:").replace("#", ".")
        ql = q.lower()
        exact, suffix = [], []
        for repo in self.ws.repos.values():
            for sym in repo.graph.symbols.values():
                if sym.kind not in CODE_KINDS:
                    continue
                qualified = sym.id.split(":", 1)[-1].replace("#", ".")
                names = {qualified.lower(), short(sym.id).lower()}
                if ql in names:
                    exact.append(Found(repo, sym))
                elif qualified.lower().endswith("." + ql) or sym.name.lower() == ql:
                    suffix.append(Found(repo, sym))
        return exact or suffix

    def _one(self, query: str) -> tuple[Found | None, dict | None]:
        hits = self.find(query)
        if not hits:
            return None, {"error": f"No class or method matches {query!r}.",
                          "hint": "Use Class.method (e.g. CardService.block) or search_code to find it."}
        if len(hits) > 1:
            return None, {"error": f"{query!r} is ambiguous.",
                          "matches": [self._sym(h.repo, h.symbol.id) for h in hits[:20]],
                          "hint": "Ask again with one of these, qualified by class."}
        return hits[0], None

    def _journey(self, j: Journey) -> dict:
        steps = []
        for h in j.hops:
            step = {"repo": h.repo, "layer": h.layer, "call": h.name}
            if h.dispatch:
                step["via_interface"] = True
            sym = self.ws.repos[h.repo].graph.symbols.get(h.symbol)
            if sym is not None:
                step["at"] = self._loc(self.ws.repos[h.repo], sym)
            steps.append(step)
            if h.outbound:
                steps.append({"crosses_to_repo": h.outbound.target_repo,
                              "via": f"{h.outbound.protocol} {h.outbound.operation}",
                              "client": h.outbound.client})
        return {"entry": j.entry.removeprefix("endpoint:"), "entry_repo": j.entry_repo,
                "steps": steps, "reaches": self._outbound(j.outbound),
                "confidence": j.confidence.value}

    def _outbound(self, o: Outbound) -> dict:
        repo = self.ws.repos[o.repo]
        sym = repo.graph.symbols.get(o.method)
        at = f"{o.repo}/{sym.location.path}:{o.line}" if sym and sym.location else None
        out = {"system": o.system, "category": o.category, "protocol": o.protocol,
               "operation": o.operation, "client": o.client, "url": o.url,
               "called_from": short(o.method), "at": at, "matched_by": o.reason,
               "confidence": o.confidence.value}
        if o.via:
            out["via"] = o.via
        return out

    # -- tools -----------------------------------------------------------
    def overview(self) -> dict:
        systems: dict[str, dict] = {}
        for j in self.journeys:
            s = systems.setdefault(j.outbound.system, {"category": j.outbound.category, "journeys": 0})
            s["journeys"] += 1
        return {
            "repos": [{"repo": r.name, "service": r.service,
                       "files": r.graph.stats.get("files_analyzed", 0),
                       "entry_points": sorted(ep.name for ep in r.graph.of_kind(NodeKind.ENDPOINT)),
                       "outbound_calls": sum(len(v) for v in r.outbound.values())}
                      for r in self.ws.repos.values()],
            "systems": systems,
            "journeys": len(self.journeys),
            "note": "Confidence: certain = explicit in code/config; likely = resolved via declared types "
                    "or interface->implementation; possible = name-based guess.",
        }

    def find_journeys(self, system: str | None = None, entry: str | None = None,
                      repo: str | None = None, category: str | None = None, limit: int = 30) -> dict:
        def ok(j: Journey) -> bool:
            if system and system.lower() not in (j.outbound.system.lower() + " " + (j.outbound.via or "").lower()):
                return False
            if entry and entry.lower() not in j.entry.lower():
                return False
            if repo and repo not in j.repos():
                return False
            return not category or j.outbound.category == category
        hits = [j for j in self.journeys if ok(j)]
        return {"count": len(hits), "journeys": [self._journey(j) for j in hits[:limit]],
                "truncated": len(hits) > limit}

    def impact(self, system: str) -> dict:
        hits = [j for j in self.journeys
                if system.lower() in (j.outbound.system + " " + (j.outbound.via or "")).lower()
                or any(h.outbound and system.lower() in h.outbound.system.lower() for h in j.hops)]
        if not hits:
            known = sorted({j.outbound.system for j in self.journeys})
            return {"error": f"No journeys reach {system!r}.", "known_systems": known}
        entries = sorted({(j.entry_repo, j.entry.removeprefix("endpoint:")) for j in hits})
        domain = sorted({(h.repo, h.name.split(".")[0]) for j in hits for h in j.hops if h.layer == "domain"})
        calls = {(j.outbound.repo, j.outbound.method, j.outbound.operation): j.outbound for j in hits
                 if system.lower() in (j.outbound.system + " " + (j.outbound.via or "")).lower()}
        return {
            "system": system,
            "entry_points_affected": [{"repo": r, "entry": e} for r, e in entries],
            "domain_classes_involved": [{"repo": r, "class": c} for r, c in domain],
            "direct_calls": [self._outbound(o) for o in calls.values()],
            "journeys": len(hits),
        }

    def callers(self, symbol: str, depth: int = 3) -> dict:
        found, err = self._one(symbol)
        if err:
            return err
        repo, start = found.repo, found.symbol.id
        tree: list[dict] = []
        seen = {(repo.name, start)}
        frontier = [(repo, start, 0)]
        while frontier:
            r, sid, d = frontier.pop(0)
            if d >= depth:
                continue
            for src, e in self._callers[r.name].get(sid, []):
                if (r.name, src) in seen:
                    continue
                seen.add((r.name, src))
                tree.append({**self._sym(r, src), "calls": short(sid), "depth": d + 1,
                             "confidence": e.confidence.value,
                             **({"via_interface": True} if e.attrs.get("dispatch") else {})})
                frontier.append((r, src, d + 1))
            # Other repos calling this handler's endpoint over HTTP.
            for ep in (x.dst for x in r.graph.out_edges(sid, {EdgeKind.EXPOSES})):
                for other in self.ws.repos.values():
                    for calls in other.outbound.values():
                        for o in calls:
                            if o.target_repo == r.name and o.target_endpoint == ep and \
                                    (other.name, o.method) not in seen:
                                seen.add((other.name, o.method))
                                tree.append({**self._sym(other, o.method), "calls": ep.removeprefix("endpoint:"),
                                             "depth": d + 1, "across_repos": f"{o.protocol} {o.operation}",
                                             "confidence": o.confidence.value})
                                frontier.append((other, o.method, d + 1))
        entry_points = [x.dst.removeprefix("endpoint:") for x in repo.graph.out_edges(start, {EdgeKind.EXPOSES})]
        return {"symbol": self._sym(repo, start), "entry_point_for": entry_points, "callers": tree}

    def callees(self, symbol: str, depth: int = 2) -> dict:
        found, err = self._one(symbol)
        if err:
            return err
        repo, start = found.repo, found.symbol.id
        out, seen, frontier = [], {start}, [(start, 0)]
        while frontier:
            sid, d = frontier.pop(0)
            for o in repo.outbound.get(sid, []):
                out.append({"from": short(sid), "depth": d, "outbound": self._outbound(o)})
            if d >= depth:
                continue
            for e in repo.graph.out_edges(sid, {EdgeKind.CALLS, EdgeKind.INSTANTIATES}):
                if e.dst and e.dst in repo.graph.symbols and e.dst not in seen:
                    seen.add(e.dst)
                    out.append({"from": short(sid), "depth": d + 1, **self._sym(repo, e.dst),
                                "edge": e.kind.value, "confidence": e.confidence.value,
                                **({"member": e.attrs["member"]} if "member" in e.attrs else {}),
                                **({"via_interface": True} if e.attrs.get("dispatch") else {})})
                    frontier.append((e.dst, d + 1))
        return {"symbol": self._sym(repo, start), "callees": out}

    def domain_dependencies(self, class_name: str | None = None) -> dict:
        deps: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for j in self.journeys:
            for i, h in enumerate(j.hops):
                if h.layer != "domain":
                    continue
                key = (h.repo, h.name.split(".")[0])
                deps[key][j.outbound.category].add(j.outbound.system)
                bridge = next((b.outbound for b in j.hops[i:] if b.outbound and b.repo == h.repo), None)
                if bridge:
                    deps[key][bridge.category].add(bridge.system)
        rows = [{"repo": r, "class": c, **{k: sorted(v) for k, v in cats.items()}}
                for (r, c), cats in sorted(deps.items())
                if not class_name or class_name.lower() in c.lower()]
        return {"domain_classes": rows}

    def outbound_calls(self, repo: str | None = None, system: str | None = None,
                       category: str | None = None) -> dict:
        unreached = {(o.repo, o.method, o.operation) for o in self.ws.unreached_outbound(self.journeys)}
        rows = []
        for r in self.ws.repos.values():
            if repo and r.name != repo:
                continue
            for calls in r.outbound.values():
                for o in calls:
                    if system and system.lower() not in (o.system + " " + (o.via or "")).lower():
                        continue
                    if category and o.category != category:
                        continue
                    rows.append({**self._outbound(o), "reached_from_entry_point":
                                 (o.repo, o.method, o.operation) not in unreached})
        return {"count": len(rows), "outbound_calls": rows}

    def source(self, symbol: str) -> dict:
        found, err = self._one(symbol)
        if err:
            return err
        repo, sym = found.repo, found.symbol
        if not sym.location:
            return {"error": f"No source location for {symbol!r}."}
        path = (repo.path / sym.location.path).resolve()
        if repo.path.resolve() not in path.parents:
            return {"error": "Refusing to read outside the repo."}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = sym.location.line
        end = min(sym.attrs.get("end_line", start), start + MAX_SOURCE_LINES - 1)
        # Include annotations directly above the declaration.
        while start > 1 and lines[start - 2].strip().startswith("@"):
            start -= 1
        code = "\n".join(f"{n:>5}  {mask_line(lines[n - 1])}" for n in range(start, end + 1))
        return {"symbol": self._sym(repo, sym.id), "file": f"{repo.name}/{sym.location.path}",
                "lines": f"{start}-{end}", "code": code,
                "truncated": sym.attrs.get("end_line", start) > end}

    def search_code(self, text: str, repo: str | None = None, regex: bool = False, limit: int = 40) -> dict:
        try:
            pattern = re.compile(text if regex else re.escape(text), re.IGNORECASE)
        except re.error as exc:
            return {"error": f"Bad regex: {exc}"}
        hits = []
        for r in self.ws.repos.values():
            if repo and r.name != repo:
                continue
            spans: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
            for sym in r.graph.symbols.values():
                if sym.kind in (NodeKind.METHOD, NodeKind.FUNCTION) and sym.location:
                    spans[sym.location.path].append((sym.location.line, sym.attrs.get("end_line", sym.location.line), sym.id))
            for f in discover(r.path, DiscoveryConfig(extensions=SOURCE_EXTS)).files:
                text_lines = f.path.read_text(encoding="utf-8", errors="replace").splitlines()
                for n, line in enumerate(text_lines, 1):
                    if not pattern.search(line):
                        continue
                    inside = [s for s in spans.get(f.rel_path, []) if s[0] <= n <= s[1]]
                    hit = {"at": f"{r.name}/{f.rel_path}:{n}", "line": mask_line(line.strip())[:240]}
                    if inside:
                        sid = min(inside, key=lambda s: s[1] - s[0])[2]
                        hit["in"] = short(sid)
                        hit["layer"] = self.ws.layer(r, sid)
                    hits.append(hit)
                    if len(hits) >= limit:
                        return {"count": len(hits), "hits": hits, "truncated": True}
        return {"count": len(hits), "hits": hits, "truncated": False}
