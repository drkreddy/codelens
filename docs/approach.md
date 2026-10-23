# Approach: where an LLM is and isn't involved

**Short answer:** the analysis is fully deterministic and has no LLM. An LLM only appears at the very end, if you connect one to the MCP server to ask questions.

## Where no LLM is involved (everything that produces the facts)

1. **Discovery** (`discovery.py`): a safe file walk. It skips symlinks, binaries and oversized files.
2. **Parsing** (`frontends/`): tree-sitter queries for Java and stdlib `ast` for Python. Both turn source bytes into symbols and edges. Nothing is executed.
3. **Linking** (`linker.py`): resolves names through a fixed order (declared types, then imports, then same package). It matches `catalog.py` to find integrations. It adds `dispatch` edges for interface-to-implementation calls.
4. **Graph** (`graph.py`): module dependencies, cycles and a BFS from each entry point to trace flows.
5. **Config and systems** (`config_files.py`, `systems.py`): flattens `application.yml`/`.properties`, masks secrets, and labels hosts from your `systems.toml` rules.
6. **Journeys and reports** (`journeys.py`, `reporters/`): stitches the call journeys across repos and writes JSON, Markdown and DOT.

So `codelens analyze` and `codelens journeys` never call a model or the network. Their output is reproducible, and every edge carries a confidence (`certain`, `likely` or `possible`).

## Where an LLM comes in (optional, only at the query layer)

- `codelens mcp` starts a server with read-only tools: overview, find_journeys, impact, callers, callees, outbound_calls, source and search_code.
- `QueryEngine` (`query.py`) answers those tools with plain deterministic lookups over the already-built graph.
- The LLM is the **MCP client you connect**, such as Claude Code through `.mcp.json`. It decides which tool to call, reads the results and writes the natural-language answer. codelens itself doesn't contain one.

## What this means in practice

- **Accuracy:** the facts come from the parser and linker. The LLM only chooses what to look up and explains the result. It can't change the facts, though it can misread them.
- **Data exposure:** `source` and `search_code` return code lines to whichever model is connected. Secret-looking assignments are masked, but for real bank code you should connect only an approved model.
- **Offline use:** you can use the `analyze` and `journeys` reports without any LLM at all.
