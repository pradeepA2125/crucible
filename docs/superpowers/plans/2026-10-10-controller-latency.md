# Controller latency implementation plan

**Goal:** Reduce model round trips and unnecessary context while preserving correctness, approvals, edit safety, and support for smaller models. Implement priorities **1 → 2 → 3**, measuring each independently before combining them. This document is a plan; implementation has not started. The agent-team review is complete; its decisions are incorporated below. See [team review](../../controller-latency-team-review-2026-10-10.md).

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
- Keep existing single-action engines and saved conversations compatible. The first batching release retains one native action and does not change the OpenAI stream/session protocol. Add initially opt-in rollout flags `CRUCIBLE_TOOL_BATCHING_ENABLED`, `CRUCIBLE_ADAPTIVE_READS_ENABLED`, and `CRUCIBLE_COMPACT_CONTEXT_ENABLED`, with a serial/conservative fallback. Expose managed settings through the existing settings/environment mapping when promoting them.

## Foundation: baseline, bounded delivery, and attribution

**Files:** `agentd/performance.py`, `scripts/profile-controller.py`, `agentd/chat/controller.py`, `agentd/reasoning/engine.py`, focused performance tests; new benchmark documentation/runner under `scripts/` as needed.

- [x] Review the plan with batching/replay, file-read/small-model, and context/measurement reviewers; reconcile their cross-review findings.
- [ ] Add numeric per-request context accounting: system prompt, tool catalog/schema, retrieval seed, active skills, history/results, and dynamic instructions. Label estimates versus provider-reported token counts. Record counts/sizes only, not content. Measure the final physical request after native session preparation separately from the logical engine payload: appended dynamic tails can accumulate even when the logical payload contains only the current tail.
- [ ] Extend timing to retrieval and controller setup before `ControllerLoop.run`; the current 69.812s capture excludes those steps. Keep controller-only and end-to-end backend measurements distinct.
- [ ] Record model requests, retries, tool calls, file paths/ranges through opaque stable identifiers, delivered lines/characters, truncations, overlap/repeated-read counts, and concurrency high-water marks. Keep source text and private paths out of performance records; normalize profiler function paths to repository-relative or library identifiers.
- [ ] Introduce the immutable delivered-read metadata and deterministic per-call/aggregate budget seam needed by Priority 1. Keep existing caps initially; changing the read limits and instructions remains Priority 2. Separate disk-revision freshness from evidence actually delivered to the model.
- [ ] Establish fixed fixtures/scenarios: the Node.js question; a code explanation involving several independent files; two independent search subprocesses to prove real overlap; a change involving a long function beyond line 500; a small-context-model version of the coding task.
- [ ] Use deterministic fake transports/tools for scheduling assertions, and repeated live runs for latency. Keep model, effort, provider, workspace snapshot, task, and approval policy fixed; record cache state and retries separately. Aim for at least five live runs per comparison when feasible, reporting median and range rather than claiming reliable p95 from a tiny sample.
- [ ] Establish answer/change quality checks before optimization: required evidence, citations, correct code change, relevant checks passing, and no omitted required reads. Use isolated benchmark workspaces for edit scenarios.

**Acceptance:** A baseline exposes request count, context composition, fragmented reads, provider latency, approval wait, and pre-controller overhead. Measurements distinguish cached/uncached and retry/no-retry runs.

## Priority 1: a batch facade for independent builtin reads/searches

**Decision:** Implement `batch_reads` as a registry tool invoked by the existing `tool_call` action. Keep `parallel_tool_calls=False`, current action schemas, native codecs, and continuation/history IDs unchanged. One aggregate result gives the model several member outputs in one request cycle. Full native multicall support is a later option, not a prerequisite for the first gain.

**Primary files:** new focused `agentd/tools/batch_reads.py` and tests; `agentd/tools/sources.py`, `agentd/chat/controller.py`, `agentd/chat/controller_loop.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/tool_events.py`, `agentd/tools/search.py`; file/result budget seam from the foundation task. Locate child alias/permission catalog and legacy loop integration points before wiring the wrapper.

### Task 1.1: define and validate the batch envelope

