"""Java frontend: tree-sitter parse + declarative queries in queries/java/*.scm.

Each query finds one kind of construct anywhere in the file. Python code then
attributes every match to its enclosing type or method by walking parents,
and resolves simple type names through the package, imports and java.lang.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import tree_sitter_java
from tree_sitter import Language, Node, Parser, Query

from codelens.catalog import CLIENT_OPERATIONS, JAVA_CLIENTS, JAVA_INTEGRATIONS, match
from codelens.frontends.base import Frontend
from codelens.model import (
    Confidence, Edge, EdgeKind, FileFacts, Location, NodeKind, Symbol,
)

LANG = "java"
QUERY_DIR = Path(__file__).parent / "queries" / "java"

TYPE_DECLS = {
    "class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.INTERFACE,
    "enum_declaration": NodeKind.ENUM,
    "record_declaration": NodeKind.RECORD,
    "annotation_type_declaration": NodeKind.INTERFACE,
}
METHOD_DECLS = {"method_declaration", "constructor_declaration", "compact_constructor_declaration"}
SCOPE_DECLS = TYPE_DECLS.keys() | METHOD_DECLS

SPRING_MAPPINGS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": None,
}
JAXRS_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
LISTENERS = {  # annotation -> (channel, attribute naming the destination)
    "KafkaListener": ("KAFKA", "topics"),
    "RabbitListener": ("AMQP", "queues"),
    "JmsListener": ("JMS", "destination"),
    "SqsListener": ("SQS", "value"),
}
STEREOTYPES = {"RestController", "Controller", "Service", "Repository", "Component",
               "Configuration", "Entity", "FeignClient", "SpringBootApplication"}


@lru_cache(maxsize=1)
def load_queries() -> tuple[Language, dict[str, Query]]:
    language = Language(tree_sitter_java.language())
    queries = {p.stem: language.query(p.read_text(encoding="utf-8"))
               for p in sorted(QUERY_DIR.glob("*.scm"))}
    return language, queries


def jid(qualified: str) -> str:
    return f"{LANG}:{qualified}"


def text(node: Node) -> str:
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def key(node: Node) -> tuple[int, int, str]:
    return (node.start_byte, node.end_byte, node.type)


def loc(path: str, node: Node) -> Location:
    return Location(path, node.start_point[0] + 1, node.start_point[1])


def type_name(node: Node | None) -> str | None:
    """Reduce a type node to a dotted name; None for primitives, void, wildcards."""
    if node is None:
        return None
    if node.type in ("type_identifier", "identifier", "scoped_type_identifier", "scoped_identifier"):
        return "".join(text(node).split())
    if node.type == "generic_type":
        return type_name(node.named_children[0])
    if node.type == "array_type":
        return type_name(node.child_by_field_name("element"))
    return None


def enclosing(node: Node, kinds) -> Node | None:
    n = node.parent
    while n is not None and n.type not in kinds:
        n = n.parent
    return n


def annotation_values(args: Node | None) -> dict[str, list[str]]:
    """@X("a") -> {"value": ["a"]}; @X(path={"a","b"}, method=M.GET) -> {...}."""
    values: dict[str, list[str]] = {}
    if args is None:
        return values
    for child in args.named_children:
        if child.type == "element_value_pair":
            name = text(child.child_by_field_name("key"))
            value = child.child_by_field_name("value")
        else:
            name, value = "value", child
        values.setdefault(name, []).extend(_literal_values(value))
    return values


def _literal_values(node: Node | None) -> list[str]:
    if node is None:
        return []
    if node.type == "string_literal":
        return ["".join(text(c) for c in node.named_children if c.type == "string_fragment")]
    if node.type == "element_value_array_initializer":
        return [v for c in node.named_children for v in _literal_values(c)]
    return [text(node)]


def join_paths(*parts: str) -> str:
    segments = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(segments)


@dataclass
class _FileContext:
    path: str
    package: str
    single_imports: dict[str, str] = field(default_factory=dict)
    wildcard_imports: list[str] = field(default_factory=list)
    local_types: dict[str, str] = field(default_factory=dict)

    def resolve_type(self, name: str | None) -> tuple[list[str], Confidence] | None:
        if not name:
            return None
        head, _, rest = name.partition(".")
        suffix = f".{rest}" if rest else ""
        if head in self.local_types:
            return [self.local_types[head] + suffix], Confidence.CERTAIN
        if head in self.single_imports:
            return [self.single_imports[head] + suffix], Confidence.CERTAIN
        if rest and head[:1].islower():
            return [name], Confidence.CERTAIN  # already fully qualified
        candidates = [f"{self.package}.{name}" if self.package else name]
        candidates += [f"{w}.{name}" for w in self.wildcard_imports]
        candidates.append(f"java.lang.{name}")
        return candidates, Confidence.LIKELY


class JavaFrontend(Frontend):
    language = LANG
    extensions = frozenset({".java"})
    integrations = JAVA_INTEGRATIONS

    def __init__(self) -> None:
        language, self.queries = load_queries()
        self.parser = Parser(language)

    def matches(self, name: str, root: Node) -> list[dict[str, list[Node]]]:
        return [captures for _, captures in self.queries[name].matches(root)]

    def parse(self, source: bytes, rel_path: str) -> FileFacts:
        root = self.parser.parse(source).root_node
        package = ""
        for m in self.matches("package", root):
            package = text(m["package.name"][0])
        module_name = package or "<default>"
        facts = FileFacts(path=rel_path, language=LANG, module_id=jid(module_name))
        if root.has_error:
            facts.errors.append(f"{rel_path}: syntax errors, results are partial")
        facts.symbols.append(Symbol(facts.module_id, NodeKind.MODULE, module_name, LANG,
                                    None, {"files": [rel_path]}))
        ctx = _FileContext(rel_path, package)
        _Extractor(self, root, facts, ctx).run()
        return facts


class _Extractor:
    def __init__(self, frontend: JavaFrontend, root: Node, facts: FileFacts, ctx: _FileContext):
        self.fe, self.root, self.facts, self.ctx = frontend, root, facts, ctx
        self.type_ids: dict[tuple, str] = {}        # type decl -> qualified name
        self.method_ids: dict[tuple, str] = {}      # method decl -> symbol id
        self.method_owner: dict[tuple, str] = {}    # method decl -> qualified type
        self.symbols: dict[str, Symbol] = {}
        self.annotations: dict[tuple, list[tuple[str, dict[str, list[str]]]]] = {}
        self.fields: dict[str, dict[str, str]] = {}   # qualified type -> field -> type
        self.locals: dict[tuple, dict[str, str]] = {}  # method decl -> var -> type
        # Values for evaluating URL-ish expressions: "${prop}" templates or initializer nodes.
        self.field_values: dict[str, dict[str, str | Node]] = {}
        self.local_values: dict[tuple, dict[str, Node]] = {}

    def run(self) -> None:
        self.imports()
        self.types()
        self.methods()
        self.collect_annotations()
        self.heritage()
        self.collect_fields()
        self.collect_locals()
        self.calls()
        self.endpoints()

    # -- utilities -------------------------------------------------------
    def edge(self, src: str, kind: EdgeKind, node: Node, resolved, **attrs) -> None:
        if not resolved:
            return
        candidates, conf = resolved
        self.facts.edges.append(Edge(src, kind, conf, candidates=[jid(c) for c in candidates],
                                     location=loc(self.ctx.path, node), attrs=attrs))

    def scope_of(self, node: Node) -> tuple[str, str] | None:
        """(source symbol id, qualified enclosing type) for any node."""
        owner = enclosing(node, SCOPE_DECLS)
        if owner is None:
            return None
        if owner.type in METHOD_DECLS:
            k = key(owner)
            if k in self.method_ids:
                return self.method_ids[k], self.method_owner[k]
            return None
        q = self.type_ids.get(key(owner))
        return (jid(q), q) if q else None

    # -- passes ----------------------------------------------------------
    def imports(self) -> None:
        for m in self.fe.matches("imports", self.root):
            decl = m["import"][0]
            name = text(m["import.name"][0])
            is_static = any(c.type == "static" for c in decl.children)
            if "import.wildcard" in m:
                if not is_static:
                    self.ctx.wildcard_imports.append(name)
                target = name
            elif is_static:
                target = name.rpartition(".")[0]  # import static a.B.m -> a.B
            else:
                self.ctx.single_imports[name.rpartition(".")[2]] = name
                target = name
            self.facts.edges.append(Edge(self.facts.module_id, EdgeKind.IMPORTS, Confidence.CERTAIN,
                                         candidates=[jid(target)], location=loc(self.ctx.path, decl),
                                         attrs={"target": name, "static": is_static}))

    def types(self) -> None:
        decls = []
        for m in self.fe.matches("types", self.root):
            decl = next(nodes[0] for cap, nodes in m.items() if cap != "type.name")
            names = []
            n: Node | None = decl
            while n is not None:
                if n.type in TYPE_DECLS:
                    names.append(text(n.child_by_field_name("name")))
                n = n.parent
            qualified = ".".join(filter(None, [self.ctx.package, *reversed(names)]))
            self.type_ids[key(decl)] = qualified
            self.ctx.local_types.setdefault(names[0], qualified)
            decls.append((decl, qualified))
        for decl, qualified in decls:
            parent = enclosing(decl, TYPE_DECLS)
            container = jid(self.type_ids[key(parent)]) if parent else self.facts.module_id
            sym = Symbol(jid(qualified), TYPE_DECLS[decl.type], qualified.rpartition(".")[2], LANG,
                         loc(self.ctx.path, decl), {"qualified_name": qualified, "annotations": [],
                                                    "end_line": decl.end_point[0] + 1})
            self.symbols[sym.id] = sym
            self.facts.symbols.append(sym)
            self.facts.edges.append(Edge(container, EdgeKind.CONTAINS, Confidence.CERTAIN,
                                         dst=sym.id, location=sym.location))

    def methods(self) -> None:
        for m in self.fe.matches("methods", self.root):
            decl = m["method"][0]
            parent = enclosing(decl, TYPE_DECLS)
            if parent is None or key(parent) not in self.type_ids:
                continue
            owner_q = self.type_ids[key(parent)]
            is_ctor = decl.type != "method_declaration"
            name = "<init>" if is_ctor else text(m["method.name"][0])
            sid = f"{jid(owner_q)}#{name}"
            self.method_ids[key(decl)] = sid
            self.method_owner[key(decl)] = owner_q
            if sid in self.symbols:  # overload: one node per name
                self.symbols[sid].attrs["overloads"] += 1
                continue
            params = decl.child_by_field_name("parameters")
            sym = Symbol(sid, NodeKind.METHOD, name, LANG, loc(self.ctx.path, decl), {
                "qualified_name": f"{owner_q}#{name}",
                "end_line": decl.end_point[0] + 1,
                "constructor": is_ctor,
                "params": len(params.named_children) if params else 0,
                "overloads": 1,
                "annotations": [],
            })
            self.symbols[sid] = sym
            self.facts.symbols.append(sym)
            self.facts.edges.append(Edge(jid(owner_q), EdgeKind.CONTAINS, Confidence.CERTAIN,
                                         dst=sid, location=sym.location))

    def collect_annotations(self) -> None:
        for m in self.fe.matches("annotations", self.root):
            node = m["annotation"][0]
            name = text(m["annotation.name"][0])
            args = m.get("annotation.args", [None])[0]
            decl = node.parent.parent if node.parent and node.parent.type == "modifiers" else node.parent
            if decl is not None:
                self.annotations.setdefault(key(decl), []).append((name, annotation_values(args)))
                sid = self.method_ids.get(key(decl)) or (
                    jid(self.type_ids[key(decl)]) if key(decl) in self.type_ids else None)
                if sid and sid in self.symbols:
                    simple = name.rpartition(".")[2]
                    self.symbols[sid].attrs["annotations"].append(simple)
                    if simple in STEREOTYPES and "stereotype" not in self.symbols[sid].attrs:
                        self.symbols[sid].attrs["stereotype"] = simple
            scope = self.scope_of(node)
            if scope:
                self.edge(scope[0], EdgeKind.USES_TYPE, node, self.ctx.resolve_type(name),
                          annotation=name)

    def heritage(self) -> None:
        for m in self.fe.matches("heritage", self.root):
            for cap, nodes in m.items():
                node = nodes[0]
                decl = enclosing(node, TYPE_DECLS)
                if decl is None or key(decl) not in self.type_ids:
                    continue
                kind = EdgeKind.IMPLEMENTS if cap == "implements" else EdgeKind.INHERITS
                self.edge(jid(self.type_ids[key(decl)]), kind, node,
                          self.ctx.resolve_type(type_name(node)))

    def collect_fields(self) -> None:
        for m in self.fe.matches("fields", self.root):
            type_node, name_node = m["field.type"][0], m["field.name"][0]
            name = text(name_node)
            decl = enclosing(type_node, TYPE_DECLS)
            if decl is None or key(decl) not in self.type_ids:
                continue
            owner_q = self.type_ids[key(decl)]
            self._remember_field_value(owner_q, name, name_node)
            tname = type_name(type_node)
            if not tname:
                continue
            self.fields.setdefault(owner_q, {})[name] = tname
            self.edge(jid(owner_q), EdgeKind.USES_TYPE, type_node, self.ctx.resolve_type(tname),
                      field=name)

    def collect_locals(self) -> None:
        for m in self.fe.matches("locals", self.root):
            type_node, name_node = m["var.type"][0], m["var.name"][0]
            name = text(name_node)
            owner = enclosing(type_node, SCOPE_DECLS)
            if owner is None or owner.type not in METHOD_DECLS or key(owner) not in self.method_ids:
                continue
            if name_node.parent is not None and name_node.parent.type == "variable_declarator":
                value = name_node.parent.child_by_field_name("value")
                if value is not None:
                    self.local_values.setdefault(key(owner), {})[name] = value
            tname = type_name(type_node)
            if not tname:
                continue
            self.locals.setdefault(key(owner), {})[name] = tname
            is_param = type_node.parent is not None and type_node.parent.type == "formal_parameter"
            if is_param and owner.type == "constructor_declaration":
                # Constructor injection: a dependency of the whole type.
                self.edge(jid(self.method_owner[key(owner)]), EdgeKind.USES_TYPE, type_node,
                          self.ctx.resolve_type(tname), injected=name)

    def receiver_type(self, receiver: Node | None, call: Node, owner_q: str) -> str | None:
        method = enclosing(call, METHOD_DECLS)
        local_vars = self.locals.get(key(method), {}) if method is not None else {}
        fields = self.fields.get(owner_q, {})
        if receiver is None or receiver.type == "this":
            return owner_q
        if receiver.type == "identifier":
            name = text(receiver)
            if name in local_vars:
                return local_vars[name]
            if name in fields:
                return fields[name]
            return name if name[:1].isupper() else None  # static call on a type
        if receiver.type == "field_access":
            obj = receiver.child_by_field_name("object")
            if obj is not None and obj.type == "this":
                return fields.get(text(receiver.child_by_field_name("field")))
        if receiver.type in ("scoped_identifier", "scoped_type_identifier"):
            return text(receiver)
        return None

    def calls(self) -> None:
        for m in self.fe.matches("calls", self.root):
            if "call" in m:
                node = m["call"][0]
                scope = self.scope_of(node)
                if not scope:
                    continue
                src, owner_q = scope
                name = text(m["call.name"][0])
                receiver = m.get("call.receiver", [None])[0]
                if src in self.symbols and self.symbols[src].kind == NodeKind.METHOD:
                    self.detect_outbound(node, src, owner_q)
                rtype = self.receiver_type(receiver, node, owner_q)
                if rtype is None:
                    continue
                if rtype == owner_q:
                    resolved = [owner_q], Confidence.LIKELY
                else:
                    resolved = self.ctx.resolve_type(rtype)
                if resolved:
                    candidates = [f"{c}#{name}" for c in resolved[0]]
                    self.edge(src, EdgeKind.CALLS, node, (candidates, Confidence.LIKELY),
                              callee=f"{text(receiver) + '.' if receiver else ''}{name}")
            elif "new" in m:
                node = m["new"][0]
                scope = self.scope_of(node)
                if scope:
                    self.edge(scope[0], EdgeKind.INSTANTIATES, node,
                              self.ctx.resolve_type(type_name(m["new.type"][0])))

    def endpoints(self) -> None:
        for decl_key, method_id in self.method_ids.items():
            anns = self.annotations.get(decl_key)
            if not anns:
                continue
            type_anns = self.annotations.get(self._type_key(self.method_owner[decl_key]), [])
            feign = next((v for n, v in type_anns if n.rpartition(".")[2] == "FeignClient"), None)
            if feign is not None:
                self.feign_outbound(method_id, anns, feign)
                continue
            spring_prefixes = [p for n, v in type_anns if n.endswith("RequestMapping")
                               for p in (v.get("value") or v.get("path") or [])] or [""]
            jaxrs_prefix = next((v.get("value", [""])[0] for n, v in type_anns
                                 if n.rpartition(".")[2] == "Path"), "")
            method_path = next((v.get("value", [""])[0] for n, v in anns
                                if n.rpartition(".")[2] == "Path"), "")
            location = self.symbols[method_id].location
            for name, values in anns:
                simple = name.rpartition(".")[2]
                if simple in SPRING_MAPPINGS:
                    verb = SPRING_MAPPINGS[simple]
                    verbs = [verb] if verb else (
                        [v.rpartition(".")[2] for v in values.get("method", [])] or ["ANY"])
                    paths = values.get("value") or values.get("path") or [""]
                    for prefix in spring_prefixes:
                        for path in paths:
                            for v in verbs:
                                self.add_endpoint(method_id, v, join_paths(prefix, path), "spring", location)
                elif simple in JAXRS_VERBS:
                    self.add_endpoint(method_id, simple, join_paths(jaxrs_prefix, method_path),
                                      "jax-rs", location)
                elif simple in LISTENERS:
                    channel, attr = LISTENERS[simple]
                    for dest in values.get(attr) or values.get("value") or ["?"]:
                        self.add_endpoint(method_id, channel, dest, "messaging", location)

    # -- outbound calls --------------------------------------------------
    def _remember_field_value(self, owner_q: str, name: str, name_node: Node) -> None:
        declarator = name_node.parent
        if declarator is None or declarator.type != "variable_declarator":
            return
        field_decl = declarator.parent
        for ann, values in self.annotations.get(key(field_decl), []) if field_decl else []:
            if ann.rpartition(".")[2] == "Value" and values.get("value"):
                self.field_values.setdefault(owner_q, {})[name] = values["value"][0]
                return
        value = declarator.child_by_field_name("value")
        if value is not None:
            self.field_values.setdefault(owner_q, {})[name] = value

    def evaluate(self, node: Node | None, method: Node | None, owner_q: str, depth: int = 0) -> str:
        """Best-effort string value: literals, constants, @Value placeholders, `+`.

        Unknown parts become `{name}` or `{?}` so the shape of a URL survives.
        """
        if node is None or depth > 8:
            return "{?}"
        t = node.type
        if t == "string_literal":
            parts = []
            for c in node.named_children:
                if c.type == "string_fragment":
                    parts.append(text(c))
                elif c.type == "escape_sequence":
                    parts.append(text(c)[1:])
            return "".join(parts)
        if t == "parenthesized_expression" and node.named_children:
            return self.evaluate(node.named_children[0], method, owner_q, depth + 1)
        if t == "binary_expression" and text(node.child_by_field_name("operator")) == "+":
            return (self.evaluate(node.child_by_field_name("left"), method, owner_q, depth + 1)
                    + self.evaluate(node.child_by_field_name("right"), method, owner_q, depth + 1))
        name = None
        if t == "identifier":
            name = text(node)
            local = self.local_values.get(key(method), {}).get(name) if method is not None else None
            if local is not None:
                return self.evaluate(local, method, owner_q, depth + 1)
        elif t == "field_access" and text(node.child_by_field_name("object")) == "this":
            name = text(node.child_by_field_name("field"))
        if name is not None:
            value = self.field_values.get(owner_q, {}).get(name)
            if isinstance(value, str):
                return value
            if value is not None:
                return self.evaluate(value, None, owner_q, depth + 1)
            return "{" + name + "}"
        if t in ("decimal_integer_literal", "true", "false"):
            return text(node)
        return "{?}"

    def detect_outbound(self, call: Node, src: str, owner_q: str) -> None:
        parent = call.parent
        if parent is not None and parent.type == "method_invocation" and \
                parent.child_by_field_name("object") == call:
            return  # an inner link of a chain; the outermost call handles the chain
        chain: list[Node] = []
        n: Node | None = call
        while n is not None and n.type == "method_invocation":
            chain.append(n)
            n = n.child_by_field_name("object")
        chain.reverse()  # root-most call first
        rtype = self.receiver_type(n, call, owner_q) if n is not None else None
        resolved = self.ctx.resolve_type(rtype) if rtype else None
        client = next((hit for c in (resolved[0] if resolved else [])
                       if (hit := match(JAVA_CLIENTS, c))), None)
        if client is None:
            return
        protocol, label = client
        ops = [text(c.child_by_field_name("name")) for c in chain]
        allowed = CLIENT_OPERATIONS.get(label)
        if allowed is not None and not allowed.intersection(ops):
            return
        method = enclosing(call, METHOD_DECLS)
        args = [self.evaluate(a, method, owner_q)
                for c in chain for a in (c.child_by_field_name("arguments").named_children
                                         if c.child_by_field_name("arguments") else [])]
        target = next((a for a in args if "${" in a or "://" in a), None)
        record = {
            "protocol": protocol,
            "client": label,
            "operation": ".".join(ops),
            "target": target,
            "evidence": [a[:240] for a in args if a not in ("{?}",)][:6],
            "class_properties": sorted({v[2:-1].split(":")[0] for v in self.field_values.get(owner_q, {}).values()
                                        if isinstance(v, str) and v.startswith("${") and v.endswith("}")}),
            "line": call.start_point[0] + 1,
        }
        if protocol == "ISO 8583" and method is not None:
            mti = self._find_mti(method, owner_q)
            if mti:
                record["operation"] = f"MTI {mti}"
        self.symbols[src].attrs.setdefault("outbound", []).append(record)

    def _find_mti(self, method: Node, owner_q: str) -> str | None:
        stack = [method]
        while stack:
            n = stack.pop()
            if n.type == "method_invocation" and text(n.child_by_field_name("name")) == "setMTI":
                args = n.child_by_field_name("arguments")
                if args is not None and args.named_children:
                    return self.evaluate(args.named_children[0], method, owner_q)
            stack.extend(n.named_children)
        return None

    def feign_outbound(self, method_id: str, anns, feign: dict[str, list[str]]) -> None:
        service = (feign.get("name") or feign.get("value") or ["?"])[0]
        base = (feign.get("url") or [f"http://{service}"])[0]
        for name, values in anns:
            simple = name.rpartition(".")[2]
            if simple not in SPRING_MAPPINGS:
                continue
            verb = SPRING_MAPPINGS[simple] or next(
                (v.rpartition(".")[2] for v in values.get("method", [])), "ANY")
            path = join_paths((values.get("value") or values.get("path") or [""])[0])
            self.symbols[method_id].attrs.setdefault("outbound", []).append({
                "protocol": "HTTP", "client": "Feign", "operation": f"{verb} {path}",
                "target": base.rstrip("/") + path, "service": service, "evidence": [],
                "class_properties": [], "line": self.symbols[method_id].location.line,
                "http": {"method": verb, "path": path},
            })

    def _type_key(self, qualified: str):
        return next((k for k, q in self.type_ids.items() if q == qualified), None)

    def add_endpoint(self, handler: str, verb: str, path: str, framework: str,
                     location: Location | None) -> None:
        eid = f"endpoint:{verb} {path}"
        self.facts.symbols.append(Symbol(eid, NodeKind.ENDPOINT, f"{verb} {path}", LANG, location,
                                         {"method": verb, "path": path, "framework": framework}))
        self.facts.edges.append(Edge(handler, EdgeKind.EXPOSES, Confidence.CERTAIN, dst=eid,
                                     location=location))
