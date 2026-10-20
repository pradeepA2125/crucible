# Changelog

## v0.8.1 (2026-10-20)

### MCP tools load by name first

- The agent now loads only the MCP tools it needs, by their exact names from the directory. It loads a whole server only when it is unsure which tools it needs or cannot find the right one. Set `CRUCIBLE_CAPABILITY_LOAD_MODE=namespace` to load a whole server every time, as in v0.8.0.
- **Measured on gpt-6-luna at high effort.** On GitHub's 63-tool server, input fell by 35% (61k against 93k per prompt) at the same correctness. On small servers and multi-step browser work, loading by name costs more than whole-server loads, because each new tool needs another load step. A live check of the final wording answered 10 of 10 prompts correctly; every load was by name.
- **Not yet checked:** medium effort, and providers other than luna.
- The evaluation harness gains `--load-mode`; results are in `docs/on-demand-capability-loading-pilot.md`.

## v0.8.0 (2026-10-20)

### On-demand MCP tool loading, now on by default

The main chat agent no longer receives every connected MCP server's tool schemas in each request. It sees a short directory instead: one line per server, which carries the server's own summary when the server sends one, and one line per tool giving its full call name and the first sentence of its description. When a request needs a server, the agent calls `load_capabilities` and gets that server's schemas.

- **Smaller requests.** With seven servers and 125 MCP tools, about 19,900 fewer tokens go into every request that uses no MCP tool (22,177 tokens of schemas replaced by a 2,239-token directory). With three servers it is about 5,500 tokens, or 25%.
- **Whole servers by default.** A load returns the whole server. If that is over the bundle limit, the agent retries with a `query` or exact `tool_names`. The limit is 20% of the context window, capped at 50,000 tokens. GitHub's 63-tool server (~15.7k tokens) loads whole on a 272k window.
- **Clear refusals.** A call to a tool that isn't loaded yet, or to a name made from the namespace (`mcp.fetch__fetch`), is refused with the exact load step and the real tool name. The agent then recovers by itself.
- **Turning it off.** Set `crucible.performance.mcpOnDemand` to false, or `CRUCIBLE_CAPABILITY_LOADING_ENABLED=0`. Sub-agents, team members and the task path keep full catalogs.
- **Evaluated on gpt-6-luna (ChatGPT plan) only.** Discovery, compaction, page reads, seven servers and a 63-tool GitHub server all pass; see `docs/on-demand-capability-loading-pilot.md`. Other providers, including the prompt-only JSON path (`openai_compatible`, Ollama, Gemini, Anthropic), are untested with it on. If tools are missed there, turn it off.
- **Reasoning effort matters.** At medium effort luna sometimes answers at the first step without calling a tool (5 of 11 GitHub prompts). At high effort, 9 of 9 were correct.

### MCP servers

- **Per-server tool filters.** `enabled_tools` and `disabled_tools` in `.crucible/mcp.json`, with a checkbox per tool in Settings › MCP Servers. Changing a filter re-filters the live connection without reconnecting, and a filtered tool is refused before the approval gate.
- **No MCP catalog size cap.** Every connected tool is listed. Previously, tools past the cap disappeared silently while Settings still showed them enabled.
- The client keeps each server's `instructions` from the MCP handshake.

### Code discovery and context

- **`search_index`** replaces the frozen retrieval seed (repo tree, outlines and excerpts, ~7k tokens in every chat request). The agent now asks the index when it needs to find code: it gets paths, symbols, line ranges and file relationships, never source.
- **o200k_base token counts** are used for the compaction trigger, the recall budget and the composer meter. The encoding loads once in the background; until then, counts fall back to estimates.

### Fixes

- **Indexer:** deleted and renamed files leave the live graph.
- **Semantic index:** old chunks are kept until their replacements are embedded, so search never sees a gap.
- **Controller:** a `batch_id` the model copies into its next action is ignored instead of failing the step.
- **Tests:** they no longer wait on fake provider backoff or leak PTYs.

### Docs and evaluation

- `docs/on-demand-capability-loading-pilot.md` holds the full evaluation: the rollout matrix, re-runs after each fix, replays, seven-server and GitHub suites, and medium against high effort, all with raw data under `docs/validation/capability-loading/`.
- `eval_rollout.py` gains `--chatgpt-registration`, `--effort`, and the `browser_actions`, `more_mcp` and `github_mcp` suites.
- The retired sidecar experiments are archived.
