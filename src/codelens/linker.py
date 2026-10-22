"""Cross-file resolution: FileFacts from every frontend -> one CodeGraph.

For each unresolved edge the linker tries the frontend's candidates in order.
A miss on a member falls back to its owner (inherited or dynamic member,
downgraded to POSSIBLE). Whatever stays unresolved is checked against the
integration catalog, then either attached to an external-module node
(imports, supertypes) or dropped and counted.
"""

from __future__ import annotations

import sys
from collections import Counter

from codelens import catalog
from codelens.graph import CodeGraph
from codelens.model import Confidence, Edge, EdgeKind, FileFacts, NodeKind, Symbol

EXTERNAL_KINDS = {EdgeKind.IMPORTS, EdgeKind.INHERITS, EdgeKind.IMPLEMENTS}
CLASS_KINDS = {NodeKind.CLASS, NodeKind.RECORD, NodeKind.ENUM}


def _split(candidate: str) -> tuple[str, str]:
    lang, _, qualified = candidate.partition(":")
    return lang, qualified


def external_module_name(lang: str, qualified: str) -> str:
    qualified = qualified.split("#")[0]
    if lang == "py":
        return qualified.split(".")[0]
    parts = qualified.split(".")
    pkg = []
    for part in parts:
        if part[:1].isupper():
            break
        pkg.append(part)
    return ".".join(pkg) or qualified


def is_stdlib(lang: str, name: str) -> bool:
    if lang == "py":
        return name in sys.stdlib_module_names
    return name == "java" or name.startswith("java.")


class Linker:
    def __init__(self, integrations: dict[str, dict[str, tuple[str, str]]]):
        self.integrations = integrations  # language -> catalog
        self.symbols: dict[str, Symbol] = {}
        self.edges: dict[tuple[str, str, EdgeKind], Edge] = {}
        self.unresolved: Counter[str] = Counter()

    def link(self, all_facts: list[FileFacts]) -> CodeGraph:
        for facts in all_facts:
            for sym in facts.symbols:
                self._add_symbol(sym)
        for facts in all_facts:
            for edge in facts.edges:
                self._link_edge(edge)
        self._add_dispatch_edges()
        graph = CodeGraph(list(self.symbols.values()), list(self.edges.values()))
        graph.stats["unresolved_by_kind"] = dict(self.unresolved)
        graph.errors = [e for f in all_facts for e in f.errors]
        return graph

    # ------------------------------------------------------------------
    def _add_dispatch_edges(self) -> None:
        """Interface/superclass method -> overriding method, so traces cross DI seams.

        Spring code calls `PaymentGateway.pay()`; the bean behind it is whichever
        class implements it. With several implementations each gets an edge.
        """
        methods: dict[str, dict[str, str]] = {}
        for e in list(self.edges.values()):
            if e.kind == EdgeKind.CONTAINS and e.dst and self.symbols[e.dst].kind == NodeKind.METHOD:
                methods.setdefault(e.src, {})[self.symbols[e.dst].name] = e.dst
        for e in list(self.edges.values()):
            if e.kind not in (EdgeKind.IMPLEMENTS, EdgeKind.INHERITS) or not e.dst:
                continue
            impl, base = e.src, e.dst
            for name, base_method in methods.get(base, {}).items():
                impl_method = methods.get(impl, {}).get(name)
                if impl_method and name != "<init>":
                    self._add_edge(Edge(base_method, EdgeKind.CALLS, Confidence.LIKELY,
                                        dst=impl_method, location=e.location,
                                        attrs={"dispatch": True}))

    def _add_symbol(self, sym: Symbol) -> None:
        existing = self.symbols.get(sym.id)
        if existing is None:
            self.symbols[sym.id] = sym
        elif sym.kind == NodeKind.MODULE:
            for f in sym.attrs.get("files", []):
                if f not in existing.attrs.setdefault("files", []):
                    existing.attrs["files"].append(f)
        else:
            existing.attrs.setdefault("duplicates", []).append(
                sym.location.to_dict() if sym.location else None)

    def _add_edge(self, edge: Edge) -> None:
        k = (edge.src, edge.dst, edge.kind)
        if k in self.edges:
            self.edges[k].attrs["count"] = self.edges[k].attrs.get("count", 1) + 1
        else:
            self.edges[k] = edge

    def _resolve(self, candidate: str) -> tuple[str, str | None] | None:
        """Exact hit -> (id, None); fallback to owner -> (owner id, member)."""
        if candidate in self.symbols:
            return candidate, None
        if "#" in candidate:
            owner, _, member = candidate.partition("#")
            if owner in self.symbols:
                return owner, member
            return None
        lang, qualified = _split(candidate)
        parts = qualified.split(".")
        for i in range(len(parts) - 1, 0, -1):
            owner = f"{lang}:{'.'.join(parts[:i])}"
            # A module owner would mean "some name in that package/module", which is
            # how a wrong same-package guess for an imported type would sneak in.
            if owner in self.symbols and self.symbols[owner].kind != NodeKind.MODULE:
                return owner, ".".join(parts[i:])
        return None

    def _link_edge(self, edge: Edge) -> None:
        if edge.resolved:
            self._add_edge(edge)
            return
        # Exact hits on any candidate beat owner fallbacks on earlier ones.
        hits = [self._resolve(c) for c in edge.candidates]
        hit = next((h for h in hits if h and h[1] is None), None) or next(filter(None, hits), None)
        if hit:
            dst, member = hit
            edge.dst = dst
            if member:
                edge.attrs["member"] = member
                if edge.kind != EdgeKind.IMPORTS:
                    edge.confidence = Confidence.POSSIBLE
            if edge.kind == EdgeKind.CALLS and self.symbols[dst].kind in CLASS_KINDS and not member:
                edge.kind = EdgeKind.INSTANTIATES
            self._add_edge(edge)
        else:
            self._link_external(edge)

    def _link_external(self, edge: Edge) -> None:
        lang = edge.src.partition(":")[0]
        table = self.integrations.get(lang, {})
        for candidate in edge.candidates:
            qualified = _split(candidate)[1].split("#")[0]
            hit = catalog.match(table, qualified)
            if hit:
                service_kind, label = hit
                sid = f"service:{service_kind}"
                svc = self.symbols.setdefault(sid, Symbol(sid, NodeKind.EXTERNAL_SERVICE,
                                                          service_kind, "external", None,
                                                          {"labels": []}))
                if label not in svc.attrs["labels"]:
                    svc.attrs["labels"].append(label)
                self._add_edge(Edge(edge.src, EdgeKind.INTEGRATES, edge.confidence, dst=sid,
                                    location=edge.location, attrs={"via": qualified, "label": label}))
                break

        names = {external_module_name(*_split(c)) for c in edge.candidates}
        if edge.kind in EXTERNAL_KINDS and len(names) == 1:
            lang, qualified = _split(edge.candidates[0])
            name = names.pop()
            xid = f"ext:{lang}:{name}"
            self.symbols.setdefault(xid, Symbol(xid, NodeKind.EXTERNAL_MODULE, name, lang, None,
                                                {"stdlib": is_stdlib(lang, name)}))
            edge.dst = xid
            edge.attrs.setdefault("target", qualified)
            self._add_edge(edge)
        else:
            self.unresolved[edge.kind.value] += 1
