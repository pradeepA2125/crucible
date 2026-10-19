# On-demand MCP capability loading (pilot)

With `CRUCIBLE_CAPABILITY_LOADING_ENABLED=1` (extension setting `crucible.performance.mcpOnDemand`, default off), the main chat agent no longer receives MCP tool schemas or the MCP guidance block in its first request. Its system prompt instead lists one line per connected MCP server under `OPTIONAL CAPABILITIES`, and the agent calls `load_capabilities` when the user's request needs one. Spec: [docs/superpowers/specs/2026-10-19-on-demand-capability-loading.md](superpowers/specs/2026-10-19-on-demand-capability-loading.md); plan: [docs/superpowers/plans/2026-10-19-on-demand-capability-loading.md](superpowers/plans/2026-10-19-on-demand-capability-loading.md), Part B.

## How it works

- **Namespaces.** Each connected server is one namespace, `mcp.<server>`. Built-in tools, including `web_search`/`web_fetch`, stay in the catalog. Per-server tool filters apply first: a filtered tool is never in the directory, a bundle or the callable set.
- **Loader.** `load_capabilities {namespace, query?, tool_names?, limit?}` returns a bundle: the selected tools' full JSON schemas, a version per tool (12 hex characters of the schema's sha256) and, the first time, the MCP guidance. With only a namespace it returns the whole server; `query` picks up to `limit` (1–8, default 4) tools by word overlap with names and descriptions; `tool_names` picks exact unprefixed names. A bundle over 32,000 characters is refused (`bundle_too_large`) and activates nothing. The loader must run alone in its step, and its result is never cut by the batch result budget.
- **Loaded means visible.** A tool is callable when a bundle carrying its current version is in the history being sent. No other state is kept, so restart and rewind need nothing extra. Compaction that evicts a bundle unloads its tools; a server whose schema changes on reconnect makes the old bundle stale. A call to an unloaded tool is refused before any member of its batch runs, with a message to load the namespace again.
- **No new permission.** Execution still goes through `McpToolSource`: the filter/down-server refusal, then the approval gate.
- **Scope.** Main agent only. Sub-agents, team members and the task path keep their full catalogs.

## Measurement

[validation/capability-loading/first-request.json](validation/capability-loading/first-request.json), produced by [measure_first_request.py](validation/capability-loading/measure_first_request.py), rebuilds the controller system prompt from the tool catalog of a recorded first request in this repository (2026-10-19, one Playwright server). It counts `o200k_base` tokens with compact context and tool batching on, the managed-runtime defaults, and without AGENTS.md, skills or memory blocks:

| | Tokens |
| --- | --- |
| System prompt, flag off | 16,783 |
| System prompt, flag on | 13,613 |
| First-request saving | 3,170 |
| Removed: 21 MCP schemas + MCP guidance | 3,405 + 156 |
| Added: loader schema + capability block and directory | 189 + 207 |
| Loading the whole `mcp.playwright` namespace later | 3,974 |

This is one workspace's estimate, not a billing figure. The recorded request showed 21 of the server's tools because the old 16,000-character MCP budget cut the rest; with the current 64,000-character budget more tools are listed, so both the saving and a whole-namespace bundle grow. A turn that loads the namespace pays the bundle once and replays it with history until compaction, so the saving is real only for turns that never need the server, or load a few tools with a `query`.

## Not measured, and deferred

- No model-behavior evaluation yet: whether models load the right namespace, pick useful queries, or try unloaded tools. That evaluation, including weaker models, comes before any change of default.
- Restoring bundles for work still in progress after compaction (spec "Lifetime" row 3). Today the model reloads the namespace.
- Children, team members and the task path.
- Provider cache effects of the smaller stable prefix plus appended bundles.
