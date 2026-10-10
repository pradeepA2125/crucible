# Controller latency implementation plan

**Goal:** Reduce model round trips and unnecessary context while preserving correctness, approvals, edit safety, and support for smaller models. Implement priorities **1 → 2 → 3**, measuring each independently before combining them. This document is a plan; implementation has not started.

**Baseline:** [Measured local smoke](../../controller-performance-smoke-2026-10-10.md) and [profiler usage](../../controller-performance.md). The Node.js question took 69.812s, six model requests, one search, three fetches, and one skill read. Provider requests plus retry backoff accounted for 89.1%; prompt/payload construction took approximately 33ms. Later requests had 91–95% cached input. This is one diagnostic run, not a controlled comparison with Codex.

## User observations to preserve

The user reports that early harness development imposed small read limits and truncation to support smaller models, and prompts specifically encouraged reading small parts. This can force adjacent or overlapping reads and add a model round trip for each fragment. The optimization must address **both execution limits and model instructions**, rather than only changing a numeric cap or enabling parallelism.

Current repository evidence confirms these restrictions are still present:

- `agentd/tools/files.py`: up to 2,000 selected lines, followed by a 100,000-character cap.
- `agentd/tools/registry.py`: applies a second 500-output-line cap after the file reader; its tool description advertises 500 lines. Its truncation count describes the already bounded output, not necessarily the actual file length.
- `agentd/chat/controller_prompts.py`: requires ranges on files over 150 lines.
- `agentd/tools/loop.py` and `agentd/tools/verify_phase_sm.py`: teach 100–200-line windows and warn against whole-file reads because of the 500-line cap.
- `agentd/tools/loop.py`: repeated-call correction suggests shifting ranges if the previous result was truncated from history; this can encourage overlapping rereads instead of restoring missing evidence.
- The legacy tool loop independently slices model-facing results using `CRUCIBLE_TOOL_RESULT_MAX_CHARS` (default 100,000). The controller currently appends `out.output` to model history directly.
- `agentd/chat/tool_events.py`: separately truncates persisted UI previews to 4,000 characters. That preview is not the controller's model-facing history. Its comment about matching the model result default is stale; changing preview size alone will not reduce model rereads.

These are verified current behaviors. The historical reason for introducing them is the user's account; the additional latency caused by fragmented reads still needs a coding-task baseline.

All `agentd/` and Python test paths below are relative to `services/agentd-py/`; extension/UI paths are relative to `apps/vscode-extension/`.

## Scope and invariants

- Apply controller changes to main agents, controller-driven children, and teams. Audit legacy execution/verification loops for the same read-policy restrictions so different paths do not teach conflicting behavior.
- Preserve serial behavior for edits, shell commands, skill activation, todo/team state changes, and unknown MCP tools. Do not infer concurrency safety merely from a tool name.
- Preserve child tool filters, workspace/shadow-root boundaries, read-before-edit checks, network approvals, origin restrictions, timeouts, and cancellation.
- Keep stable prompt prefixes and append-only history where possible. Do not rebuild the retrieval seed every iteration or reorder committed history.
- Keep file/result and aggregate request budgets bounded. Larger reads must be proportional to available context; smaller models retain conservative budgets.
- Do not rewrite Python or optimize its millisecond-scale prompt formatter as a primary latency project.
- Approval-policy changes, model/provider migration, retry-policy changes, and automatic search-and-crawl are separate follow-ups. This plan may measure them but does not silently change them.
- Keep existing single-action engines and saved conversations compatible. Add initially opt-in rollout flags `CRUCIBLE_TOOL_BATCHING_ENABLED`, `CRUCIBLE_ADAPTIVE_READS_ENABLED`, and `CRUCIBLE_COMPACT_CONTEXT_ENABLED`, with a serial/conservative fallback. Expose managed settings through the existing settings/environment mapping when promoting them.

## Foundation: baseline and attribution

**Files:** `agentd/performance.py`, `scripts/profile-controller.py`, `agentd/chat/controller.py`, `agentd/reasoning/engine.py`, focused performance tests; new benchmark documentation/runner under `scripts/` as needed.

- [ ] Add numeric per-request context accounting: system prompt, tool catalog/schema, retrieval seed, active skills, history/results, and dynamic instructions. Label estimates versus provider-reported token counts. Record counts/sizes only, not content.
- [ ] Extend timing to retrieval and controller setup before `ControllerLoop.run`; the current 69.812s capture excludes those steps. Keep controller-only and end-to-end backend measurements distinct.
- [ ] Record model requests, retries, tool calls, file paths/ranges through opaque stable identifiers, delivered lines/characters, truncations, overlap/repeated-read counts, and concurrency high-water marks. Keep source text and private paths out of performance records; normalize profiler function paths to repository-relative or library identifiers.
- [ ] Establish fixed fixtures/scenarios: the Node.js question; a code explanation involving several independent files; a change involving a long function beyond line 500; a small-context-model version of the coding task.
- [ ] Use deterministic fake transports/tools for scheduling assertions, and repeated live runs for latency. Keep model, effort, provider, workspace snapshot, task, and approval policy fixed; record cache state and retries separately. Aim for at least five live runs per comparison when feasible, reporting median and range rather than claiming reliable p95 from a tiny sample.
- [ ] Establish answer/change quality checks before optimization: required evidence, citations, correct code change, relevant checks passing, and no omitted required reads. Use isolated benchmark workspaces for edit scenarios.

