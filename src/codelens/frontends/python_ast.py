"""Python frontend built on the stdlib `ast` module (parse only, never exec)."""

from __future__ import annotations

import ast
import warnings
from pathlib import PurePosixPath

from codelens.catalog import PYTHON_INTEGRATIONS
from codelens.frontends.base import Frontend
from codelens.model import (
    Confidence, Edge, EdgeKind, FileFacts, Location, NodeKind, Symbol,
)

LANG = "py"
HTTP_DECORATORS = {"get", "post", "put", "delete", "patch", "head", "options",
                   "route", "api_route", "websocket"}


def module_name_for(rel_path: str) -> tuple[str, bool]:
    """'src/pkg/mod.py' -> ('pkg.mod', False); 'pkg/__init__.py' -> ('pkg', True)."""
    parts = list(PurePosixPath(rel_path).with_suffix("").parts)
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts) or "__root__", is_package


def dotted(node: ast.AST) -> str | None:
    """`a.b.c` expression -> 'a.b.c'; None for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def pid(qualified: str) -> str:
    return f"{LANG}:{qualified}"


class PythonFrontend(Frontend):
    language = LANG
    extensions = frozenset({".py"})
    integrations = PYTHON_INTEGRATIONS

    def parse(self, source: bytes, rel_path: str) -> FileFacts:
        module, is_package = module_name_for(rel_path)
        facts = FileFacts(path=rel_path, language=LANG, module_id=pid(module))
        facts.symbols.append(Symbol(facts.module_id, NodeKind.MODULE, module, LANG,
                                    Location(rel_path, 1), {"files": [rel_path]}))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source, filename=rel_path)
        except (SyntaxError, ValueError) as exc:
            facts.errors.append(f"{rel_path}: syntax error: {exc}")
            return facts
        _Visitor(facts, module, is_package).visit(tree)
        return facts


class _Visitor(ast.NodeVisitor):
    def __init__(self, facts: FileFacts, module: str, is_package: bool) -> None:
        self.facts = facts
        self.module = module
        self.package = module if is_package else module.rpartition(".")[0]
        self.aliases: dict[str, str] = {}       # local name -> qualified target
        self.defs: dict[str, str] = {}          # module-level name -> qualified
        self.class_stack: list[str] = []        # qualified class names
        self.scope_stack: list[str] = [facts.module_id]

    # -- helpers ---------------------------------------------------------
    def loc(self, node: ast.AST) -> Location:
        return Location(self.facts.path, getattr(node, "lineno", 1),
                        getattr(node, "col_offset", 0))

    @property
    def scope(self) -> str:
        return self.scope_stack[-1]

    def add_edge(self, kind: EdgeKind, conf: Confidence, node: ast.AST,
                 candidates: list[str], src: str | None = None, **attrs) -> None:
        self.facts.edges.append(Edge(src=src or self.scope, kind=kind, confidence=conf,
                                     candidates=[pid(c) for c in candidates],
                                     location=self.loc(node), attrs=attrs))

    def resolve_name(self, name: str) -> tuple[list[str], Confidence] | None:
        """Resolve a dotted expression name to qualified candidates."""
        root, _, rest = name.partition(".")
        if root == "self" and self.class_stack and rest and "." not in rest:
            return [f"{self.class_stack[-1]}.{rest}"], Confidence.LIKELY
        if root in self.defs:
            base = self.defs[root]
        elif root in self.aliases:
            base = self.aliases[root]
        else:
            return None
        return [f"{base}.{rest}" if rest else base], Confidence.LIKELY

    def absolute_from(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        parts = self.package.split(".") if self.package else []
        if node.level > 1:
            parts = parts[: len(parts) - (node.level - 1)]
        if node.module:
            parts.append(node.module)
        return ".".join(p for p in parts if p)

    # -- visitors --------------------------------------------------------
    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs[stmt.name] = f"{self.module}.{stmt.name}"
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                top = alias.name.split(".")[0]
                self.aliases[top] = top
            self.add_edge(EdgeKind.IMPORTS, Confidence.CERTAIN, node, [alias.name],
                          src=self.facts.module_id, target=alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self.absolute_from(node)
        for alias in node.names:
            if alias.name == "*":
                self.add_edge(EdgeKind.IMPORTS, Confidence.CERTAIN, node, [base],
                              src=self.facts.module_id, target=base)
                continue
            target = f"{base}.{alias.name}" if base else alias.name
            self.aliases[alias.asname or alias.name] = target
            self.add_edge(EdgeKind.IMPORTS, Confidence.CERTAIN, node, [target, base],
                          src=self.facts.module_id, target=target)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent = self.class_stack[-1] if self.class_stack else self.module
        qualified = f"{parent}.{node.name}"
        sid = pid(qualified)
        decorators = [d for d in (dotted(x.func if isinstance(x, ast.Call) else x)
                                  for x in node.decorator_list) if d]
        self.facts.symbols.append(Symbol(sid, NodeKind.CLASS, node.name, LANG, self.loc(node),
                                         {"qualified_name": qualified, "decorators": decorators,
                                          "end_line": node.end_lineno}))
        self.facts.edges.append(Edge(self.scope, EdgeKind.CONTAINS, Confidence.CERTAIN, dst=sid,
                                     location=self.loc(node)))
        for base in node.bases:
            name = dotted(base)
            resolved = self.resolve_name(name) if name else None
            if resolved:
                self.add_edge(EdgeKind.INHERITS, Confidence.CERTAIN, base, resolved[0], src=sid)
        self.class_stack.append(qualified)
        self.scope_stack.append(sid)
        self.generic_visit(node)
        self.scope_stack.pop()
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        in_class = bool(self.class_stack) and self.scope == pid(self.class_stack[-1])
        if in_class:
            qualified, kind = f"{self.class_stack[-1]}.{node.name}", NodeKind.METHOD
        elif self.scope == self.facts.module_id:
            qualified, kind = f"{self.module}.{node.name}", NodeKind.FUNCTION
        else:
            # Nested function: attribute its body to the enclosing function.
            self.generic_visit(node)
            return
        sid = pid(qualified)
        self.facts.symbols.append(Symbol(sid, kind, node.name, LANG, self.loc(node), {
            "qualified_name": qualified,
            "end_line": node.end_lineno,
            "async": isinstance(node, ast.AsyncFunctionDef),
            "params": [a.arg for a in node.args.args],
        }))
        self.facts.edges.append(Edge(self.scope, EdgeKind.CONTAINS, Confidence.CERTAIN, dst=sid,
                                     location=self.loc(node)))
        for deco in node.decorator_list:
            self._maybe_endpoint(deco, sid)
        self.scope_stack.append(sid)
        for stmt in node.body:
            self.visit(stmt)
        self.scope_stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _maybe_endpoint(self, deco: ast.expr, handler: str) -> None:
        if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
            return
        verb = deco.func.attr
        if verb not in HTTP_DECORATORS or not deco.args:
            return
        first = deco.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return
        path = first.value
        if verb in ("route", "api_route"):
            methods = ["GET"]
            for kw in deco.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [e.value.upper() for e in kw.value.elts
                               if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        else:
            methods = [verb.upper()]
        for method in methods:
            eid = f"endpoint:{method} {path}"
            self.facts.symbols.append(Symbol(eid, NodeKind.ENDPOINT, f"{method} {path}", LANG,
                                             self.loc(deco), {"method": method, "path": path,
                                                              "framework": "python-web"}))
            self.facts.edges.append(Edge(handler, EdgeKind.EXPOSES, Confidence.CERTAIN, dst=eid,
                                         location=self.loc(deco)))

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted(node.func)
        if name:
            resolved = self.resolve_name(name)
            if resolved:
                candidates, conf = resolved
                self.add_edge(EdgeKind.CALLS, conf, node, candidates, callee=name)
        self.generic_visit(node)
