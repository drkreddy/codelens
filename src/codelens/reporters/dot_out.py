"""Module dependency graph as Graphviz DOT."""

from __future__ import annotations

from codelens.graph import CodeGraph


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_dot(graph: CodeGraph, include_stdlib: bool = False) -> str:
    deps = graph.module_dependencies()
    lines = ["digraph modules {", "  rankdir=LR;", '  node [shape=box, fontname="Helvetica"];']
    hidden = {n for n, d in deps.nodes(data=True) if d.get("stdlib") and not include_stdlib}
    for node, data in sorted(deps.nodes(data=True)):
        if node in hidden:
            continue
        external = data.get("external")
        label = node.split(":", 2)[-1] if external else node.split(":", 1)[-1]
        style = ", style=dashed, color=gray" if external else ""
        lines.append(f"  {_q(node)} [label={_q(label)}{style}];")
    for u, v, data in sorted(deps.edges(data=True)):
        if u in hidden or v in hidden:
            continue
        lines.append(f"  {_q(u)} -> {_q(v)} [label={data['weight']}];")
    lines.append("}")
    return "\n".join(lines) + "\n"
