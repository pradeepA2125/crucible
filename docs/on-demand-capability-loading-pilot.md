# On-demand MCP capability loading (pilot)

With `CRUCIBLE_CAPABILITY_LOADING_ENABLED` on (the default since 2026-10-20; extension setting `crucible.performance.mcpOnDemand`, default true; set `0` to turn it off), the main chat agent no longer receives MCP tool schemas or the MCP guidance block in its first request. Its system prompt instead lists one line per connected MCP server under `OPTIONAL CAPABILITIES`, and the agent calls `load_capabilities` when the user's request needs one. Spec: [docs/superpowers/specs/2026-10-19-on-demand-capability-loading.md](superpowers/specs/2026-10-19-on-demand-capability-loading.md); plan: [docs/superpowers/plans/2026-10-19-on-demand-capability-loading.md](superpowers/plans/2026-10-19-on-demand-capability-loading.md), Part B.

## How it works

- **Namespaces.** Each connected server is one namespace, `mcp.<server>`. Built-in tools, including `web_search`/`web_fetch`, stay in the catalog. Per-server tool filters apply first: a filtered tool is never in the directory, a bundle or the callable set.
- **Loader.** `load_capabilities {namespace, query?, tool_names?, limit?}` returns a bundle: the selected tools' full JSON schemas, a version per tool (12 hex characters of the schema's sha256) and, the first time, the MCP guidance. With only a namespace it returns the whole server; `query` picks up to `limit` (1–8, default 4) tools by word overlap with names and descriptions; `tool_names` picks exact unprefixed names. A bundle over its token ceiling is refused (`bundle_too_large`) and activates nothing. The ceiling is 20% of the live compaction window, capped at 50,000 tokens (o200k; chars/3 until the encoding loads): 25,600 at 128k, 50,000 at 272k. Until 2026-10-20 it was a flat 32,000 characters. The loader must run alone in its step, and its result is never cut by the batch result budget.
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

This is one workspace's estimate, not a billing figure. The recorded request showed 21 of the server's tools because the old 16,000-character MCP budget cut the rest; with no MCP size cap now (removed after this measurement) every tool is listed, so both the saving and a whole-namespace bundle grow. A turn that loads the namespace pays the bundle once and replays it with history until compaction, so the saving is real only for turns that never need the server, or load a few tools with a `query`.

## Live run (2026-10-19)

The installed extension's managed backend, on the ChatGPT plan with `gpt-6-luna`, with Playwright (25 tools), `everything` (16) and `fetch` (1) connected. The same message was sent in a new thread with the flag off, then on. Counts are the provider's own input tokens per request. Raw data: [validation/capability-loading/live-2026-10-19.json](validation/capability-loading/live-2026-10-19.json). One run per cell.

| Turn | Flag off | Flag on | Change |
| --- | --- | --- | --- |
| Plain turn, first request | 22,676 | 17,106 | −5,570 (−24.6%) |
| Plain turn, all input (2 requests) | 45,065 | 33,925 | −11,140 (−24.7%) |
| Browser turn, requests | 2 | 3 | +1 (`load_capabilities`) |
| Browser turn, all input | 46,406 | 55,825 | +9,419 (+20%) |
| Browser turn, uncached input | 23,878 | 20,113 | −3,765 (−16%) |
| Browser turn, time | 15.3 s | 22.4 s | +7.1 s |

- Every request in a turn that uses no MCP tool is about 5,570 tokens smaller: the 42 connected tools' schemas and the MCP guidance, minus the loader and the directory.
- In the browser turn the model loaded Playwright with a query (`"browser navigate open URL read page title"`), got a 2,873-character bundle of 4 tools, called `browser_navigate` next and answered correctly. The load costs one extra request, so total input went up; uncached input went down, because every request in the turn started from the smaller prefix.
- Both plain turns took 2 requests because the model answered "ready" as text without calling a function and the loop retried. That happens with the flag off too.

## Not measured, and deferred

- Model behavior on providers other than the two in the rollout evaluation below (Anthropic, Gemini, Ollama, Groq and the rest are untested live).
- Restoring bundles for work still in progress after compaction (spec "Lifetime" row 3). Today the model reloads the namespace.
- Children, team members and the task path.
- Provider cache effects of the smaller stable prefix plus appended bundles.

## Rollout evaluation (2026-10-19)

The matched evaluation the spec requires before a default change ("Validation and rollout"). Recommendation at the end.

**Setup.** Each arm is its own backend (`python -m agentd.serve`), started by [eval_rollout.py](validation/capability-loading/eval_rollout.py) with the installed extension's managed-spawn settings, its own chat, task and memory databases, and only `CRUCIBLE_CAPABILITY_LOADING_ENABLED` differing between arms. The workspace is a `git archive` of this repository at `766296b6`, with the same skills and agents and `.crucible/mcp.json` (Playwright 25 tools, `everything` 16, `fetch` 1), and no remembered approvals. The harness approves every gate except in the denial scenario, where it rejects every `mcp_tool` gate. It answers a clarifying question with "Use your best judgment." Prompts are what a user would type; none names the loader or the namespace.

