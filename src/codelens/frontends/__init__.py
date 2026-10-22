from __future__ import annotations

from codelens.frontends.base import Frontend
from codelens.frontends.java import JavaFrontend
from codelens.frontends.python_ast import PythonFrontend

ALL_FRONTENDS: tuple[type[Frontend], ...] = (PythonFrontend, JavaFrontend)


def build_frontends(languages: set[str] | None = None) -> dict[str, Frontend]:
    """Map file extension -> frontend instance, optionally limited to some languages."""
    by_ext: dict[str, Frontend] = {}
    for cls in ALL_FRONTENDS:
        if languages and cls.language not in languages:
            continue
        frontend = cls()
        for ext in frontend.extensions:
            by_ext[ext] = frontend
    return by_ext


__all__ = ["Frontend", "JavaFrontend", "PythonFrontend", "build_frontends", "ALL_FRONTENDS"]