**Acceptance:** A baseline exposes request count, context composition, fragmented reads, provider latency, approval wait, and pre-controller overhead. Measurements distinguish cached/uncached and retry/no-retry runs.

## Priority 1: multiple tool calls and concurrent independent reads

**Primary files:** `agentd/providers/openai_native.py`, `agentd/providers/openai_transport.py`, `agentd/providers/contracts.py`, `agentd/reasoning/contracts.py`, `agentd/reasoning/engine.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller_loop.py`, `agentd/tools/sources.py`, `agentd/chat/controller.py`.

**Event/UI integration:** `agentd/chat/tool_events.py`, controller storage/models where needed, webview tool-event types/handlers and approval rendering. Locate the active handlers before editing; concurrent completion must not rely on a mutable "current tool".

### Task 1.1: define a provider-neutral batch contract

- [ ] Define a normalized tool batch with unique stable call IDs, tool names, arguments, and optional thought. Preserve the existing single `tool_call` contract unchanged.
- [ ] Structured-output providers get an optional `tool_batch` action; engines without batching support keep their current schema. The native OpenAI path normalizes multiple actual function calls into the same internal batch.
- [ ] Set an initial maximum of eight calls per batch and four concurrent executions. Count every tool call against the tool budget; a batch must not bypass iteration/deadline limits.
- [ ] Prevalidate the entire batch against current tool permissions, argument shape, duplicate IDs, available budget, and batch safety. Reject invalid batches before any side effect. A batch cannot mix tools with answer/edit/submit/state-transition actions.
- [ ] Define results by call ID with explicit success, failure, denial, or cancellation. Ordinary per-tool errors do not discard successful siblings. Cancellation of the turn cancels active work and closes its resources; it must not be swallowed by a broad exception handler.

### Task 1.2: support multiple native calls end to end

- [ ] Replace the OpenAI stream collector's single-call slot with an ordered collection preserving provider call IDs. Update decoding, history encoding, request continuation, and session-input bookkeeping together.
- [ ] Enable `parallel_tool_calls` only when the active schema/engine/controller supports the batch contract. Remove conflicting "exactly one action" instructions for this path.
- [ ] Commit all calls and corresponding outputs before the next model request, ordered deterministically by emission order rather than completion order. Keep durable partial results recoverable across stop/reload/restart.
- [ ] Preserve historical single-call fixtures and cached-prefix/session behavior. Do not simply flip the flag: the current decoder retains only the first call.

### Task 1.3: schedule eligible tools safely

- [ ] Add explicit batching eligibility metadata for known tools. Initially allow independent `read_file`, `list_directory`, `search_code`, and verified read-only retrieval/graph operations.
- [ ] Validate `web_search`/`web_fetch` independently of their execution and retain each call's existing approval gate. Once approved, independent network work can overlap. Verify multiple pending gates, denial, timeout, remembered rules, and child restrictions before enabling network batching.
- [ ] Keep `read_skill` serial because it activates prompt state. Keep commands, edits, team/todo actions, dispatch/wait operations, and unknown MCP tools outside concurrent batches.
- [ ] Execute eligible calls with bounded concurrency and tool/deadline limits. Permission checks and observer/read-watermark updates still apply per call.
- [ ] Give each live/durable tool pill a stable identity before execution. Out-of-order completions, progress-message boundaries, thread switching, and reload must update the correct pill.
- [ ] Prompt main/child/team agents to batch known independent reads; keep discovery-dependent reads sequential. Instruct them to decide needed resources together without inventing paths or arguments dependent on unseen results.

**Meaningful checks:** `test_openai_native.py`, `test_openai_transport.py`, `test_tool_registry_aggregator.py`, controller tool-event/durable-history/stop tests, permission tests, and focused new batch scheduler tests. Use barriers to prove overlap without flaky speed thresholds. Test mixed success/failure, cancellation, denied calls, bounded concurrency, duplicate IDs, budgets, two approval gates, serial fallback, and old saved histories. Add webview tests for out-of-order completion and gate routing; run extension/webview typechecks where affected.

