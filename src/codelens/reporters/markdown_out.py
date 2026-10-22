"""Human-readable architecture report."""

from __future__ import annotations

from codelens.graph import CodeGraph
from codelens.model import EdgeKind, NodeKind


def _short(sid: str) -> str:
    return sid.split(":", 1)[-1]


def render_markdown(graph: CodeGraph, max_flow_nodes: int = 15) -> str:
    out: list[str] = ["# codelens report", ""]
    stats = graph.summary()
    out.append(f"Root: `{stats.get('root', '?')}` · files analysed: {stats.get('files_analyzed', 0)}")
    out.append("")
    out.append("| Node kind | Count |")
    out.append("|---|---|")
    for kind, n in sorted(stats["nodes"].items()):
        out.append(f"| {kind} | {n} |")
    out.append("")

    # Architecture: modules and the types they hold, with stereotypes.
    out += ["## Modules", ""]
    for mod in graph.of_kind(NodeKind.MODULE):
        types = [s for s in graph.symbols.values()
                 if s.kind in (NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM, NodeKind.RECORD)
                 and graph.module_of(s.id) == mod.id]
        labels = ", ".join(
            f"{t.name}" + (f" ({t.attrs['stereotype']})" if t.attrs.get("stereotype") else "")
            for t in sorted(types, key=lambda t: t.name))
        out.append(f"- **{mod.name}** ({mod.language}): {labels or '—'}")
    out.append("")

    # Dependencies.
    deps = graph.module_dependencies()
    out += ["## Module dependencies", ""]
    internal = [(u, v, d) for u, v, d in deps.edges(data=True) if not deps.nodes[v].get("external")]
    external = sorted({v for _, v, _ in deps.edges(data=True)
                       if deps.nodes[v].get("external") and not deps.nodes[v].get("stdlib")})
    for u, v, d in sorted(internal):
        out.append(f"- {_short(u)} → {_short(v)} ({d['weight']})")
    if not internal:
        out.append("- (no internal dependencies)")
    out.append("")
    if external:
        out.append("Third-party: " + ", ".join(f"`{x.split(':', 2)[-1]}`" for x in external))
        out.append("")
    cycles = graph.cycles()
    out += ["### Cycles", ""]
    out += [f"- {' ↔ '.join(_short(m) for m in c)}" for c in cycles] or ["- none"]
    out.append("")

    # External integrations.
    out += ["## External integrations", ""]
    services = graph.of_kind(NodeKind.EXTERNAL_SERVICE)
    for svc in services:
        users = sorted({e.src for e in graph.edges if e.kind == EdgeKind.INTEGRATES and e.dst == svc.id})
        # A module that imports the client is implied by its finer-grained users.
        users = [u for u in users if not (graph.symbols[u].kind == NodeKind.MODULE and any(
            v != u and graph.module_of(v) == u for v in users))]
        out.append(f"- **{svc.name}** ({', '.join(svc.attrs.get('labels', []))}): "
                   + ", ".join(f"`{_short(u)}`" for u in users))
    if not services:
        out.append("- none detected")
    out.append("")

    # Entry points and the flows behind them.
    out += ["## Entry points and flows", ""]
    endpoints = graph.of_kind(NodeKind.ENDPOINT)
    if not endpoints:
        out.append("- none detected")
    for ep in endpoints:
        for handler in graph.handlers(ep.id):
            flow = graph.trace(handler)
            out.append(f"### `{ep.name}` → `{_short(handler)}`")
            out.append("")
            for sid, depth in flow.tree()[:max_flow_nodes]:
                out.append(f"{'  ' * depth}- `{_short(sid)}`")
            if len(flow.reached) > max_flow_nodes:
                out.append(f"- … {len(flow.reached) - max_flow_nodes} more")
            if flow.services:
                out.append("")
                out.append("Touches: " + ", ".join(
                    f"**{graph.symbols[s].name}** (via {', '.join(f'`{_short(v)}`' for v in sorted(set(vs)))})"
                    for s, vs in sorted(flow.services.items())))
            out.append("")

    if graph.errors or graph.skipped:
        out += ["## Problems", ""]
        out += [f"- {e}" for e in graph.errors]
        out += [f"- skipped `{p}`: {r}" for p, r in graph.skipped]
        out.append("")
    unresolved = stats.get("unresolved_by_kind") or {}
    if unresolved:
        out.append("Unresolved references (dropped): " +
                   ", ".join(f"{k}={v}" for k, v in sorted(unresolved.items())))
        out.append("")
    return "\n".join(out)
