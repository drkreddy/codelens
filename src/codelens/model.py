"""Language-neutral intermediate representation.

Frontends turn one source file into a FileFacts. Symbol ids are qualified
names prefixed with the language ("java:com.acme.Foo#bar", "py:pkg.mod.func").
An edge whose target could not be pinned down at parse time carries
`candidates`: qualified names the linker tries in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Confidence(str, Enum):
    CERTAIN = "certain"    # explicit in the syntax (import, extends, annotation)
    LIKELY = "likely"      # resolved through a declared type or the enclosing scope
    POSSIBLE = "possible"  # name match only; static typing information was missing


class NodeKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    FUNCTION = "function"
    METHOD = "method"
    ENDPOINT = "endpoint"
    EXTERNAL_MODULE = "external_module"
    EXTERNAL_SERVICE = "external_service"


class EdgeKind(str, Enum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CALLS = "calls"
    INSTANTIATES = "instantiates"
    USES_TYPE = "uses_type"
    EXPOSES = "exposes"          # code -> endpoint it serves
    INTEGRATES = "integrates"    # code -> external service it talks to


@dataclass(frozen=True)
class Location:
    path: str
    line: int
    column: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "column": self.column}


@dataclass
class Symbol:
    id: str
    kind: NodeKind
    name: str
    language: str
    location: Location | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "language": self.language,
            "location": self.location.to_dict() if self.location else None,
            "attrs": self.attrs,
        }


@dataclass
class Edge:
    src: str
    kind: EdgeKind
    confidence: Confidence
    dst: str | None = None
    candidates: list[str] = field(default_factory=list)
    location: Location | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.dst is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind.value,
            "confidence": self.confidence.value,
            "location": self.location.to_dict() if self.location else None,
            "attrs": self.attrs,
        }


@dataclass
class FileFacts:
    path: str
    language: str
    module_id: str
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
