---
description: Generate OpenAPI specs of the outgoing calls of a workspace, one per external system
argument-hint: [workspace] [systems.toml]
---

Generate OpenAPI specs of outgoing calls using the four project agents in sequence.

Workspace: `$1`, default `examples/bank-poc`.
Systems file: `$2`, default `<workspace>/systems.toml`.

Run the steps in order. Each agent has no memory of the others, so pass file paths between them.

1. **outbound-extractor**: give it the workspace and systems file. It writes
   `out/openapi/work/inventory.json`. Read the inventory afterwards to get the system list.
2. **outbound-enricher**: start one per system, in parallel (one message, several Agent calls).
   Name the system in each prompt. Each writes `out/openapi/work/enriched.<system-slug>.json`.
3. **openapi-writer**: run once it has all the enriched files. It writes
   `out/openapi/<system-slug>.yaml` and `out/openapi/unsupported.md`.
4. **openapi-validator**: run last, and give it no summary of what you think the result is.

If the validator reports FAIL, stop. Show the failures and do not "fix" the specs yourself.
Ask whether to re-run the failing step.

On PASS, finish with a table of system, spec file, operations and unsupported calls, plus
the validator's spot-check notes.