- **Models.** `gpt-6-luna` on the ChatGPT plan (native function calling, effort medium), and `nvidia/nemotron-3-ultra-550b-a55b` on NVIDIA NIM through `openai_compatible`. NIM failed the startup newline probe, so every nemotron arm ran in `json_object` mode with the schema in the prompt. That is the prompt-only path. Local Ollama models were dropped from the matrix at the user's request; no other provider was run.
- **Suites.** *main*: greeting, a narrow code question, "what does example.com say right now?", a two-turn books.toscrape.com task (navigate, open Poetry, read; then open the book and read its stock), and a screenshot request with the gate denied. *discovery*: four first-turn prompts in fresh threads, repeated (luna 5×, plus 10 more of the explicit-browser prompt; nemotron 3×). *compaction*: one six-turn browsing thread with `CRUCIBLE_MEMORY_WINDOW_TOKENS=45000`, so compaction fires inside the session.
- **Measures.** Requests, input, cached and output tokens per turn come from the backend's `/usage` route (provider counts). First-request size comes from the provider's own count on the closing `token_progress` event. Loads, refusals of unloaded tools and malformed-output corrections come from the SSE stream and the backend log. Correctness is checked against ground truth fetched the same day: 50 categories; Poetry's first book is *A Light in the Attic* at £51.77 with 22 in stock; the Travel category has 11 titles; Mystery's most expensive book is *Boar Island* at £59.48; Historical Fiction's first book is *Tipping the Velvet* at £53.74, UPC `90fa61229261140a`; the home page's last book is *It's Only the Himalayas*.
- **Data.** Raw results are in [validation/capability-loading/rollout-2026-10-19/](validation/capability-loading/rollout-2026-10-19/). [analyze_rollout.py](validation/capability-loading/analyze_rollout.py) adds o200k estimates per request from the controller artifacts. [summarize_discovery.py](validation/capability-loading/summarize_discovery.py) produces the rates.

### Turns that need no MCP tool

| Model | Turn | First request off → on | Whole turn off → on (requests) | Correct |
| --- | --- | --- | --- | --- |
| luna | greeting | 22,072 → 16,542 (−25%) | 43,864 (2) → 16,542 (1) | both |
| luna | code question | 22,113 → 16,583 (−25%) | 95,965 (4) → 63,804 (3) | both, 28,000 |
| luna | example.com, 5 runs each | — | 45,102 → 34,052 (−24%), 11.2 → 9.7 s | 5/5 both |
| nemotron | greeting, 3 runs each | 22,779–24,370 → 16,835–17,778 | 1 request each | all |
| nemotron | code question | 22,820–23,763 → 16,876–17,819 | 81,791 and 220,185 → 88,847, 66,946 and 61,763 | 2/2 off (+1 timeout) → 3/3 on |
| nemotron | example.com, 3 runs each | — | 47,375 → 41,395 (−13%) | 3/3 → 2/3 (1 timeout) |

The first request is about 5,500 tokens smaller on both providers, as in the single live run. The greeting's extra request with the flag off is the known text-reply retry, not the flag. "example.com" never needed a browser: with the flag off both models used the `fetch` MCP server, and with it on both used the built-in `web_fetch`. Deferring the `fetch` server changed the route but not the answer. No model loaded a namespace on a turn that didn't need one.

### First turns that need a browser (discovery)

| Model | Prompt | Arm | Runs | Used browser | Correct | Loads per turn | Unloaded refusals | Requests | Input (uncached) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| luna | "Open books.toscrape.com in the browser and tell me how many book categories…" | off | 15 | 15 | 15 | 0 | 0 | 3.2 | 83,300 (26,417) | 16.7 |
| | | on | 15 | **12** | 12 | 1.5 | 5 | 4.6 | 94,325 (27,074) | 22.3 |
| luna | "Go to books.toscrape.com, open the Poetry category, and tell me the first book's title and price." | off | 5 | 5 | 2 | 0 | 0 | 5.6 | 171,776 (29,005) | 28.8 |
| | | on | 5 | 4 | 4 | 1.6 | 2 | 7.8 | 184,031 (30,149) | 41.1 |
| luna | "Take a screenshot of books.toscrape.com for me." | off | 5 | 5 | 5 | 0 | 0 | 2.8 | 64,667 (20,046) | 15.4 |
| | | on | 5 | 5 | 5 | 1.0 | 0 | 4.2 | 84,697 (14,118) | 20.5 |
| nemotron | explicit browser count | off | 3 | 2 | 2 | 0 | 0 | 3.0 | 62,074 | 171.0 |
| | | on | 3 | 3 | 3 | 1.0 | 1 | 4.7 | 100,062 | 83.2 |
| nemotron | Poetry first book | off | 3 | 3 | 1 | 0 | 0 | 10.3 | 203,749 | 340.2 |
| | | on | 3 | 1 | 2 | 0.33 | 0 | 7.3 | 146,385 | 199.2 |
| nemotron | screenshot | off | 3 | 3 | 3 | 0 | 0 | 2.3 | 55,751 | 19.9 |
| | | on | 3 | 2 | 2 | 1.0 | 0 | 6.0 | 96,282 | 216.4 |

