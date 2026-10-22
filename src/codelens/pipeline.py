"""discover -> parse (per-language frontend) -> link -> CodeGraph."""

from __future__ import annotations

from pathlib import Path

from codelens.discovery import DiscoveryConfig, discover, read_source
from codelens.frontends import build_frontends
from codelens.graph import CodeGraph
from codelens.linker import Linker
from codelens.model import FileFacts


def analyze(root: Path, languages: set[str] | None = None,
            max_file_bytes: int | None = None) -> CodeGraph:
    frontends = build_frontends(languages)
    config = DiscoveryConfig(extensions=frozenset(frontends))
    if max_file_bytes is not None:
        config.max_file_bytes = max_file_bytes
    found = discover(root, config)

    all_facts: list[FileFacts] = []
    skipped = list(found.skipped)
    errors: list[str] = []
    for file in found.files:
        source = read_source(file)
        if source is None:
            skipped.append((file.rel_path, "binary content"))
            continue
        frontend = frontends[file.extension]
        try:
            all_facts.append(frontend.parse(source, file.rel_path))
        except Exception as exc:  # one bad file must not sink the run
            errors.append(f"{file.rel_path}: frontend crashed: {type(exc).__name__}: {exc}")

    linker = Linker({fe.language: fe.integrations for fe in frontends.values()})
    graph = linker.link(all_facts)
    graph.errors = errors + graph.errors
    graph.skipped = skipped
    graph.stats["files_analyzed"] = len(all_facts)
    graph.stats["root"] = str(root.resolve())
    return graph
