"""The linked code graph and the analyses that run over it."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from codelens.model import Edge, EdgeKind, NodeKind, Symbol

FLOW_KINDS = {EdgeKind.CALLS, EdgeKind.INSTANTIATES}


@dataclass
class Flow:
    """What an entry point reaches through calls, and which services it touches."""
    entry: str
    reached: list[tuple[str, int]] = field(default_factory=list)  # (symbol id, depth), BFS order
    parent: dict[str, str] = field(default_factory=dict)          # first caller that reached it
    services: dict[str, list[str]] = field(default_factory=dict)  # service id -> via symbols

    def tree(self) -> list[tuple[str, int]]:
        """Reached nodes in depth-first order under their first caller."""
        children: dict[str, list[str]] = {}
        for node, _ in self.reached[1:]:
            children.setdefault(self.parent[node], []).append(node)
        out: list[tuple[str, int]] = []
        stack = [(self.entry, 0)]
        while stack:
            node, depth = stack.pop()
            out.append((node, depth))
            stack.extend((c, depth + 1) for c in reversed(children.get(node, [])))
        return out


class CodeGraph:
    def __init__(self, symbols: list[Symbol], edges: list[Edge]):
        self.symbols: dict[str, Symbol] = {s.id: s for s in symbols}
        self.edges = edges
        self.errors: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        self.stats: dict[str, Any] = {}
        self._parent: dict[str, str] = {}
        self._out: dict[str, list[Edge]] = {}
        for e in edges:
            self._out.setdefault(e.src, []).append(e)
            if e.kind == EdgeKind.CONTAINS and e.dst:
                self._parent[e.dst] = e.src

    # -- basic queries --------------------------------------------------
    def out_edges(self, sid: str, kinds: set[EdgeKind] | None = None) -> list[Edge]:
        return [e for e in self._out.get(sid, []) if kinds is None or e.kind in kinds]

    def of_kind(self, *kinds: NodeKind) -> list[Symbol]:
        return sorted((s for s in self.symbols.values() if s.kind in kinds), key=lambda s: s.id)

    def module_of(self, sid: str) -> str:
        node = sid
        seen = set()
        while node in self._parent and node not in seen:
            seen.add(node)
            if self.symbols.get(node) and self.symbols[node].kind == NodeKind.MODULE:
                return node
            node = self._parent[node]
        return node

    def owner_type(self, sid: str) -> str | None:
        node = self._parent.get(sid)
        while node and self.symbols.get(node) and self.symbols[node].kind in (
                NodeKind.METHOD, NodeKind.FUNCTION):
            node = self._parent.get(node)
        return node

    # -- architecture ---------------------------------------------------
    def module_dependencies(self, include_external: bool = True) -> nx.DiGraph:
        g = nx.DiGraph()
        for m in self.of_kind(NodeKind.MODULE):
            g.add_node(m.id, external=False)
        for e in self.edges:
            if e.kind in (EdgeKind.CONTAINS, EdgeKind.EXPOSES) or not e.dst:
                continue
            dst_sym = self.symbols.get(e.dst)
            if dst_sym is None or dst_sym.kind == NodeKind.EXTERNAL_SERVICE:
                continue
            src_mod, dst_mod = self.module_of(e.src), self.module_of(e.dst)
            external = dst_sym.kind == NodeKind.EXTERNAL_MODULE
            if src_mod == dst_mod or (external and not include_external):
                continue
            if external:
                g.add_node(dst_mod, external=True, stdlib=dst_sym.attrs.get("stdlib", False))
            if g.has_edge(src_mod, dst_mod):
                g[src_mod][dst_mod]["weight"] += 1
                g[src_mod][dst_mod]["kinds"].add(e.kind.value)
            else:
                g.add_edge(src_mod, dst_mod, weight=1, kinds={e.kind.value})
        return g

    def cycles(self) -> list[list[str]]:
        g = self.module_dependencies(include_external=False)
        return sorted((sorted(c) for c in nx.strongly_connected_components(g) if len(c) > 1),
                      key=lambda c: (-len(c), c))

    # -- flows ----------------------------------------------------------
    def handlers(self, endpoint_id: str) -> list[str]:
        return sorted(e.src for e in self.edges
                      if e.kind == EdgeKind.EXPOSES and e.dst == endpoint_id)

    def trace(self, entry: str, max_depth: int = 8) -> Flow:
        flow = Flow(entry)
        seen = {entry}
        queue = deque([(entry, 0)])
        while queue:
            node, depth = queue.popleft()
            flow.reached.append((node, depth))
            for e in self.out_edges(node, {EdgeKind.INTEGRATES}):
                flow.services.setdefault(e.dst, []).append(node)
            if depth >= max_depth:
                continue
            for e in self.out_edges(node, FLOW_KINDS):
                if e.dst and e.dst not in seen and e.dst in self.symbols:
                    seen.add(e.dst)
                    flow.parent[e.dst] = node
                    queue.append((e.dst, depth + 1))
        return flow

    # -- export ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        deps = self.module_dependencies()
        return {
            "stats": self.summary(),
            "nodes": [s.to_dict() for s in sorted(self.symbols.values(), key=lambda s: s.id)],
            "edges": [e.to_dict() for e in self.edges],
            "module_dependencies": [
                {"src": u, "dst": v, "weight": d["weight"], "kinds": sorted(d["kinds"])}
                for u, v, d in sorted(deps.edges(data=True))
            ],
            "cycles": self.cycles(),
            "errors": self.errors,
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
        }

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for s in self.symbols.values():
            kinds[s.kind.value] = kinds.get(s.kind.value, 0) + 1
        edge_kinds: dict[str, int] = {}
        for e in self.edges:
            edge_kinds[e.kind.value] = edge_kinds.get(e.kind.value, 0) + 1
        return {"nodes": kinds, "edges": edge_kinds, **self.stats}
