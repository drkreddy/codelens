from __future__ import annotations

import json

from codelens.graph import CodeGraph


def render_json(graph: CodeGraph) -> str:
    return json.dumps(graph.to_dict(), indent=2, sort_keys=False, default=sorted)