- [ ] Add `batch_reads` over the **already permission-filtered registry**. Do not execute builtin functions directly to bypass its routing, child permissions, root checks, or read observation.
- [ ] Use an ordered member list with unique stable member IDs, tool names, and arguments. Initially allow only known builtin `read_file`, `list_directory`, and `search_code`. Reject recursive batches, skills, network tools, commands, edits, state changes, unknown MCP tools, and retrieval/graph tools whose lazy execution can mutate indexes.
- [ ] Start with at most eight members and four active operations. Keep model-iteration and member-tool counters distinct. Reserve remaining member budget before execution; charge members rather than wrapper-plus-members. Singles count against the same tool budget.
- [ ] Prevalidate all member IDs, shapes, eligibility, visibility/permissions, path boundaries, and budgets before execution. An invalid member rejects the envelope before members run. Runtime errors caused by later file changes or tool failures remain per-member results.
- [ ] Allocate deterministic aggregate result shares before delivering reads and updating observers. Keep a complete labelled result/status for every member; include explicit range/omission metadata when a share cannot deliver everything. Do not truncate an aggregate after treating its full content as observed evidence.
- [ ] Provide one compact tool description and a batching instruction for main/child/team agents. Do not repeat the complete descriptions/schemas of the three member tools inside the wrapper catalog entry.

### Task 1.2: bounded scheduling and subprocess cleanup

- [ ] Execute eligible members with bounded concurrency, turn deadlines, deterministic result ordering, and the existing per-member guards. Ordinary tool errors do not discard successful siblings.
- [ ] Fix `search_code` subprocess cleanup: timeout or cancellation kills and reaps the `rg` child and closes resources. Test this before permitting concurrent search batches.
- [ ] Local file readers currently perform synchronous work despite their async signatures. Measure request-cycle reduction for read batches; do not claim `gather` makes those file reads overlap, and do not add threads merely to satisfy a concurrency test.
- [ ] Use two independent async search calls with barriers/process fakes to demonstrate actual overlap and bounded active operations. Discovery-dependent searches/reads still occur in successive batches.

### Task 1.3: truthful partial results, history, and evidence

- [ ] Reserve and persist the outer call before execution. Keep ordered member states (`pending`, `running`, `success`, `error`, `cancelled`/`interrupted`) and completed outputs. Use one outer aggregate pill initially; member IDs/statuses are visible inside its result.
- [ ] Correct `trace_to_tool_events`: a call without a result must be pending rather than `done=True`. Persist the aggregate's completed member evidence during progress so stop/reload does not lose it.
- [ ] On turn cancellation, stop/reap active work, finalize unfinished member slots, persist the aggregate output and partial controller history, then propagate `CancelledError`. Never swallow cancellation or label unperformed members successful.
- [ ] On reload/restart, retain completed evidence and mark abandoned members interrupted; do not silently rerun them. Verify existing main/child `partial_history` cancellation paths and durable transcript behavior.
- [ ] Expand successful completed members in `_explore_context_from_history`, which currently expects adjacent single call/result pairs. Preserve each member's identity, errors, delivered ranges, and order. Cancelled/unperformed members are not code evidence.
- [ ] Keep saved single-call conversations and engines unchanged. Prompt agents to batch independent known reads and stop when their evidence is sufficient.

**Meaningful checks:** new batch facade/scheduler tests; `test_tool_registry_aggregator.py`; controller tool-event/durable-history/stop/planner-evidence tests; child permission tests; search subprocess cleanup tests; prompt goldens. Use barriers for concurrency assertions, not fragile millisecond thresholds. Cover invalid/recursive/disallowed members, aggregate budgets, distinct counters, mixed success/failure, out-of-order completion, cancellation/partial evidence, interruption recovery, and flag-off behavior. Run frontend tests/typechecks only where event rendering changes.

**Acceptance:** Three eligible independent reads can supply three distinct results in one model-request cycle. Two async searches overlap without exceeding concurrency limits or leaving subprocesses behind. Every member has truthful status, and completed evidence survives stop/reload. Stateful and network operations remain outside the initial wrapper.

**Deferred batching extensions:** per-member live pills, network batching, and multiple native provider calls. Per-member pills first need extension result routing by stable call identity: `forwardToolResult` currently relies on a mutable `openToolEvent[source]` and ignores `call_index`. Full native calls require replacing single pending-call/history assumptions together. Add those only when envelope limitations are measured.

## Priority 2: remove fragmented and redundant reads/steps

**Primary files:** `agentd/tools/files.py`, `agentd/tools/registry.py`, `agentd/tools/sources.py`, `agentd/tools/loop.py`, `agentd/tools/verify_phase_sm.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller_loop.py`, `agentd/prompting/web_research.py`, skill-loading/config seams as needed.

### Task 2.1: one bounded read policy, rather than stacked caps

