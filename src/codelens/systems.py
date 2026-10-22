"""System catalog: which named system (Finacle, NPCI, …) an outbound call reaches.

The catalog is a TOML file the user owns. Each system lists host globs,
config property globs and marker substrings (service codes, XML namespaces,
message names). Systems in the "middleware" category (ESB, API gateway) are
reported as the hop a call goes through, not as its destination.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

CATEGORIES = ("internal-core", "internal-service", "middleware", "third-party", "unclassified")

STEREOTYPE_LAYERS = {
    "RestController": "web", "Controller": "web",
    "Service": "domain",
    "Repository": "persistence", "Entity": "persistence",
    "Component": "infrastructure", "Configuration": "infrastructure",
    "FeignClient": "infrastructure",
}


@dataclass
class System:
    name: str
    category: str
    hosts: list[str] = field(default_factory=list)
    properties: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)

    def matches(self, host: str | None, props: list[str], texts: list[str]) -> str | None:
        """Why this system matches ("host", "property", "marker"), or None."""
        if host and any(fnmatch(host.lower(), g.lower()) for g in self.hosts):
            return "host"
        if any(fnmatch(p, g) for p in props for g in self.properties):
            return "property"
        if any(m in t for t in texts for m in self.markers):
            return "marker"
        return None


@dataclass
class Catalog:
    systems: list[System] = field(default_factory=list)
    layers: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None) -> "Catalog":
        if path is None:
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        systems = []
        for raw in data.get("system", []):
            category = raw.get("category", "third-party")
            if category not in CATEGORIES:
                raise ValueError(f"{path}: system {raw.get('name')!r} has unknown category {category!r}")
            systems.append(System(raw["name"], category, raw.get("hosts", []),
                                  raw.get("properties", []), raw.get("markers", [])))
        return cls(systems, data.get("layers", {}))

    def classify(self, host: str | None, props: list[str],
                 texts: list[str]) -> tuple[System | None, System | None, str | None]:
        """(destination, via-middleware, reason)."""
        hits = [(s, why) for s in self.systems if (why := s.matches(host, props, texts))]
        dest = next(((s, w) for s, w in hits if s.category != "middleware"), None)
        via = next((s for s, _ in hits if s.category == "middleware"), None)
        if dest:
            return dest[0], via, dest[1]
        if via:
            return via, None, next(w for s, w in hits if s is via)
        return None, None, None

    def layer_of(self, qualified_type: str, stereotype: str | None) -> str:
        for layer, globs in self.layers.items():
            if any(fnmatch(qualified_type, g) for g in globs):
                return layer
        return STEREOTYPE_LAYERS.get(stereotype or "", "other")
