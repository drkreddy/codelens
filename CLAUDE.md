# codelens

Offline static analysis tool. Given a repository it extracts module structure,
call flows from entry points, external integrations and the module dependency
graph, and writes JSON, Markdown and DOT reports.

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
codelens analyze <repo> -o out/ [-f json,md,dot] [-l java,py]
codelens journeys <folder-of-repos> [-s systems.toml] -o out/   # API journeys report
codelens mcp <folder-of-repos> [-s systems.toml] [--db P] [--reindex]   # Q&A tools over MCP (stdio)
codelens index <folder-of-repos> [--db P]                        # parse and save the graph store
codelens query <db> "<cypher>"                                   # read-only Cypher on the store
```

The current goal is a banking POC: map which **internal core systems** (CBS/Finacle,
DCMS) and **third-party systems** (FSS, NPCI, UIDAI/Aadhaar) the domain layer reaches,
and the full call journey, across many repos. Sample workspace: `examples/bank-poc/`
(three Spring repos plus `systems.toml`). Its hosts and keys are sample values, not real bank config.

## Non-negotiable rules

- **Never execute target code.** Only parse it (`ast.parse`, tree-sitter). No `import`,
  `exec`, `compile`-and-run, subprocesses or build tools on analysed sources.
  `tests/test_python_frontend.py::test_parse_never_executes_code` guards this.
- **Offline only.** No network access at runtime. `tests/conftest.py` blocks sockets for
  every test.
- **Every edge carries a confidence.** `certain` = explicit syntax (import, extends,
  annotation); `likely` = resolved through a declared type or enclosing scope;
  `possible` = name/owner fallback. Never present a guess as `certain`.
- Discovery never follows symlinks, skips binaries and oversized files, and one
  crashing file must not abort the run (errors are collected in the report).

## Pipeline

```
discovery.py  -> frontends/<lang>  -> linker.py      -> graph.py   -> reporters/
(safe file      (bytes -> FileFacts:  (resolve            (CodeGraph:   (json, md, dot)
 walk)           symbols + edges       candidates,         module deps,
                 with candidate        externals,          cycles, flow
                 names)                integrations)       tracing)
```

- `model.py`: language-neutral IR. Symbol ids are `<lang>:<qualified>`:
  `java:com.acme.Foo`, `java:com.acme.Foo#bar`, `java:com.acme.Foo#<init>`,
  `py:pkg.mod.func`. Java modules are packages and Python modules are files. Others are
  `endpoint:<VERB> <path>`, `ext:<lang>:<package>` and `service:<kind>`.
- Frontends do not resolve across files. They emit `Edge.candidates` (qualified names
  in preference order). `Linker` tries them, falls back from `X#m` / `a.b.c` to the
  owning symbol (marked `member`, downgraded to `possible`), and otherwise matches
  `catalog.py` to emit `integrates` edges to `service:*` nodes. Unresolved
  imports and supertypes become `ext:` nodes. Other unresolved refs are dropped
  and counted in `stats.unresolved_by_kind`.
- `catalog.py`: known integration prefixes per language (HTTP clients, DBs,
  messaging, cloud SDKs). Extend it there rather than in frontends.
- `CodeGraph.trace(entry)` runs a BFS over `calls`/`instantiates` and collects
  `integrates` edges. That is how "endpoint → services touched" is computed.

## Java frontend (`frontends/java.py` + `frontends/queries/java/*.scm`)

- Syntax knowledge lives in the `.scm` query files. Each query finds one construct
  anywhere in the file. Python attributes matches to the enclosing type or method by
  walking `node.parent`.
- Type resolution order: types declared in the file → single imports → fully
  qualified → same package, wildcard imports, `java.lang` (as `likely`).
- Call receivers are resolved via locals and params → fields → capitalised name (static
  call). Chained calls (`a().b()`) are not resolved yet.
- Endpoints: Spring `@*Mapping` (with class `@RequestMapping` prefix), JAX-RS
  `@Path` + verb, and `@KafkaListener`/`@RabbitListener`/`@JmsListener`/`@SqsListener`.
- Overloads collapse to one method node (`attrs.overloads`).
- tree-sitter and tree-sitter-java are pinned to 0.23.x because grammar node names can
  change. `tests/test_java_queries.py` has a golden case per `.scm` file and
  fails if a query file has none.

