from __future__ import annotations

from abc import ABC, abstractmethod

from codelens.model import FileFacts


class Frontend(ABC):
    """Turns the bytes of one source file into FileFacts.

    Implementations must only parse. They must never import, compile-and-run,
    or otherwise execute the code they are given, and must not touch the network.
    """

    language: str
    extensions: frozenset[str]
    integrations: dict[str, tuple[str, str]]

    @abstractmethod
    def parse(self, source: bytes, rel_path: str) -> FileFacts: ...