NIM reports no cached tokens, so nemotron's input is all uncached. Nemotron's failures in both arms are 420 s or 600 s timeouts on an overloaded endpoint (3 of 12 turns per arm), plus its pre-existing malformed output (below). The Poetry prompt does not require a browser; "Correct" counts a right answer from `web_fetch` too. Flag-off luna read the home page instead of the Poetry page twice: a model error, not the flag.

- **Discovery failures (luna, flag on).** Asked to "Open books.toscrape.com in the browser…", luna answered without loading Playwright in 3 of 15 discovery runs, and again in the first turn of the compaction session (4 of 16). It replied "I can't open an interactive browser in this session", or "browser access isn't available in the tools currently loaded". Once it also gave the count from memory. The model's own thought shows what it checked: "This is a live website lookup, not frontend work in this repository; no listed skill matches." It checked the skills catalog and stopped there. The `OPTIONAL CAPABILITIES` directory sits directly after that catalog, at character 46,266 of a 65,874-character system prompt. With the flag off it found the browser 16 of 16 times. Nemotron loaded Playwright every time it reached a browser-needing step (no misses in 7 runs; one flag-on screenshot run timed out before any tool call, so that run can't be judged).
- **Loader selection misses the obvious tools.** `select_tools` scores a query by word overlap with each tool's name *and* description, without weighting the name, so long descriptions that mention "page", "browser" or "click" outrank short ones. luna's query `"browser navigate inspect click page"` returned `browser_navigate_back`, `browser_click`, `browser_close`, `browser_emulate_media`: neither `browser_navigate` nor `browser_snapshot`. The model then calls the missing tool (refused as unloaded) or loads again by name. That is why luna averages 1.5 loads and up to 3 per browser turn, accounts for every luna unloaded refusal in the table (7 across 25 browser turns), and adds most of the extra requests and seconds. Loads by exact `tool_names` always returned what was asked for.
- **Whole-turn cost on browser turns.** luna: +13% input on the explicit prompt (+5.6 s), +7% on the Poetry prompt (+12.3 s), +31% on the screenshot (+5.1 s). Uncached input is flat or lower (−30% on the screenshot), because every request starts from the smaller prefix. In the main suite's same-thread follow-up ("Open that book's page…"), the tools were already loaded and the turn cost 90,659 tokens with the flag on against 113,182 off (−20%).

### Denied gate, follow-up and compaction

- **Denied `mcp_tool` gate.** luna in both arms: one `browser_navigate` gate, rejected; the model stopped and said so without retrying (flag on: one extra `read_skill` and one load before it, 91,621 against 45,130 tokens). Nemotron flag off: rejected navigate, then tried the `fetch` server (also rejected), then explained. Nemotron flag on: rejected navigate and screenshot, then asked a clarifying question. The harness's "Use your best judgment" answer led it to retry both denied tools (rejected again), try `fetch` (rejected), and then probe the shell for a local browser (`which chromium … google-chrome`, allowed under `allow_all`) until the 600 s cap. The retry after the clarification is a harness confound. Probing the shell after a denial is a behavior to watch, but one flag-on run doesn't show it comes from the flag. No run executed a denied tool, and loading never bypassed a gate.
- **Follow-up turn in the same thread** (main suite, "Open that book's page. How many copies are in stock?"): both models answered 22 in both arms. luna reused the loaded tools with no reload.
- **Compaction, six browser turns at a 45k window.**

| Model | Arm | Compaction events | Loads | Unloaded refusals | Correct turns | Input (sum of turns) |
| --- | --- | --- | --- | --- | --- | --- |
| luna | off | 7 | 0 | 0 | 3/6 | 680,037 |
| luna | on | 2 | 5 | 2 | 5/6 | 611,211 |
| nemotron | off | 7 | 0 | 0 | 4/6 (1 timeout) | 513,446 |
| nemotron | on | 0 | 1 | 0 | 1/6 | 396,169 |

  luna, flag on: turns 4 and 6 began after a compaction that evicted the bundle. The first browser call was refused as unloaded, the model reloaded with one `load_capabilities`, and it answered correctly. Recovery costs about one extra request and works without restoring bundles. Turn 1 is the discovery miss above. Flag-off luna's wrong answers came from truncated `browser_snapshot` output (counting 36 visible categories, listing 6 of 11 titles). Flag-on luna happened to use `browser_find`/`browser_evaluate`, which return less. The smaller prefix also compacted 2 times instead of 7.

  Nemotron, flag on: the session never compacted. Turns 2–6 failed: three timeouts on NIM, and two turns ended after four malformed `tool_calls` in a row. The malformed actions are a pre-existing echo, not the flag: the loop stamps `batch_id` on each persisted `tool_calls` action (`controller_loop.py` `persisted = {**resp, "batch_id": …}`), nemotron copies it into its next action, and validation rejects the extra field as "tool_calls cannot be mixed with another action". Across every nemotron run, 21 of 77 flag-off and 17 of 76 flag-on `tool_calls` actions carried it. This session drew 11 of them. Compaction recovery is untested on nemotron.

### Against the spec's criteria