- [ ] Introduce a shared read/result budget policy passed to the actual reader and model-history injection. Remove the registry's second 500-line slice. Do not substitute an unconditional unlimited read.
- [ ] Use `ProviderRuntime.context_capacity` with its configured/catalog/unknown provenance, reserved generation headroom, and conservative estimates of the current final physical request. The latest MAIN context-usage UI snapshot is not the occupancy of a pending batch or child request. Begin with deterministic budgets; make them adaptive only after physical accounting is reliable. Use a conservative fallback when capacity is unknown; retain a hard character/byte ceiling for pathological files.
- [ ] For initial rollout, permit whole files that fit the effective budget and explicit ranges larger than 500 lines when they fit. Keep the existing 2,000-line/100,000-character values as initial hard ceilings until benchmark evidence justifies increasing them. Apply smaller effective budgets to smaller contexts.
- [ ] Bound input file reading separately: the current reader loads the entire file before applying output limits. Add an input byte ceiling or bounded streaming strategy; output caps alone do not bound memory.
- [ ] Enforce budgets while delivering complete numbered lines. Report actual file line count, requested range, delivered range, omitted range, and truncation reason. Avoid a misleading total calculated after an earlier cap or silent mid-line cuts.
- [ ] Preserve path traversal protections, shadow/live-root behavior, read observation, and edit safety. Do not count content omitted by truncation as evidence the model received. Fix empty/out-of-range reads currently triggering a successful path-only observation; do not equate file revision freshness with visible line coverage.
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
- [ ] Audit routine `using-superpowers`/general workflow activation. Prefer the existing persisted activation and forced-seeding mechanisms. The parent replaces its active skill while children accumulate theirs; generic preloading must not evict a specialized workflow or create duplicate bodies. Reuse an already activated skill without an extra tool cycle when appropriate. Keep specialized and user-requested skill requirements intact; do not remove skill semantics merely to improve a benchmark.
- [ ] Add a research stopping instruction: answer once fresh authoritative evidence satisfies the requested claims; fetch additional sources when needed for gaps or conflicts. Preserve uncertainty-driven search and source grounding. Do not hardcode the benchmark answer or globally skip fetching.

**Meaningful checks:** file/registry/tool prompt tests plus new budget/truncation/range tests; read-before-edit and duplicate-after-edit tests; legacy loop/verification tests; prompt goldens for main/child/team; skills and web research prompt tests. Cover a requested 900-line range, a relevant function beyond line 500, very long individual lines, oversized input files, empty/out-of-range reads, small-context fallback, aggregate batch budgets, edits invalidating prior evidence, and missing/compacted evidence.

**Acceptance:** A relevant file/range that fits the effective budget is delivered in one call with no hidden second truncation. Small-context cases stay bounded. Coding benchmarks show reduced adjacent/overlapping reads and model requests while preserving necessary evidence. The Node.js scenario should move toward search → fetch → answer when one fetched source is sufficient; this is a target, not a guaranteed three-call contract.

## Priority 3: shrink irrelevant model context without breaking caching

**Primary files:** `agentd/reasoning/engine.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller.py`, `agentd/providers/openai_native.py`, skill catalog/config, MCP tool-source catalog, and existing context/retrieval/history seams discovered during the accounting task.

### Task 3.0: compact known bulk before complex context redesign

- [ ] Add deterministic entry and serialized-character caps to the repository structure tree, which currently has only a depth limit. Emit explicit omitted-directory/entry markers and retain directory tools for expansion.
- [ ] Exclude generated caches/worktrees/artifacts through the existing ignore policy or explicit workspace configuration. Do not globally exclude legitimate source/test directories merely because the benchmark had generated fixtures under `workspaces/`.
- [ ] Apply the new compact seed policy to new threads. Preserve existing pinned retrieval seeds byte-for-byte until the established compaction/refresh flow explicitly replaces their evidence.
- [ ] Serialize the embedded tool catalog compactly while preserving every available tool/schema. Measure this cheap change before introducing lazy tool discovery or changing native tool exposure.
- [ ] Re-measure context size, requests, quality, and cache behavior before moving to the more invasive tasks below. Their need must be established by remaining measurable bulk.

**Evidence:** The earlier identical-question Node smoke (13:32:59, not the later 69.812s profiled turn) contained 103,442 serialized characters of retrieval seed, including a 1,773-entry / 86,734-character tree. Its 48-tool catalog occupied 44,883 pretty-printed characters versus 32,947 compact characters. See the team review for the exact artifact and limits of this attribution.

### Task 3.1: attribute and simplify the static prompt/catalog

