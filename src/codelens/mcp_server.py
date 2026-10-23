"""MCP server: lets an AI client ask questions about a workspace through codelens.

The server is read-only and offline. It loads the workspace once at startup and
answers from the graph; the model only chooses tools and explains results.
Whatever a tool returns is sent to the connected model, so for real bank code
connect only a model your security team has approved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from codelens.query import QueryEngine
from codelens.store import DEFAULT_DB, describe, open_workspace, query_limited
from codelens.systems import Catalog

MAX_ROWS = 1000
QUERY_TIMEOUT_MS = 10_000
GRAPH_EXAMPLES = [
    "MATCH (s:Symbol {kind: 'endpoint'}) RETURN s.repo, s.id LIMIT 20",
    "MATCH (m:Symbol)-[:HAS_OUTBOUND]->(o:Outbound) RETURN o.system, o.category, count(*) AS calls"
    " ORDER BY calls DESC",
    "MATCH (a:Symbol)-[e:Edge {kind: 'calls'}]->(b:Symbol {id: 'java:com.acme.Foo#bar'})"
    " RETURN a.id, e.confidence, e.file, e.line",
]

INSTRUCTIONS = """\
codelens answers questions about a workspace of source repos: which internal core
systems (e.g. CBS/Finacle, DCMS), internal services and third-party systems
(e.g. NPCI, UIDAI, FSS) the code reaches, and the call journey to each.

How to answer well:
- Start with `overview` if you don't know the repos or systems yet.
- Facts come from tools. Cite the `at` locations (repo/path:line) and say when
  confidence is "likely" or "possible" rather than "certain".
- Journeys show calls, not conditions. Before saying when or why a call happens,
  read the code with `source`.
- Use `search_code` for business terms (limits, statuses, error codes) that are
  not class or method names.
- Symbols are named Class.method (e.g. CardService.block). An ambiguous name
  returns the candidates; ask again with one of them.
- For questions no tool covers (counts, custom filters), call `graph_schema`, then
  `graph_query` with read-only Cypher.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                            openWorldHint=False)


def build_server(root: Path, systems: Path | None = None, db: Path | None = None,
                 reindex: bool = False) -> MCPServer:
    """Serve a workspace, loading it from the graph store when that is up to date."""
    catalog_path = systems or (root / "systems.toml")
    catalog = Catalog.load(catalog_path if catalog_path.is_file() else None)
    engine = QueryEngine(open_workspace(root, catalog, db, reindex))
    server = MCPServer(name="codelens", instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def overview() -> dict[str, Any]:
        """List the repos, their entry points and outbound calls, and the systems reached."""
        return engine.overview()

    @server.tool(annotations=READ_ONLY)
    def find_journeys(system: str | None = None, entry: str | None = None, repo: str | None = None,
                      category: str | None = None, limit: int = 30) -> dict[str, Any]:
        """Call journeys from entry points (REST endpoints, message listeners) to external systems.

        Every filter is optional. `system` and `entry` are case-insensitive substrings,
        e.g. system="NPCI", entry="/cards". `category` is one of internal-core,
        internal-service, middleware, third-party, unclassified.
        """
        return engine.find_journeys(system, entry, repo, category, limit)

    @server.tool(annotations=READ_ONLY)
    def impact(system: str) -> dict[str, Any]:
        """What depends on a system: affected entry points, domain classes and the direct calls.

        Use it for "what breaks if <system> is down". `system` is a substring such as "Finacle" or "ESB".
        """
        return engine.impact(system)

    @server.tool(annotations=READ_ONLY)
    def callers(symbol: str, depth: int = 3) -> dict[str, Any]:
        """Who calls a method, transitively, including other repos calling its endpoint over HTTP."""
        return engine.callers(symbol, depth)

    @server.tool(annotations=READ_ONLY)
    def callees(symbol: str, depth: int = 2) -> dict[str, Any]:
        """What a method calls, transitively, and any outbound calls to external systems on the way."""
        return engine.callees(symbol, depth)

    @server.tool(annotations=READ_ONLY)
    def domain_dependencies(class_name: str | None = None) -> dict[str, Any]:
        """For each domain-layer class: the internal core, internal service and third-party systems it reaches."""
        return engine.domain_dependencies(class_name)

    @server.tool(annotations=READ_ONLY)
    def outbound_calls(repo: str | None = None, system: str | None = None,
                       category: str | None = None) -> dict[str, Any]:
        """Every outbound call site with its protocol, URL, system and how it was classified."""
        return engine.outbound_calls(repo, system, category)

    @server.tool(annotations=READ_ONLY)
    def source(symbol: str) -> dict[str, Any]:
        """The code of a class or method, with line numbers. Secret-looking values are masked."""
        return engine.source(symbol)

    @server.tool(annotations=READ_ONLY)
    def search_code(text: str, repo: str | None = None, regex: bool = False,
                    limit: int = 40) -> dict[str, Any]:
        """Search code and config for text (case-insensitive), with the enclosing method and layer of each hit."""
        return engine.search_code(text, repo, regex, limit)

    db_path = db or (root / DEFAULT_DB)

    def need_store() -> dict[str, Any] | None:
        if not db_path.is_file():
            return {"error": f"no graph store at {db_path} (it could not be written; "
                             "run `codelens index` or start the server with a writable --db)"}
        return None

    @server.tool(annotations=READ_ONLY)
    def graph_schema() -> dict[str, Any]:
        """Tables and columns of the graph database, plus example Cypher. Read this before `graph_query`."""
        if (err := need_store()):
            return err
        return {**describe(db_path), "examples": GRAPH_EXAMPLES}

    @server.tool(annotations=READ_ONLY)
    def graph_query(cypher: str, limit: int = 100) -> dict[str, Any]:
        """Run read-only Cypher (Kuzu dialect) on the graph database and return the rows.

        Use it for questions the other tools do not cover, e.g. counts, paths and
        custom filters. Writes fail. Rows are capped by `limit` (max 1000) and
        `truncated` says if more existed. Call `graph_schema` first for the tables.
        """
        if (err := need_store()):
            return err
        try:
            return query_limited(db_path, cypher, max(1, min(limit, MAX_ROWS)), QUERY_TIMEOUT_MS)
        except Exception as exc:  # bad Cypher, write attempt, timeout: let the model fix it
            return {"error": str(exc)}

    return server