| Criterion | luna (ChatGPT plan) | nemotron (NIM, prompt-only JSON) |
| --- | --- | --- |
| No failure to discover or use required browser tools | **Fails**: 4 of 16 explicit browser requests answered without the browser (0 of 16 with the flag off) | No miss in 7 runs where a browser step was reached; 1 run unjudgeable (timeout) |
| Lower initial payload on non-MCP turns | Passes: −5,530 tokens (−25%) per request | Passes: about −5,500 tokens per request (−25% estimated) |
| No loss of correctness | Fails through the discovery misses; otherwise equal or better | Not established: compaction session 1/6 on vs 4/6 off; failures are endpoint timeouts and the pre-existing `batch_id` echo, and 3 runs per cell can't separate those from the flag |
| Whole-task regressions visible and assessed | Browser turns +7% to +31% input and +5 to +12 s, mostly from re-loads caused by query selection; non-MCP turns −24% (example.com) and −33% (code question) | Browser turns ran slower on NIM in both arms; too noisy to attribute |

### Recommendation: no-go for default

Keep `CRUCIBLE_CAPABILITY_LOADING_ENABLED` off by default, on every model.

- **luna: no-go.** The spec says a discovery failure blocks rollout, and luna missed the browser in a quarter of the requests that named it, then told the user browser access was unavailable. That is a correctness loss the flag-off arm never shows.
- **nemotron: inconclusive, not a go.** It discovered the browser every time it got that far, but the free NIM endpoint timed out on 3 of 12 discovery turns per arm, and the pre-existing malformed `tool_calls` echo dominated the compaction session.

Before re-running this evaluation, the measurable fixes are:
1. Rank loader queries by tool name before description, so `"browser navigate …"` returns `browser_navigate`. This removes most re-loads, refusals and the whole-turn cost on browser turns.
2. Make an unloaded capability impossible to read as "unavailable". For example, name the deferred namespaces where the model checks what it can do (next to TOOLS rather than after the skills catalog), and say plainly that a listed namespace is available to load.
3. Fix the `batch_id` echo in persisted `tool_calls` (independent of this feature) and run nemotron again on a less loaded endpoint.

Then repeat at least the discovery suite (≥15 runs per arm) on each model meant to get the default. The per-model rollback (flag off) stays as it is.

## Re-runs after the fixes (2026-10-19, luna)

Two re-runs, each with the discovery suite ×5 plus 10 more of the explicit-browser prompt (15 per arm), both arms in parallel, same harness, workspace and settings as above. The tree also held two concurrent changes, which both arms ran with: the loop drops a model-echoed `batch_id` (51bd64d6), and the MCP tool-catalog size cap was removed (the flag-off catalog was already under it). Raw results: [validation/capability-loading/rerun-2026-10-19/](validation/capability-loading/rerun-2026-10-19/), one folder per run.

### What changed

Kept in the final design:
- **Directory placement and wording** (`chat/controller_prompts.py`). `OPTIONAL CAPABILITIES` now sits directly after the tool catalog, ahead of web guidance, memory, AGENTS.md and the skills catalog. It opens "available now, loaded on demand" and says the listed servers' tools are available in this turn. It also tells the model to check these namespaces before telling the user something is out of reach.
- **Whole namespace by default** (`_CAPABILITY_BLOCK`, the `load_capabilities` description). The model loads a namespace by name alone and gets every tool. `query` and `tool_names` are offered only for a retry after a `bundle_too_large` refusal, and that refusal now says to retry with them. All three servers fit whole: Playwright's 25 tools come to 21,428 characters with guidance, against the 32,000 limit. `select_tools` is unchanged and remains the fallback path.
- **The directory names every tool** (`capabilities/catalog.py`, cap 10 → 40). With 10, Playwright's line stopped at `fill_form, +15 more` and hid navigate, snapshot and click.

Tried and dropped: name-first query ranking in `select_tools`. Run 1 below measured it. It cut luna's unloaded refusals from 7 to 1 in 25 browser turns. Whole-namespace loading removes the query from the normal path altogether.

The flag-off prompt is byte-identical (the goldens are unchanged). With the flag on, the first request's system prompt is 66,676 characters against 91,006 off (−27%); the fuller directory and the longer block add about 800 characters over the first evaluation.

### Run 1: wording + name-ranked queries (dropped design)

| Prompt | Arm | Runs | Used browser | Correct | Loads per turn | Unloaded refusals | Requests | Input (uncached) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Explicit browser count | off | 15 | 15 | 15 | 0 | 0 | 3.3 | 84,550 (23,400) | 20.1 |
| | on | 15 | 15 | 15 | 1.07 | 1 | 4.1 | 89,230 (25,785) | 23.7 |
| Poetry first book | off | 5 | 5 | 3 | 0 | 0 | 6.2 | 182,946 (39,790) | 39.6 |
| | on | 5 | 5 | 3 | 1.0 | 0 | 9.4 | 262,453 (47,490) | 54.0 |
| Screenshot | off | 5 | 5 | 5 | 0 | 0 | 3.0 | 69,441 (16,090) | 18.1 |
| | on | 5 | 5 | 5 | 1.0 | 0 | 4.0 | 79,563 (16,587) | 22.7 |
| example.com | off | 5 | 0 | 4 | 0 | 0 | 1.8 | 40,494 (14,638) | 11.8 |
| | on | 5 | 0 | 5 | 0.6 | 0 | 3.4 | 61,561 (20,293) | 18.5 |