## Journeys (`journeys.py`, `systems.py`, `config_files.py`)

- `Workspace(root, catalog)`: every subfolder with a build marker is a repo. Each repo is
  analysed on its own, and its `application*.yml/.properties` are flattened, with secret-looking
  values masked at read time (`config_files.mask`). Reports must never contain secrets
  (`tests/test_journeys.py::test_reports_never_contain_secrets`).
- The Java frontend records `attrs.outbound` on methods that call a client type from
  `catalog.JAVA_CLIENTS` (RestTemplate, WebClient, Spring WS, JMS, Kafka, jPOS…) or that belong
  to a `@FeignClient`. URL arguments are evaluated from literals, constants, `@Value("${…}")`
  fields and `+` concatenation. For jPOS, the MTI comes from `setMTI`.
- `systems.toml` (user-owned) labels calls by host glob, property glob or marker substring.
  The `middleware` category (ESB) is reported as "via", not as the destination. A host whose
  first label equals another repo's `spring.application.name` is `internal-service`, and the
  journey continues from that repo's matching endpoint.
- The linker adds `dispatch` edges from interface/superclass methods to overriding methods, so
  journeys cross Spring DI seams (domain port → infra adapter).
- Known gaps: branch conditions aren't shown (a call inside `if (LOST)` looks unconditional);
  values injected through constructor `@Value` params aren't tracked; only Java has outbound detection.

## Q&A over MCP (`query.py`, `mcp_server.py`)

- `QueryEngine` holds all the logic and returns plain dicts with `at` (repo/path:line) and
  confidence. `mcp_server.py` only wraps it as read-only tools: overview, find_journeys,
  impact, callers, callees, domain_dependencies, outbound_calls, source, search_code, plus
  `graph_schema` and `graph_query` (read-only Cypher on the Kuzu store, row-capped at 1000, 10 s timeout).
- `.mcp.json` registers the server for this project against `examples/bank-poc`.
- `source` and `search_code` mask secret-looking assignments (`query.mask_line`). Tool output
  goes to whichever model is connected, so for real bank code only connect an approved model.
- Never print to stdout in code reachable from `codelens mcp`: stdout carries the protocol.

## Graph store (`store.py`)

- Parsed repos are saved in an embedded Kuzu database (`<root>/.codelens/graph.kuzu`, git-ignored;
  `kuzu==0.11.3`, optional extra `store`, included in `dev`). `codelens mcp` loads it when the
  fingerprint (sha256 of sources, config files, codelens version) still matches, otherwise it
  re-parses and saves. A store that cannot be written only logs a warning to stderr.
- The in-memory `CodeGraph` is still what `query.py` and `journeys.py` use. The store is a copy of it
  plus derived `Outbound` nodes for Cypher. Tables: `Repo`, `Symbol`, `Edge` (rel), `Outbound`,
  `HAS_OUTBOUND` (rel), `Meta`. Outbound calls are not loaded back; they are recomputed from method
  attrs and config on load, so `systems.toml` edits apply without re-parsing.
- `codelens query` opens the database read-only, so Cypher writes fail. Never use Kuzu extensions
  (they download from the network); JSON lives in STRING columns. Config in the store is the masked one.
- Not done: per-file incremental updates (any change re-parses the whole workspace).

## Python frontend (`frontends/python_ast.py`)

stdlib `ast`. Resolves calls through import aliases, module-level defs and
`self.method()`. Detects Flask/FastAPI-style `@x.get/post/route(...)` endpoints.
A leading `src/` path component is stripped from module names.

## Roadmap

1. ✅ Core pipeline, IR, linker, reports, Python frontend
2. ✅ Java frontend with tree-sitter queries
3. Intra-procedural control flow: a CFG per method (branches, loops, try/catch,
   early returns) and data flow from endpoint parameters to persistence and outbound calls
4. Better resolution: inheritance-aware member lookup, interface → implementation
   (Spring beans), chained calls, Python type hints
5. Config-aware integrations: `application.yml`/`.properties` URLs, Feign `url`,
   string-literal hosts, SQL table names in `@Query`/JDBC strings
6. More languages (TypeScript, Go): a frontend plus `.scm` files each
7. Per-file incremental updates of the graph store; interactive HTML graph viewer