- [ ] Use the context breakdown to identify what produced the 54k-token initial request. Do not assume repository retrieval or MCP schemas alone explain it.
- [ ] Remove repeated teaching/examples and contradictory instructions while preserving tool semantics, approval rules, grounding, and child/team constraints. Ensure only applicable audience/capability blocks are rendered.
- [ ] Keep stable core rules and available tool names visible. If measured bulk remains after Task 3.0, introduce discoverable on-demand detailed schemas/descriptions for large optional tool families, rather than silently dropping tools when the catalog hits a character cap.
- [ ] Preserve explicit skill catalog triggers and load skill bodies only when applicable. Deduplicate an active skill body if it is also present verbatim in retained history; keep one complete, authoritative copy recoverable after compaction.
- [ ] Preserve the native provider's required action/function schemas. Measure any duplicated tool catalog text before replacing it; the current native action schema is not necessarily a native schema for each underlying tool.

### Task 3.2: defer irrelevant repository retrieval if the compact seed remains costly

- [ ] Avoid automatic code retrieval for a general knowledge question. Prefer lazy retrieval when a code/repository tool is selected, retaining a compact workspace identity and user-mentioned files/instructions.
- [ ] Do not add a separate LLM classifier request merely to decide whether retrieval is needed. Use the existing model's tool choice or an inexpensive deterministic routing decision with a safe fallback.
- [ ] Preserve explicitly requested project context, AGENTS/project instructions, edit grounding, and access to retrieval tools. A later turn can become code-related; defer the seed once, then freeze it rather than recomputing it each iteration.
- [ ] Validate cross-turn session continuation and restart behavior when deferred retrieval first appears. Add new evidence through a stable append point/delta rather than rewriting an existing committed prefix.

### Task 3.3: bound accumulated results and preserve evidence

- [ ] Apply the shared effective result/context budget consistently to controller and legacy histories. Do not confuse 4k-character UI previews with the full model input.
- [ ] Remove duplicate wrappers and superseded evidence where safe; preserve distinct call IDs and matching tool outputs. Avoid aggressive summarization that erases source details needed for an edit or citation.
- [ ] Reuse the existing controller-loop compaction and recovery mechanism. Validate its evidence/revision metadata integration before changing history; do not introduce a second retention subsystem. Keep original evidence recoverable and teach the model how to obtain missing regions without range-shifting loops.
- [ ] Keep static content before dynamic content and preserve append-only history/session inputs. Compare cached-token ratios and first-content latency before/after; current 91–95% cache reuse should not be accidentally lost.
- [ ] Fix stale comments/docs about preview/model limits so future changes operate on the correct layer.

**Meaningful checks:** context-accounting tests, main/child/team prompt goldens, skill/MCP visibility tests, provider session/native history fixtures, durable history/stop/rewind tests, retrieval-seed persistence, deferred retrieval across turns/restart, and small-context recovery scenarios. Confirm code-specific questions still retrieve/read actual evidence and explicitly invoked tools/skills remain discoverable.

**Acceptance:** General-knowledge initial context is measurably smaller; coding context contains relevant rather than generic bulk. No extra classification model request is introduced. Cache reuse remains comparable under matched conditions, and required evidence survives or is recoverable within budget.

## Rollout and completion criteria

- [ ] Land focused commits in order: baseline/accounting and delivery metadata; batch facade plus subprocess cleanup; bounded scheduler/durable evidence/prompts; unified read budgets; read/research/skill behavior; compact tree/catalog, then any justified retrieval reduction. Keep each change independently reviewable.
- [ ] Run relevant tests and required typechecks per change. Re-run broader controller/provider/permission/prompt suites after the combined integration.
- [ ] Repeat the same benchmark scenarios after each priority, then with all three enabled. Report backend wall time, controller time, model requests, retries, read overlap/truncation, context sizes, cache ratio, answer quality, and tool/approval times.
- [ ] Test both a larger-context model and a smaller-context configuration. Do not improve the large model by breaking the original small-model use case.
- [ ] Install the combined change locally and smoke-test main chat plus an authorized child/team scenario. Verify tool pills, approvals, stop/reload, final answers, and generated code.
- [ ] Promote defaults only after correctness checks and measured improvements; document fallback/rollback controls and any remaining unsupported provider path.
- [ ] Preserve a final before/after report. Compare with Codex only in a separate matched experiment using the same task/model/effort where available; do not promise parity from this plan.

**Success:** fewer requests for independent reads, fewer reads caused by artificial fragmentation, smaller relevant context, and lower median latency on matched tasks with equivalent correctness. Set numeric improvement thresholds from the repeated baseline, not from the single 69.812s smoke.

## Deferred priorities

Full native multicall, per-member live pills, network batching, and invasive lazy tool/retrieval redesign are conditional follow-ups after the simpler three priorities have been measured.

Retry error classification/server hints, reasoning-effort selection, provider/model comparisons, and streaming final-answer responsiveness remain follow-ups after priorities 1–3. They should reuse the same measurements; changes to approval policy require separate user direction.