Most loads were by exact `tool_names`, taken from the now-complete directory (16 of 29). The one refusal came from the query "browser navigate page and inspect visible content": word matching can't link "inspect" to `browser_snapshot`. On example.com, luna loaded the `fetch` server in 3 of 5 runs. Each time it first called a made-up `mcp.fetch__fetch` (the directory's namespace glued onto the call syntax), was refused with "tool must be visible and args must be an object", and recovered with one load.

### Run 2: wording + whole-namespace loads (final design)

| Prompt | Arm | Runs | Used browser | Correct | Loads per turn | Unloaded refusals | Requests | Input (uncached) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Explicit browser count | off | 15 | **13** | 13 | 0 | 0 | 2.7 | 70,151 (14,181) | 14.4 |
| | on | 15 | **14** | 14 | 0.93 | 0 | 3.8 | 87,524 (27,773) | 19.8 |
| Poetry first book | off | 5 | 5 | 5 | 0 | 0 | 6.0 | 181,563 (27,425) | 34.9 |
| | on | 5 | 5 | 5 | 1.0 | 0 | 7.2 | 211,757 (38,317) | 37.6 |
| Screenshot | off | 5 | 5 | 5 | 0 | 0 | 3.0 | 69,428 (11,905) | 17.9 |
| | on | 5 | 5 | 5 | 1.0 | 0 | 4.0 | 87,174 (26,656) | 22.7 |
| example.com | off | 5 | 0 | 5 | 0 | 0 | 2.0 | 45,111 (1,949) | 12.0 |
| | on | 5 | 0 | **3** | 0 | 0 | 1.6 | 27,328 (8,333) | 8.6 |

- **Loading works as intended.** All 24 loads gave the namespace alone, none hit `bundle_too_large`, and there were no unloaded refusals or malformed corrections in either arm. The `mcp.fetch__fetch` error from run 1 did not recur: on example.com luna used the built-in `web_fetch` and loaded nothing.
- **Discovery: the flag no longer makes it worse.** luna skipped the browser in 1 of 15 explicit requests with the flag on, and in **2 of 15 with it off**. The three misses look the same. On the first request the thought checks the skills catalog ("neither frontend-design nor impeccable applies"), and the model answers with no tool call: "I can't open an interactive browser in this session". Flag-off luna did this with every Playwright schema in its catalog, so this is luna's own first-step behavior, not a missing capability. Across both re-runs the explicit prompt used the browser in 29 of 30 turns with the flag on and 28 of 30 with it off, against 12 of 15 on before the fixes.
- **New: 2 of 5 flag-on example.com answers declined without a tool.** One said "the web-fetch tool requires external-access approval, which hasn't been granted"; the built-in `web_fetch` needs no approval. Both thoughts again checked only the skills. Flag off answered 5 of 5, and flag on answered 5 of 5 in run 1 and in the first evaluation, so n = 5 can't separate this from the same first-step variance. It is possible that the block's sentence about approval for external tools primes the claim. Watch this in the next run.
- **Cost on browser turns rose.** Counting only turns that used the browser: explicit +19% input (92,579 vs 77,542) and +5.0 s, screenshot +26% and +4.8 s, Poetry +17% and +2.7 s. Uncached input roughly doubles, because the request right after the load is the first to carry the new bundle. The full Playwright bundle (about 21k characters) is resent with every later request of the turn. In run 1 the bundles held 2 to 4 tools, and the cost was +6% (explicit) and +15% (screenshot). Non-MCP turns keep the −27% system-prompt saving.

### Updated recommendation

- **luna: meets the spec's criteria with the final design.** No discovery loss attributable to the flag (14 of 15 on vs 13 of 15 off; 29 of 30 vs 28 of 30 across both re-runs), equal correctness on every browser prompt, zero refusals and zero malformed loads. The trade-off is +17% to +26% input and +3 to +5 s on the first browser turn of a thread, against −27% on every request of a turn that needs no MCP tool. Later turns in the same thread reuse the loaded bundle, as shown above.
- **Default: still no-go globally.** The flag is one process-wide setting, and nemotron has not been re-run with these fixes or the `batch_id` fix. The compaction suite, where luna's fourth discovery miss happened, has not been re-run either.
- **Before flipping the default:**
  1. Re-run the compaction suite on luna.
  2. Re-run discovery on nemotron, on a less loaded endpoint.
  3. Re-check the example.com prompt (n ≥ 15) for the "web fetch needs approval" refusal.

## Pre-default checks (2026-10-20)

The three checks the updated recommendation listed, run on the final design (c6bcc067) with the same harness and workspace. Raw results: [validation/capability-loading/checks-2026-10-20/](validation/capability-loading/checks-2026-10-20/).

