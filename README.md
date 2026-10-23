# codelens

Offline static analysis for source code. Point it at a repository, or a folder of
repositories, and it extracts:

- module structure and the module dependency graph
- call flows from entry points (REST endpoints, message listeners)
- external integrations and outbound calls (HTTP, SOAP, JMS, Kafka, jPOS, ...)
- journeys: which internal core systems (CBS/Finacle, DCMS) and third-party systems
  (FSS, NPCI, UIDAI/Aadhaar) each endpoint reaches

It can write JSON, Markdown and DOT reports, serve answers to an AI client over MCP,
and save everything in an embedded graph database that you can query with Cypher.

## Guarantees

- **Never executes target code.** Sources are only parsed (`ast`, tree-sitter).
- **Offline.** No network access at runtime.
- **Every edge has a confidence:** `certain` (explicit syntax), `likely` (resolved through a
  declared type or scope), `possible` (name fallback).
- **No secrets in output.** Secret-looking config values are masked when read.

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
```

| Extra   | Adds                                         |
|---------|----------------------------------------------|
| `store` | Kuzu graph database (`index`, `query`, MCP graph tools) |
| `mcp`   | MCP server (`codelens mcp`)                  |
| `dev`   | pytest and Kuzu                              |

Check the install:

```bash
pytest -q
codelens --version
```

## Quick start with the sample workspace

`examples/bank-poc/` has three Spring repos and a `systems.toml`. Its hosts and keys are
sample values, not real bank config.

```bash
codelens journeys examples/bank-poc -o out/       # journeys report
codelens index examples/bank-poc                  # build the graph store
codelens query examples/bank-poc/.codelens/graph.kuzu \
  "MATCH (s:Symbol {kind: 'endpoint'}) RETURN s.repo, s.id LIMIT 10"
```

## Commands

### `codelens analyze <repo> -o out/ [-f json,md,dot] [-l java,py]`

Analyse a single repository and write reports to `out/`. `-f` picks formats, `-l` limits
languages (Java and Python are supported).

### `codelens journeys <folder-of-repos> [-s systems.toml] -o out/`

Treat every subfolder with a build marker (for example `pom.xml`) as a repo, analyse each
one, and map each entry point to the outbound calls it triggers. Writes a JSON and a
Markdown journeys report.

### `codelens index <folder-of-repos> [-s systems.toml] [--db PATH]`

Parse the repos and save the graph store. Default location:
`<folder>/.codelens/graph.kuzu` (git-ignored).

### `codelens query <db> "<cypher>"`

Run read-only Cypher against a store and print JSON. The database is opened read-only, so
writes fail.

### `codelens mcp <folder-of-repos> [-s systems.toml] [--db PATH] [--reindex]`

Serve questions about the workspace over MCP (stdio). On start it loads the graph store if
it is up to date (a hash of sources, config files and the codelens version), and otherwise
re-parses and saves it. `--reindex` forces a re-parse.

If the store can't be written (read-only mount), a warning goes to stderr and the server
runs from memory. The graph tools then return an error. Use `--db` with a writable path.

## System catalog: `systems.toml`

You own this file. It labels each outbound call by host glob, property glob or marker
substring, and maps package patterns to layers. Default location: `<folder>/systems.toml`,
or pass `-s`.

```toml
[layers]
web = ["*.web.*", "*.controller.*", "*.api.*"]
domain = ["*.domain.*", "*.service.*", "*.core.*"]
infrastructure = ["*.infra.*", "*.client.*", "*.adapter.*"]

[[system]]
name = "Finacle (CBS)"
category = "internal-core"      # internal-core | internal-service | middleware | third-party
hosts = ["finacle*", "*.finacle.*"]
properties = ["finacle.*", "cbs.*"]
markers = ["FIN_"]
```

The first matching system wins. `middleware` systems (ESB, gateways) are reported as the
hop ("via"), not the destination. A host that matches another repo's
`spring.application.name` is an `internal-service`, and the journey continues in that repo.

Edits to `systems.toml` apply on the next run without re-parsing.

## Use with an AI client (MCP)

`.mcp.json` in this repo registers the server for Claude Code against `examples/bank-poc`.
For another workspace, add an entry like this (use absolute paths):

```json
{
  "mcpServers": {
    "codelens": {
      "type": "stdio",
      "command": "/path/to/codelens/.venv/bin/codelens",
      "args": ["mcp", "/path/to/your/repos"]
    }
  }
}
```

Tools (all read-only):

| Tool                  | Answers                                                      |
|-----------------------|--------------------------------------------------------------|
| `overview`            | repos, entry points, outbound calls, systems reached         |
| `find_journeys`       | call journeys from entry points to external systems          |
| `impact`              | what depends on a system ("what breaks if X is down")        |
| `callers` / `callees` | who calls a method, and what it calls, transitively          |
| `domain_dependencies` | systems reached by each domain-layer class                   |
| `outbound_calls`      | every outbound call site with protocol, URL and system       |
| `source`              | code of a class or method, secrets masked                    |
| `search_code`         | text or regex search with enclosing method and layer         |
| `graph_schema`        | tables and columns of the graph database, with examples      |
| `graph_query`         | read-only Cypher (max 1000 rows, 10 s timeout)               |

Symbols are named `Class.method`, for example `CardService.block`. Results include `at`
(`repo/path:line`) and a confidence.

> Tool output goes to whichever model is connected. For real bank code, connect only a
> model your security team has approved.

## Graph store

Parsed repos are saved in an embedded [Kuzu](https://kuzudb.com) database.

| Table          | Holds                                                  |
|----------------|--------------------------------------------------------|
| `Repo`         | one row per repo, with masked config, stats and errors |
| `Symbol`       | classes, methods, endpoints, external nodes            |
| `Edge`         | `Symbol → Symbol` with `kind`, `confidence`, `file`, `line` |
| `Outbound`     | classified outbound calls                              |
| `HAS_OUTBOUND` | `Symbol → Outbound`                                    |
| `Meta`         | schema version and fingerprint                         |

Example queries:

```cypher
// endpoints per repo
MATCH (s:Symbol {kind: 'endpoint'}) RETURN s.repo, s.id

// outbound calls per system
MATCH (m:Symbol)-[:HAS_OUTBOUND]->(o:Outbound)
RETURN o.system, o.category, count(*) AS calls ORDER BY calls DESC

// who calls a method
MATCH (a:Symbol)-[e:Edge {kind: 'calls'}]->(b:Symbol {id: 'java:com.acme.Foo#bar'})
RETURN a.id, e.confidence, e.file, e.line
```

Symbol ids look like `java:com.acme.Foo`, `java:com.acme.Foo#bar`, `py:pkg.mod.func`,
`endpoint:POST /cards/{id}/block`.

## Known limits

- Branch conditions aren't shown: a call inside `if (...)` looks unconditional.
- Values injected through constructor `@Value` parameters aren't tracked.
- Chained calls (`a().b()`) aren't resolved.
- Outbound call detection covers Java only.
- Any source change re-parses the whole workspace (no incremental updates yet).

## Development

```bash
pytest -q
```

Tests block network sockets. See `CLAUDE.md` for architecture notes and the roadmap, and
`docs/` for the approach and MCP data-source notes.
