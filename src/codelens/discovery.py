"""Find source files to analyse, defensively.

Never follows symlinks, skips oversized and binary files, and never leaves
the repository root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", ".venv", "venv",
    "node_modules", "build", "target", "dist", "out", ".gradle", ".mvn",
    ".tox", ".mypy_cache", ".pytest_cache",
})
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024


@dataclass
class DiscoveryConfig:
    extensions: frozenset[str]
    ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES


@dataclass
class DiscoveredFile:
    path: Path      # absolute
    rel_path: str   # POSIX, relative to the root
    extension: str


@dataclass
class DiscoveryResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (rel_path, reason)


def discover(root: Path, config: DiscoveryConfig) -> DiscoveryResult:
    root = root.resolve()
    result = DiscoveryResult()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in config.ignored_dirs and not (current / d).is_symlink()
        )
        for name in sorted(filenames):
            path = current / name
            ext = path.suffix.lower()
            if ext not in config.extensions:
                continue
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                result.skipped.append((rel, "symlink"))
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                result.skipped.append((rel, f"stat failed: {exc}"))
                continue
            if size > config.max_file_bytes:
                result.skipped.append((rel, f"too large ({size} bytes)"))
                continue
            result.files.append(DiscoveredFile(path=path, rel_path=rel, extension=ext))
    return result


def read_source(file: DiscoveredFile) -> bytes | None:
    """Read raw bytes; None for files that look binary."""
    data = file.path.read_bytes()
    if b"\x00" in data[:8192]:
        return None
    return data
