"""Golden tests: each .scm query against a snippet, so grammar upgrades that
rename nodes fail loudly here instead of silently returning nothing."""

import pytest
from tree_sitter import Parser

from codelens.frontends.java import QUERY_DIR, load_queries, text

SNIPPET = b"""
package com.acme;
import java.util.List;
import static java.util.Objects.requireNonNull;
import org.example.*;

@RestController
public class A extends Base implements I1, I2 {
    private final List<String> names;
    record R(int x, String y) {}
    interface J extends K {}
    enum E { X }
    A(Svc svc) { this.names = null; }
    @GetMapping("/a") public void run(Req req) {
        for (String s : names) { svc.go(s); }
        Helper h = new Helper();
        h.help();
    }
}
"""

EXPECTED = {
    "package": {"package.name": ["com.acme"]},
    "imports": {"import.name": ["java.util.List", "java.util.Objects.requireNonNull", "org.example"],
                "import.wildcard": ["*"]},
    "types": {"type.name": ["A", "R", "J", "E"]},
    "heritage": {"extends": ["Base", "K"], "implements": ["I1", "I2"]},
    "methods": {"method.name": ["A", "run"]},
    "fields": {"field.name": ["names", "x", "y"]},
    # Record components are formal_parameters too; the frontend drops them by scope.
    "locals": {"var.name": ["svc", "req", "s", "h", "x", "y"]},
    "calls": {"call.name": ["go", "help"], "new.type": ["Helper"]},
    "annotations": {"annotation.name": ["RestController", "GetMapping"]},
}


@pytest.fixture(scope="module")
def tree():
    language, _ = load_queries()
    return Parser(language).parse(SNIPPET)


def test_every_query_file_has_a_golden_case():
    assert {p.stem for p in QUERY_DIR.glob("*.scm")} == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_query_captures(name, tree):
    _, queries = load_queries()
    captured: dict[str, list[str]] = {}
    for _, caps in queries[name].matches(tree.root_node):
        for cap, nodes in caps.items():
            if cap in EXPECTED[name]:
                captured.setdefault(cap, []).extend(text(n) for n in nodes)
    for cap, expected in EXPECTED[name].items():
        assert sorted(set(captured.get(cap, []))) == sorted(expected), cap