**Acceptance:** Three independent eligible reads can execute in one model-request cycle and overlap; every result reaches the next request exactly once with its call ID. Stateful operations remain serialized. Equivalent coding tasks use fewer requests without quality or permission regressions.

## Priority 2: remove fragmented and redundant reads/steps

**Primary files:** `agentd/tools/files.py`, `agentd/tools/registry.py`, `agentd/tools/sources.py`, `agentd/tools/loop.py`, `agentd/tools/verify_phase_sm.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller_loop.py`, `agentd/prompting/web_research.py`, skill-loading/config seams as needed.

### Task 2.1: one bounded read policy, rather than stacked caps

- [ ] Introduce a shared read/result budget policy passed to the actual reader and model-history injection. Remove the registry's second 500-line slice. Do not substitute an unconditional unlimited read.
- [ ] Resolve a per-request character/token budget from configured context capacity, reserved generation/context headroom, and current occupancy. Use a conservative fallback when capacity is unknown; retain a hard character/byte ceiling for pathological files.
- [ ] For initial rollout, permit whole files that fit the effective budget and explicit ranges larger than 500 lines when they fit. Keep the existing 2,000-line/100,000-character values as initial hard ceilings until benchmark evidence justifies increasing them. Apply smaller effective budgets to smaller contexts.
- [ ] Enforce budgets while delivering complete numbered lines. Report actual file line count, requested range, delivered range, omitted range, and truncation reason. Avoid a misleading total calculated after an earlier cap or silent mid-line cuts.
- [ ] Preserve path traversal protections, shadow/live-root behavior, read observation, and edit safety. Do not count content omitted by truncation as evidence the model received.
- [ ] Give concurrent batches an aggregate result budget so eight individually valid reads cannot overfill one request. Allocate deterministic per-call shares and report any budget-limited continuation explicitly.

### Task 2.2: remove instructions that manufacture small chunks

- [ ] Update the `read_file` description and all main/child/team/legacy verification instructions. Replace the >150-line range rule and generic 100–200-line window rules with: read a complete relevant function/module when it fits, batch independent resources, and use targeted ranges when the file is genuinely too large or the task is local.
- [ ] Keep search-first guidance when the location is unknown, but allow direct reads of known paths and whole relevant files. Do not force a redundant search just to re-find a supplied location.
- [ ] Revise truncation guidance to request the missing region precisely. Do not direct models to shift ranges merely to evade duplicate-call blocking.
- [ ] Preserve grounding requirements: claims and edits still need the relevant source; broad reads are an option, not a requirement to load every file.
- [ ] Update prompt goldens and add assertions that obsolete 150/500-line and small-window instructions are absent from the changed paths. Keep explicit line-range support for targeted reads.

### Task 2.3: make rereads and skill steps purposeful

- [ ] Track delivered file revision/ranges, not just requested arguments, for reread diagnostics. Permit a fresh read after a relevant edit or after evidence has actually left retained model context.
- [ ] Replace blanket duplicate corrections with precise guidance about already-visible versus missing content. Do not return "already read" when the source is stale or no longer visible to the model. Never infer an unread region was seen from a path-only watermark.
- [ ] Avoid automatically rereading overlapping ranges when one broader read can supply the complete relevant section. Filesystem caching alone is not success: the target is fewer model/tool cycles.
- [ ] Audit routine `using-superpowers`/general workflow activation. Preload explicitly required stable guidance once when appropriate, or reuse an already activated skill without an extra tool cycle. Keep specialized and user-requested skill requirements intact; do not remove skill semantics merely to improve a benchmark.
- [ ] Add a research stopping instruction: answer once fresh authoritative evidence satisfies the requested claims; fetch additional sources when needed for gaps or conflicts. Preserve uncertainty-driven search and source grounding. Do not hardcode the benchmark answer or globally skip fetching.

**Meaningful checks:** file/registry/tool prompt tests plus new budget/truncation/range tests; read-before-edit and duplicate-after-edit tests; legacy loop/verification tests; prompt goldens for main/child/team; skills and web research prompt tests. Cover a requested 900-line range, a relevant function beyond line 500, very long individual lines, small-context fallback, aggregate batch budgets, edits invalidating prior evidence, and missing/compacted evidence.

**Acceptance:** A relevant file/range that fits the effective budget is delivered in one call with no hidden second truncation. Small-context cases stay bounded. Coding benchmarks show reduced adjacent/overlapping reads and model requests while preserving necessary evidence. The Node.js scenario should move toward search → fetch → answer when one fetched source is sufficient; this is a target, not a guaranteed three-call contract.

## Priority 3: shrink irrelevant model context without breaking caching

**Primary files:** `agentd/reasoning/engine.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller.py`, `agentd/providers/openai_native.py`, skill catalog/config, MCP tool-source catalog, and existing context/retrieval/history seams discovered during the accounting task.