- **Account.** The evaluation's ChatGPT registration had signed out, so luna ran on another signed-in account (`reg_753b274021f0`; `eval_rollout.py --chatgpt-registration`). Same model (`gpt-6-luna`), effort and route.
- **Arms.** The flag-off luna arm was stopped after the compaction session and 3 example.com runs. The flag-off prompt is byte-identical to the earlier runs, so the earlier flag-off results are the control for the rest.
- **Nemotron: not run.** NIM held requests open with no tokens until the 600 s turn cap: 2 of 3 turns timed out (one flag-on, right after a successful `load_capabilities`). It was stopped after 2 and 1 turns, which is too few to judge.

### Compaction, six browser turns at a 45k window (luna)

| Arm | Correct turns | Compactions | Loads | Unloaded refusals | Requests | Input (sum of turns) |
| --- | --- | --- | --- | --- | --- | --- |
| off (today) | 3/6 | 10 | 0 | 0 | 37 | 764,931 |
| on (today) | 5/6 | 6 | 9 | 6 | 39 | 798,237 |
| off (2026-10-19) | 3/6 | 7 | 0 | 0 | — | 680,037 |
| on, query loader (2026-10-19) | 5/6 | 2 | 5 | 2 | — | 611,211 |

**Passes.** Turn 1 used the browser (the earlier fourth discovery miss happened here). Every refusal came after a compaction that evicted the bundle, and each one ended in a reload and a correct answer. Both arms missed the Travel turn the same way, on a truncated `browser_snapshot`. Flag-off also gave the wrong Historical Fiction book, and then the wrong UPC. Whole-namespace loads make eviction cost more: 9 loads and 6 refusals against 5 and 2 with the query loader, and +4% input over flag-off. 45k is an artificial window; luna runs at 272k.

### "what does example.com say right now?" (luna)

| Arm | Runs | Answered from the page | Declined without a tool call | Route when answered |
| --- | --- | --- | --- | --- |
| off (today + 2026-10-19 runs) | 3 + 10 | 2 + 10 | 1 + 0 | `fetch` MCP server |
| on (today) | 15 | **8** | **7** | `web_fetch` 4, load `mcp.fetch` then fetch 4 |
| on (2026-10-19, both rounds) | 10 | 8 | 2 | |

**Fails.** Flag-on luna declined 7 of 15 times ("I can't access the live site from this chat right now"), against 1 of 13 with the flag off. In 6 of the 7, its first step was the same as luna's discovery miss: the thought checks the skills catalog ("no installed skill applies"), and it answers without calling any tool. The built-in `web_fetch` was in its catalog every time.

The seventh shows a second, fixable failure. **Luna never called `load_capabilities` first.** In all 5 runs where it reached for the `fetch` server, its first action called a name it built from the directory line: `mcp.fetch__fetch` (3 runs) or `mcp.fetch.fetch` (2), gluing the namespace onto the tool name (run 1 saw the same thing). The batch validator refused each with the generic "tool must be visible and args must be an object", which never says the namespace must be loaded first. In 4 of the 5 runs, luna then loaded `mcp.fetch`, called `mcp__fetch__fetch` and answered, one load and one refused call more than needed. In the fifth it answered that the fetch "was rejected".

### Fixes and rerun

Three changes, then the flag-on example.com prompt ×15 again (same account, workspace and harness):

1. **Namespace-aware refusal** (`capabilities/history.py::misnamed_reason`, checked in `ControllerLoop` before batch validation). A call to `mcp.<server>__<tool>`, `mcp.<server>.<tool>`, `mcp.<server>` or `mcp__<server>` for a deferred server is refused with "`mcp.fetch__fetch` is not a tool name: `mcp.fetch` is a namespace. Call load_capabilities with namespace "mcp.fetch" alone in its step, then call the tool as `mcp__fetch__fetch`." The generic "tool must be visible" no longer applies to these calls.
2. **Real call names in the directory** (`capabilities/catalog.py::render_directory`). The line reads `- mcp.fetch (1 tool): mcp__fetch__fetch` instead of `fetch`, and the block tells the model to call loaded tools by the names on that line, and that a call made before the load is refused. This adds about 800 characters for the three servers.
3. **The block no longer says that loading grants no permission** ("…every call to an external tool still pauses for the user's approval…"). The same guidance arrives with every loaded bundle (`_MCP_BLOCK`), and the loader's description still says it.

**Replay first.** The captured flag-on first step of a declined run was resent to luna 8 times per arm, interleaved, on a fresh transport per trial ([replay-first-step.jsonl](validation/capability-loading/checks-2026-10-20/replay-first-step.jsonl)). With changes 1 and 2: `web_fetch` 5, a misnamed or unloaded fetch-server call 2, no-tool answer 1. With change 3 added: `web_fetch` 8 of 8. The live result before the changes is the reference (7 of 15 no-tool), so no unchanged arm was replayed.

**Live rerun, flag on, all three changes:**

| Runs | Answered from the page | Declined without a tool call | Route |
| --- | --- | --- | --- |
| 15 | **14** | **1** | `web_fetch` 10, `web_search` then `web_fetch` 1, load `mcp.fetch` then fetch 3 |

