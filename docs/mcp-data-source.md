# Where the MCP tools get their data

The MCP tools answer from an in-memory workspace. That workspace is loaded from an
embedded graph database (Kuzu) when the stored copy is up to date, and parsed from
source otherwise.

## Startup flow

1. `.mcp.json` launches `codelens mcp <workspace>` as a stdio subprocess. For this
   project the workspace is `examples/bank-poc`.
2. `build_server` (`src/codelens/mcp_server.py`) loads `systems.toml` and calls
   `open_workspace` (`src/codelens/store.py`).
3. `open_workspace` computes a fingerprint of the workspace (sha256 of every source file
   and `application*` config file, plus the codelens version) and compares it with the one
   saved in `<workspace>/.codelens/graph.kuzu`:
   - **match:** `store.load` rebuilds the graphs from the database. No source is parsed.
   - **no match, `--reindex`, or no store:** `Workspace` parses every repo, then `store.save`
     writes the result. If the store cannot be written, a warning goes to stderr and the
     parsed workspace is used anyway.
4. `QueryEngine` (`src/codelens/query.py`) builds its caller index and journeys from the
   workspace, as before.

## What is stored

Tables: `Repo`, `Symbol`, `Edge` (relationship), `Outbound` and `HAS_OUTBOUND`
(relationship), `Meta`. Symbols and edges keep their original order, so a loaded workspace
produces byte-identical journeys reports.

Classified outbound calls are written for querying but are not loaded back. They are
recomputed from the stored method attrs and config on every load, so editing `systems.toml`
takes effect without re-parsing. Config values in the store are the already-masked ones.

## Querying the graph directly

```bash
codelens index examples/bank-poc          # build the store without starting MCP
codelens query examples/bank-poc/.codelens/graph.kuzu \
  "MATCH (m:Symbol)-[:HAS_OUTBOUND]->(o:Outbound) RETURN m.id, o.system, o.operation"
```

`query` opens the database read-only, so writes fail.

## Notes

- `source` and `search_code` still read the `.java` files from the repos on demand.
- `out/journeys.json` and `out/journeys.md` come from `codelens journeys` and are never
  read back by the MCP tools.
- Any source or config change re-parses the whole workspace. Per-file incremental updates
  are not implemented.
- To analyse other repos, change the path argument in `.mcp.json`. Use `--db` to keep the
  store outside the repos folder.
