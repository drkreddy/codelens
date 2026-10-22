"""Reports for API journeys across a workspace."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict

from codelens.journeys import Journey, Workspace

CATEGORY_TITLES = {
    "internal-core": "Internal core",
    "internal-service": "Internal service",
    "middleware": "Middleware",
    "third-party": "Third-party",
    "unclassified": "Unclassified",
}


def _journey_lines(j: Journey) -> list[str]:
    lines = [f"{j.entry.removeprefix('endpoint:')}   [{j.entry_repo}]"]
    for h in j.hops:
        arrow = "⇒ " if h.dispatch else ""
        lines.append(f"  {h.repo:<20} {h.layer:<15} {arrow}{h.name}")
        if h.outbound:
            lines.append(f"  {'':<20} {'':<15} ── {h.outbound.protocol} {h.outbound.operation} ──▶")
    o = j.outbound
    lines.append(f"  → {o.label()}")
    if o.url:
        lines.append(f"    {o.url}")
    return lines


def render_journeys_markdown(ws: Workspace, journeys: list[Journey]) -> str:
    out = ["# API journeys", ""]
    out += ["| Repo | Service | Files | Entry points | Outbound calls |", "|---|---|---|---|---|"]
    for r in ws.repos.values():
        n_out = sum(len(v) for v in r.outbound.values())
        n_entry = len([1 for e in ws.entries() if e[0] is r])
        out.append(f"| {r.name} | {r.service} | {r.graph.stats.get('files_analyzed', 0)} | {n_entry} | {n_out} |")
    out.append("")

    # Systems summary.
    by_system: dict[str, list[Journey]] = defaultdict(list)
    for j in journeys:
        by_system[j.outbound.system].append(j)
    out += ["## Systems reached", "",
            "| System | Category | Protocol · operation | Journeys | Called by |", "|---|---|---|---|---|"]
    order = list(CATEGORY_TITLES)
    for system, js in sorted(by_system.items(), key=lambda kv: (order.index(kv[1][0].outbound.category), kv[0])):
        o = js[0].outbound
        ops = sorted({f"{j.outbound.protocol} {j.outbound.operation}" + (f" (via {j.outbound.via})" if j.outbound.via else "")
                      for j in js})
        callers = sorted({j.hops[-1].name for j in js})
        out.append(f"| **{system}** | {CATEGORY_TITLES[o.category]} | {'<br>'.join(ops)} | {len(js)} | "
                   f"{', '.join(f'`{c}`' for c in callers)} |")
    out.append("")

    # Domain view.
    out += ["## What the domain layer depends on", ""]
    domain: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for j in journeys:
        for i, h in enumerate(j.hops):
            if h.layer != "domain":
                continue
            owner = h.name.split(".")[0]
            domain[(h.repo, owner)][j.outbound.category].add(j.outbound.system)
            # The first hop into another repo after this one, within this repo's segment.
            bridge = next((b.outbound for b in j.hops[i:] if b.outbound and b.repo == h.repo), None)
            if bridge:
                domain[(h.repo, owner)][bridge.category].add(bridge.system)
    out += ["| Domain class | Internal core | Internal services | Third-party |", "|---|---|---|---|"]
    for (repo, owner), cats in sorted(domain.items()):
        def cell(c: str) -> str:
            return ", ".join(sorted(cats.get(c, set()))) or "—"
        out.append(f"| `{owner}` ({repo}) | {cell('internal-core')} | {cell('internal-service')} | {cell('third-party')} |")
    out.append("")

    # Journeys.
    out += ["## Journeys", ""]
    for system, js in sorted(by_system.items()):
        out += [f"### {system}", ""]
        for j in js:
            out += ["```", *_journey_lines(j), "```",
                    f"Confidence: {j.confidence.value}" + (f" · matched by {j.outbound.reason}" if j.outbound.reason else ""),
                    ""]

    unclassified = [o for r in ws.repos.values() for calls in r.outbound.values() for o in calls
                    if o.category == "unclassified"]
    if unclassified:
        out += ["## Needs a label in systems.toml", ""]
        out += [f"- `{o.repo}` {o.method.split(':', 1)[-1]}: {o.protocol} {o.operation} → "
                f"{o.url or 'target unknown'}" for o in unclassified]
        out.append("")
    unreached = ws.unreached_outbound(journeys)
    if unreached:
        out += ["## Outbound calls with no entry point", "",
                "Reached by nothing codelens recognises as an entry point (schedulers, batch jobs, dead code).", ""]
        out += [f"- `{o.repo}` {o.method.split(':', 1)[-1]} → {o.label()}" for o in unreached]
        out.append("")
    out += ["Config values that look like secrets are masked when read and never appear in this report.", ""]
    return "\n".join(out)


def render_journeys_json(ws: Workspace, journeys: list[Journey]) -> str:
    def enc(o):
        return o.value if hasattr(o, "value") else str(o)
    return json.dumps({
        "repos": [{"name": r.name, "service": r.service,
                   "files": r.graph.stats.get("files_analyzed", 0)} for r in ws.repos.values()],
        "journeys": [asdict(j) for j in journeys],
        "outbound": [asdict(o) for r in ws.repos.values() for calls in r.outbound.values() for o in calls],
    }, indent=2, default=enc)