The misnamed calls still happened (`mcp.fetch__fetch`, `mcp.fetch.fetch`), along with one call to `mcp__fetch__fetch` before loading. Each got the new refusal, loaded `mcp.fetch` in the next step and answered. The one decline is the same no-tool first step that flag-off luna showed in 1 of 13 runs. Turns averaged 2.5 requests, 45,536 input tokens and 12.1 s. Two other refusals were `web_search` argument mismatches, unrelated to the flag. Raw: [luna-examplecom-on-fixed.json](validation/capability-loading/checks-2026-10-20/luna-examplecom-on-fixed.json).

### Result

- **Compaction:** passes (5/6 flag-on vs 3/6 flag-off). Run before the three changes, which touch only the directory, the block and the refusal text.
- **example.com:** passes after the changes. 14 of 15 answered with the flag on, against 12 of 13 off. Before the changes it was 8 of 15.
- **Discovery on luna:** passed earlier (29 of 30 on vs 28 of 30 off).
- **Nemotron:** not evaluated on this design. NIM timed out, and the run was stopped by decision. The prompt-only JSON path is untested with the flag on.

### Tool descriptions and server summaries in the directory

Names alone tell the model nothing about tools like `trigger-resource-update` or a server's generic `search`. Each directory line now carries the first sentence of the tool's description, capped at 120 characters. Each server header also carries the first prose sentence of the server's own MCP `instructions` (`InitializeResult.instructions`, kept by `McpConnectionManager.server_instructions()`), when the server sends them; Markdown headings are skipped. Of the three servers, only `everything` sends instructions. The directory grows from about 1,600 to 4,009 characters, about 600 tokens, against the roughly 5,500-token first-request saving. The full rendering is in [directory-with-descriptions.txt](validation/capability-loading/checks-2026-10-20/directory-with-descriptions.txt).

**Replay** (the current captured first step with only the directory swapped, 8 trials per prompt):

| Directory | Prompt | Tried a tool | No-tool decline | MCP call before loading |
| --- | --- | --- | --- | --- |
| names only (shipped before) | example.com | 8/8 | 0 | 0 |
| with descriptions | example.com | 8/8 | 0 | 2 (`mcp.fetch__fetch`) |
| with descriptions | explicit browser | 8/8 | 0 | 3 |
| with descriptions + "not loaded: load_capabilities first" header | explicit browser | 8/8 | 0 | 0 |
| with descriptions + "not loaded…" header | example.com | 5/8 (+1 error) | **2** | 0 |
| with descriptions + an MCP-scoped line above the directory (below) | explicit browser | 8/8 | 0 | 0 |
| with descriptions + the MCP-scoped line | example.com | 5/8 | **3** | 0 |

Descriptions make the lines read as callable, so the model sometimes calls before loading. The refusal recovers that every time, at the cost of one extra request. A "not loaded" header stopped those calls on the browser prompt, but brought back the declines on example.com ("not loaded" reads as "not available"), so it was not adopted. The same happened with a line placed directly above the directory: "The MCP tools listed below are names and descriptions only: their schemas are not loaded, so calling one of them now is refused. When a request needs one, call load_capabilities with its namespace, then call the tool using the loaded schema. Tools in TOOLS above are already loaded and callable directly." Every browser run loaded first, but 3 of 8 example.com runs declined ("If you share the page text or enable a web fetch…"), with `web_fetch` in TOOLS. Telling luna that something can't be called yet makes it more likely to say it can't fetch at all. An early call costs one request, and the refusal recovers it; a decline is a wrong answer. So neither wording was adopted. Raw: [replay-descriptions-mcp-scoped-line.jsonl](validation/capability-loading/checks-2026-10-20/replay-descriptions-mcp-scoped-line.jsonl), [replay-descriptions.jsonl](validation/capability-loading/checks-2026-10-20/replay-descriptions.jsonl), [replay-descriptions-not-loaded-header.jsonl](validation/capability-loading/checks-2026-10-20/replay-descriptions-not-loaded-header.jsonl).

