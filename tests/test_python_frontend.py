from codelens.frontends.python_ast import PythonFrontend, module_name_for
from codelens.model import Confidence, EdgeKind, NodeKind


def edge(graph, src, kind, dst):
    return next((e for e in graph.edges if e.src == src and e.kind == kind and e.dst == dst), None)


def test_module_names():
    assert module_name_for("src/pkg/mod.py") == ("pkg.mod", False)
    assert module_name_for("pkg/__init__.py") == ("pkg", True)


def test_endpoints(py_graph):
    names = {s.name for s in py_graph.of_kind(NodeKind.ENDPOINT)}
    assert names == {"GET /items/{item_id}", "POST /items", "PUT /items"}
    assert py_graph.handlers("endpoint:POST /items") == ["py:app.api.write_item"]


def test_calls_and_relative_imports(py_graph):
    g = py_graph
    assert edge(g, "py:app.api.read_item", EdgeKind.CALLS, "py:app.service.load")
    assert edge(g, "py:app.api.write_item", EdgeKind.INSTANTIATES, "py:app.models.Item")
    assert edge(g, "py:app.service.Cache.get", EdgeKind.CALLS, "py:app.service.Cache._lookup")
    assert edge(g, "py:app.api", EdgeKind.IMPORTS, "py:app.service").confidence == Confidence.CERTAIN


def test_integrations(py_graph):
    g = py_graph
    assert edge(g, "py:app.service.store", EdgeKind.INTEGRATES, "service:http-client")
    assert edge(g, "py:app.service._db", EdgeKind.INTEGRATES, "service:database")
    flow = g.trace("py:app.api.write_item")
    assert set(flow.services) == {"service:http-client", "service:database"}


def test_external_modules(py_graph):
    ext = {s.name: s for s in py_graph.of_kind(NodeKind.EXTERNAL_MODULE)}
    assert {"fastapi", "requests", "sqlite3", "dataclasses"} <= set(ext)
    assert ext["sqlite3"].attrs["stdlib"] and not ext["fastapi"].attrs["stdlib"]


def test_syntax_error_is_reported(py_graph):
    assert any("broken.py" in e for e in py_graph.errors)


def test_parse_never_executes_code(tmp_path):
    marker = tmp_path / "pwned"
    src = f"open({str(marker)!r}, 'w').write('x')\nimport os\nos.system('touch {marker}')\n"
    PythonFrontend().parse(src.encode(), "evil.py")
    assert not marker.exists()
