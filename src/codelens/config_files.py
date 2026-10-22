"""Read Spring Boot config (application*.yml / *.properties) as flat keys.

Only the subset that matters for finding endpoints is supported: nested
`key: value` maps, `---` documents and `.properties` lines. Lists and
anchors are skipped. Secret-looking values are masked at read time, so
they never reach the graph or the reports.
"""

from __future__ import annotations

import re
from pathlib import Path

from codelens.discovery import DEFAULT_IGNORED_DIRS

SECRET_KEY = re.compile(r"(password|passwd|pwd|secret|token|api[-_.]?key|license[-_.]?key|"
                        r"private[-_.]?key|credential|\bpin\b)", re.IGNORECASE)
URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
MASK = "***"
CONFIG_NAMES = re.compile(r"^(application|bootstrap)(-[\w.-]+)?\.(ya?ml|properties)$")


def mask(key: str, value: str) -> str:
    if SECRET_KEY.search(key):
        return MASK
    return URL_USERINFO.sub(lambda m: m.group("scheme") + MASK + "@", value)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_yaml(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    stack: list[tuple[int, str]] = []  # (indent, key)
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            stack = []
            continue
        if stripped.startswith("- ") or ":" not in stripped:
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = stripped.partition(":")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = ".".join([k for _, k in stack] + [key.strip()])
        value = _unquote(value)
        if value and value not in ("|", ">"):
            out[full] = mask(full, value)
        else:
            stack.append((indent, key.strip()))
    return out


def parse_properties(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        sep = min((i for i in (line.find("="), line.find(":")) if i > 0), default=-1)
        if sep < 0:
            continue
        key, value = line[:sep].strip(), _unquote(line[sep + 1:])
        out[key] = mask(key, value)
    return out


def load_repo_config(repo: Path) -> dict[str, str]:
    """Merge every application*/bootstrap* config file in a repo.

    Base files load before profile files (application-uat.yml), so profile
    values win, roughly like Spring with every profile active.
    """
    files = []
    for path in repo.rglob("*"):
        if any(part in DEFAULT_IGNORED_DIRS for part in path.relative_to(repo).parts):
            continue
        if path.is_file() and not path.is_symlink() and CONFIG_NAMES.match(path.name):
            files.append(path)
    files.sort(key=lambda p: ("-" in p.stem, str(p)))
    config: dict[str, str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        config.update(parse_properties(text) if path.suffix == ".properties" else parse_yaml(text))
    return config


def resolve_placeholders(template: str, config: dict[str, str]) -> tuple[str, list[str]]:
    """'${a.url}/x' -> ('https://…/x', ['a.url']). Unknown keys stay as ${key}."""
    keys: list[str] = []

    def sub(m: re.Match) -> str:
        key, default = m.group(1).strip(), m.group(2)
        keys.append(key)
        if key in config:
            return config[key]
        return default if default is not None else m.group(0)

    return PLACEHOLDER.sub(sub, template), keys