**Live, flag on, headed Playwright** (the eval workspace's `mcp.json` without `--headless`), discovery suite ×4 plus an open → snapshot → screenshot thread:

| Prompt | Runs | Used browser | Correct | Loads per turn | Calls before load | Requests | Input | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| explicit browser count | 4 | 4 | 4 | 1.0 | 0 | 4.0 | 95,840 | 18.0 |
| Poetry first book | 4 | 4 | 4 | 1.0 | 1 | 6.0 | 173,408 | 32.2 |
| screenshot | 4 | 4 | 4 | 1.0 | 1 | 4.2 | 95,373 | 19.3 |
| example.com | 4 | 0 | 4 | 0.25 | 1 | 2.5 | 45,913 | 12.2 |

16 of 16 were correct, with no declines and no misnamed calls; each of the 3 calls made before loading was refused and recovered. In the three-turn thread, luna loaded Playwright once in turn 1 (`browser_navigate`), then reused it for `browser_snapshot` (correct first three categories) and `browser_take_screenshot`. Input per turn: 85,013, 61,700 and 74,239. Raw: [luna-discovery-on-desc.json](validation/capability-loading/checks-2026-10-20/luna-discovery-on-desc.json), [luna-actions-on-desc.json](validation/capability-loading/checks-2026-10-20/luna-actions-on-desc.json).

**Decision (2026-10-20): default ON**, by user decision, with the kill-switch kept (`CRUCIBLE_CAPABILITY_LOADING_ENABLED=0`, or `crucible.performance.mcpOnDemand: false`). The evidence covers luna on the ChatGPT plan (native function calling) only. **Nemotron and every other provider are untested with the flag on**, including the prompt-only JSON path (`openai_compatible`, Ollama, Gemini, Anthropic and the rest). If a model there misses tools it should find, turn the flag off first.

## More servers, a large GitHub server and reasoning effort (2026-10-20, luna, flag on)

The eval workspace's `.crucible/mcp.json` grew from three servers to seven: `time` (`uvx mcp-server-time`, 2 tools), `git` (`uvx mcp-server-git --repository <workspace>`, 12), `sqlite` (`uvx --with 'mcp<2' mcp-server-sqlite --db-path data/shop.db`, 6), and GitHub's remote server with every toolset, read-only (`https://api.githubcopilot.com/mcp/x/all/readonly`, `Authorization: Bearer ${GITHUB_PAT}`, 63). That makes 125 MCP tools. The archived `mcp-server-sqlite` fails at startup against the current `mcp` SDK (`'Server' object has no attribute 'list_resources'`), so it is pinned below 2. `data/shop.db` is a 6-customer, 60-order sample, committed to the workspace's git as "Add sample shop database for order reports" by Priya Menon. Harness suites `more_mcp` and `github_mcp`, plus a new `--effort` option; raw results are in [validation/capability-loading/more-servers-2026-10-20/](validation/capability-loading/more-servers-2026-10-20/). Only the flag-on arm was run.

**Prompt size.** All 125 schemas in the catalog would come to about 103,000 characters. The flag-on directory is 9,328. The first request of a greeting turn is 17,819 tokens with six servers and 18,744 with seven, against about 17,100 with the original three.

### Six servers, medium effort, 3 runs per prompt

| Prompt | Server | Correct | Loads per turn | Requests | Input | Seconds |
| --- | --- | --- | --- | --- | --- | --- |
| "What time is it in Tokyo right now?" | time | 3/3 | 1.0 | 4.0 | 77,888 | 19 |
| "Who made the most recent commit in this repo, and what did it add?" | git | 3/3 | 1.0 | 5.0 | 100,031 | 30 |
| "Which customer has spent the most in the shop database?" | sqlite | 3/3 | 1.0 | 6.7 | 134,587 | 37 |
| Top-spending customer, then the current time in their city | sqlite, then time | 2/3 | 1.3 | 6.0 | 131,588 | 41 |

Twelve refusals all recovered: 3 misnamed calls (`mcp.sqlite__list_tables`, which got the namespace refusal), 5 calls before loading, and 4 calls whose arguments failed the tool's schema. In two of the three two-server runs, luna loaded `mcp.sqlite`, then `mcp.time`, and answered "Dana Kim, Seoul" with the time. The miss ended the turn with an `answer` saying what it would do ("I'll inspect the available data"), with no tool call.

### GitHub (63 tools), medium against high effort

Questions about `pradeepA2125/crucible`: its latest release, its Actions workflows, and the latest commit message on `main`. The last one can only be answered from GitHub, because the local `git` server's newest commit is a different one.

| Effort | Bundle limit | Turns | Correct | No-tool first step | Requests | Input | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| medium | 32,000 characters | 9 | 5 | 4 | 2.9 | 62,714 | 18 |
| medium | 20% of the window (50k on luna) | 2 | 1 | 1 | 2.0 | 63,205 | 12 |
| **high** | 20% of the window | 9 | **9** | **0** | 3.0 | 93,341 | 21 |

- **Under the old 32,000-character limit**, the whole GitHub bundle (67,931 characters, about 15,700 tokens) was refused. Every load then narrowed by `tool_names` (`get_latest_release`, `mcp__github__list_commits`) or by `query` ("list GitHub Actions workflows for a repository") and returned the right tool. Under the token limit it loads whole in one step: the release prompt took 3 requests and 18 s, down from 5 requests and 36 s.
- **At medium effort, 5 of 11 GitHub turns ended at the first step without a tool call.** The thought named the right source each time ("I should query the repository's workflow listing directly", "GitHub tools can retrieve it"), while the action was an `answer`: "I'll check the repository's `main` branch…", or "I need access to GitHub to check the repository's current Actions workflows". `mcp__github__actions_list` and its description were in the directory.
- **At high effort, all 9 turns loaded `mcp.github` whole, called the right tool and answered correctly**, with no refusals or misnamed calls. Output rose about 85 tokens per turn and time 2–3 s. (The `main` commit moved during the run; every high-effort answer matched the commit that was latest at that time.)
- **Not established:** whether the flag raises the medium-effort decline rate. Flag-off was not run on these prompts. Luna's 1-in-13 to 1-in-15 no-tool rate on simpler prompts appears with or without the flag.

**Wording matters more than the flag for bare requests.** "open linkedin" made no tool call in 0 of 8 replays with the flag on and 0 of 8 off, nor live in either arm: luna read it as acting on the user's own device. "open linkedin in the browser" loaded Playwright, navigated and opened the page.
