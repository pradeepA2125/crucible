# Review: on-demand capability schemas and guidance

Date: 2026-10-19. Reviews `2026-10-19-on-demand-capability-loading.md` against the code and the latest
captured first request (`.crucible/state/artifacts/chat/chat-183601b80251/…/controller-turn-00.json`).

The code-seam table is accurate. Verified: the controller response schema has no tool-name enum,
so newly visible tools are callable through `tool_calls` without changing the provider action
schema. Delivering bundles through history, not the system prompt, is the right call: appending
schemas to the system catalog would force a full re-prefill of the conversation on every load.

## 1. Lead with reachability: today's MCP budget hides tools

This backend connected 39 MCP tools (`playwright` 25, `everything` 13, `fetch` 1; `web` failed on a
missing `OLLAMA_API_KEY`). The first request carried only 21 of them. `McpToolSource.definitions`
order-truncates at `CRUCIBLE_MCP_TOOLS_MAX_CHARS` (16,000 characters), which stopped after 21
Playwright tools. Four Playwright tools and every `everything` and `fetch` tool were invisible, and
nothing told the model or the user.

**Changed since the spec was written:** the default budget is now 64,000 characters
(`agentd/mcp/config.py`, test and CLAUDE.md updated). That fits every currently connected tool, at
the cost of a larger first request until deferral lands. The spec's purpose should lead with this:
loading makes every connected tool reachable and removes the hard ceiling on adding servers.

## 2. State the size of the token saving

Latest first request: about 19.7k tokens (system 18.0k, of which the tool catalog is 8.1k; payload
1.7k). The 21 Playwright schemas are 3.4k, about 17% of the first request. After that request the
system prefix is cached, so the per-turn gain is mostly 3.4k of window room, and a browser task pays
an extra round trip. Worth doing, but evaluate it on reachability and window room, not billing.

## 3. Cut schema-once rows from this workstream

The spec's own measurement is 184 tokens (2.31%). That is a new format the model must read correctly
in exchange for almost nothing. Keep ordinary JSON.

## 4. Drop the 5–6 level hierarchy for now

The largest real server has 25 tools. Use one level, `mcp.<server>`. Add families, family summaries
and directory continuation only when a server is actually large enough to need them.

## 5. Load whole small servers; search only large ones

All of Playwright is 3.4k tokens. When a server's complete catalog fits a fixed size (about 4k
tokens), return all of it: no lexical matcher, no recall misses, no refinement rounds. Query within a
server only above that size.

## 6. Replace the lifetime table with three rules

The ten-event table, including "active operation" detection after compaction, is the largest source
of state. Cache-safe alternative:

1. Persist the loaded tool set per thread (and per child) in durable state, the way todos are kept.
2. After compaction, restart or rewind, append current bundles for every still-loaded tool to the
   rebuilt history. Compaction already invalidates the cached prefix, so this costs nothing extra.
3. Only permission narrowing or server disablement removes tools. No idle/active distinction.

## 7. Gap: tool results can truncate a bundle

`tools/batching.py` splits the result budget across a batch (`share = budget // len(calls)`; the
fallback budget is 16,000 characters) and replaces overflow with "[Result omitted beyond aggregate
budget…]". A bundle batched with other reads can lose part of a schema, which the spec forbids. The
loader must run alone in its step, or its result must be exempt from the shared budget. The spec only
rules out batching the loader with a call that uses an unloaded tool.

## 8. Evaluate on more than one model

The `search_index` trial showed adoption only on the ChatGPT plan. Weaker models here have needed
worked examples before using a discovery step reliably. Include at least one local or weaker model
in the matched evaluation before making this the default.

## Aside

`.crucible/mcp.json` enables `everything`, the MCP reference test server. With the larger budget its
13 tools now reach the model; consider disabling it in this workspace.
