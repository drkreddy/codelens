from codelens.frontends.java import JavaFrontend
from codelens.model import Confidence, EdgeKind, NodeKind

P = "java:com.acme.shop."


def edge(graph, src, kind, dst):
    return next((e for e in graph.edges if e.src == src and e.kind == kind and e.dst == dst), None)


def test_types_modules_and_stereotypes(java_graph):
    g = java_graph
    assert {m.name for m in g.of_kind(NodeKind.MODULE)} == {
        "com.acme.shop.web", "com.acme.shop.service", "com.acme.shop.repo", "com.acme.shop.client"}
    assert g.symbols[P + "web.OrderController"].attrs["stereotype"] == "RestController"
    assert g.symbols[P + "service.Order"].kind == NodeKind.RECORD
    assert g.symbols[P + "repo.OrderRepository"].kind == NodeKind.INTERFACE
    nested = g.symbols[P + "service.OrderService.AuditLog"]
    assert g.owner_type(P + "service.OrderService.AuditLog#write") == nested.id
    assert g.module_of(nested.id) == "java:com.acme.shop.service"


def test_heritage_resolves_same_package_and_external(java_graph):
    g = java_graph
    svc = P + "service.OrderService"
    assert edge(g, svc, EdgeKind.INHERITS, P + "service.BaseService").confidence == Confidence.LIKELY
    assert edge(g, svc, EdgeKind.IMPLEMENTS, P + "service.Auditable")
    jpa = edge(g, P + "repo.OrderRepository", EdgeKind.INHERITS,
               "ext:java:org.springframework.data.jpa.repository")
    assert jpa.confidence == Confidence.CERTAIN


def test_constructor_injection_and_fields(java_graph):
    g = java_graph
    assert edge(g, P + "web.OrderController", EdgeKind.USES_TYPE, P + "service.OrderService")
    assert edge(g, P + "service.OrderService", EdgeKind.USES_TYPE, P + "client.PaymentClient")


def test_calls_through_fields_this_and_inherited_members(java_graph):
    g = java_graph
    e = edge(g, P + "web.OrderController#get", EdgeKind.CALLS, P + "service.OrderService#find")
    assert e.confidence == Confidence.LIKELY
    assert edge(g, P + "web.OrderController#legacy", EdgeKind.CALLS, P + "web.OrderController#helper")
    # findById is inherited from JpaRepository: falls back to the type, marked POSSIBLE.
    e = edge(g, P + "service.OrderService#find", EdgeKind.CALLS, P + "repo.OrderRepository")
    assert e.attrs["member"] == "findById" and e.confidence == Confidence.POSSIBLE
    assert edge(g, P + "service.OrderService#audit", EdgeKind.INSTANTIATES,
                P + "service.OrderService.AuditLog")
    assert edge(g, P + "service.OrderService#audit", EdgeKind.CALLS,
                P + "service.OrderService.AuditLog#write")


def test_endpoints(java_graph):
    g = java_graph
    names = {s.name: s for s in g.of_kind(NodeKind.ENDPOINT)}
    assert set(names) == {"GET /api/orders/{id}", "POST /api/orders", "PUT /api/orders/legacy",
                          "PATCH /api/orders/legacy", "KAFKA orders.retry"}
    assert g.handlers("endpoint:GET /api/orders/{id}") == [P + "web.OrderController#get"]
    assert names["KAFKA orders.retry"].attrs["framework"] == "messaging"


def test_integrations_and_flow(java_graph):
    g = java_graph
    assert edge(g, P + "client.PaymentClient#charge", EdgeKind.INTEGRATES, "service:http-client")
    assert edge(g, P + "repo.OrderRepository", EdgeKind.INTEGRATES, "service:database")
    flow = g.trace(P + "web.OrderController#create")
    assert set(flow.services) == {"service:http-client", "service:database"}
    tree = flow.tree()
    assert tree[0] == (P + "web.OrderController#create", 0)
    assert (P + "service.OrderService.AuditLog#write", 3) in tree


def test_package_cycle_detected(java_graph):
    # Order lives in service, which depends on client and repo, which use Order.
    assert ["java:com.acme.shop.client", "java:com.acme.shop.repo",
            "java:com.acme.shop.service"] in java_graph.cycles()


def test_partial_parse_is_reported_not_fatal():
    facts = JavaFrontend().parse(b"package a; class Ok { void f() { } } class Bad { void g( }",
                                 "Bad.java")
    assert facts.errors and "partial" in facts.errors[0]
    assert any(s.id == "java:a.Ok#f" for s in facts.symbols)


def test_overloads_collapse_to_one_node():
    facts = JavaFrontend().parse(b"class C { void f(){} void f(int x){} }", "C.java")
    methods = [s for s in facts.symbols if s.kind == NodeKind.METHOD]
    assert [m.id for m in methods] == ["java:C#f"] and methods[0].attrs["overloads"] == 2


def test_wildcard_imported_types_are_not_swallowed_by_own_package(tmp_path):
    from codelens.pipeline import analyze
    pkg = tmp_path / "com" / "acme"
    pkg.mkdir(parents=True)
    (pkg / "Client.java").write_text(
        "package com.acme;\n"
        "import org.springframework.web.client.*;\n"
        "public class Client { private RestTemplate http; void go() { http.getForObject(\"u\", String.class); } }\n")
    g = analyze(tmp_path)
    assert edge(g, "java:com.acme.Client#go", EdgeKind.INTEGRATES, "service:http-client")
    assert not [e for e in g.edges if e.src == "java:com.acme.Client" and e.dst == "java:com.acme"
                and e.kind == EdgeKind.USES_TYPE]