### Task 3.1: attribute and simplify the static prompt/catalog

- [ ] Use the context breakdown to identify what produced the 54k-token initial request. Do not assume repository retrieval or MCP schemas alone explain it.
- [ ] Remove repeated teaching/examples and contradictory instructions while preserving tool semantics, approval rules, grounding, and child/team constraints. Ensure only applicable audience/capability blocks are rendered.
- [ ] Keep stable core rules and available tool names visible. Introduce discoverable on-demand detailed schemas/descriptions for large optional tool families, rather than silently dropping tools when the catalog hits a character cap.
- [ ] Preserve explicit skill catalog triggers and load skill bodies only when applicable. Deduplicate an active skill body if it is also present verbatim in retained history; keep one complete, authoritative copy recoverable after compaction.
- [ ] Preserve the native provider's required action/function schemas. Measure any duplicated tool catalog text before replacing it; the current native action schema is not necessarily a native schema for each underlying tool.

### Task 3.2: load repository context only when useful

- [ ] Avoid automatic code retrieval for a general knowledge question. Prefer lazy retrieval when a code/repository tool is selected, retaining a compact workspace identity and user-mentioned files/instructions.
- [ ] Do not add a separate LLM classifier request merely to decide whether retrieval is needed. Use the existing model's tool choice or an inexpensive deterministic routing decision with a safe fallback.
- [ ] Preserve explicitly requested project context, AGENTS/project instructions, edit grounding, and access to retrieval tools. A later turn can become code-related; defer the seed once, then freeze it rather than recomputing it each iteration.
- [ ] Validate cross-turn session continuation and restart behavior when deferred retrieval first appears. Add new evidence through a stable append point/delta rather than rewriting an existing committed prefix.

### Task 3.3: bound accumulated results and preserve evidence

- [ ] Apply the shared effective result/context budget consistently to controller and legacy histories. Do not confuse 4k-character UI previews with the full model input.
- [ ] Remove duplicate wrappers and superseded evidence where safe; preserve distinct call IDs and matching tool outputs. Avoid aggressive summarization that erases source details needed for an edit or citation.
- [ ] Use the existing compaction/recovery mechanism if present; otherwise design and validate bounded retention explicitly before changing history. Keep original evidence recoverable and teach the model how to obtain missing regions without range-shifting loops.
- [ ] Keep static content before dynamic content and preserve append-only history/session inputs. Compare cached-token ratios and first-content latency before/after; current 91–95% cache reuse should not be accidentally lost.
- [ ] Fix stale comments/docs about preview/model limits so future changes operate on the correct layer.

**Meaningful checks:** context-accounting tests, main/child/team prompt goldens, skill/MCP visibility tests, provider session/native history fixtures, durable history/stop/rewind tests, retrieval-seed persistence, deferred retrieval across turns/restart, and small-context recovery scenarios. Confirm code-specific questions still retrieve/read actual evidence and explicitly invoked tools/skills remain discoverable.

**Acceptance:** General-knowledge initial context is measurably smaller; coding context contains relevant rather than generic bulk. No extra classification model request is introduced. Cache reuse remains comparable under matched conditions, and required evidence survives or is recoverable within budget.

## Rollout and completion criteria

- [ ] Land focused commits in order: baseline/accounting; batch contract/provider support; bounded scheduler/events/prompts; unified read budgets; read/research/skill behavior; context/catalog/retrieval reduction. Keep each change independently reviewable.
- [ ] Run relevant tests and required typechecks per change. Re-run broader controller/provider/permission/prompt suites after the combined integration.
- [ ] Repeat the same benchmark scenarios after each priority, then with all three enabled. Report backend wall time, controller time, model requests, retries, read overlap/truncation, context sizes, cache ratio, answer quality, and tool/approval times.
- [ ] Test both a larger-context model and a smaller-context configuration. Do not improve the large model by breaking the original small-model use case.
- [ ] Install the combined change locally and smoke-test main chat plus an authorized child/team scenario. Verify tool pills, approvals, stop/reload, final answers, and generated code.
- [ ] Promote defaults only after correctness checks and measured improvements; document fallback/rollback controls and any remaining unsupported provider path.
- [ ] Preserve a final before/after report. Compare with Codex only in a separate matched experiment using the same task/model/effort where available; do not promise parity from this plan.

**Success:** fewer requests for independent reads, fewer reads caused by artificial fragmentation, smaller relevant context, and lower median latency on matched tasks with equivalent correctness. Set numeric improvement thresholds from the repeated baseline, not from the single 69.812s smoke.

## Deferred priorities

Retry error classification/server hints, reasoning-effort selection, provider/model comparisons, and streaming final-answer responsiveness remain follow-ups after priorities 1–3. They should reuse the same measurements; changes to approval policy require separate user direction.
