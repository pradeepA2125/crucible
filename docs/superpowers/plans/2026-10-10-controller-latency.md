# Controller latency implementation plan

**Goal:** Reduce model round trips and unnecessary context while preserving correctness, approvals, edit safety, and support for smaller models. Implement priorities **1 → 2 → 3**, measuring each independently before combining them. Implementation is in progress on `codex/controller-latency`; the first rollout is opt-in. The agent-team review is complete. The user subsequently expanded Priority 1 to general schema-level tool calls and dependency-aware execution; this supersedes the initial read-only facade recommendation. See [team review](../../controller-latency-team-review-2026-10-10.md).

**Baseline:** [Measured local smoke](../../controller-performance-smoke-2026-10-10.md) and [profiler usage](../../controller-performance.md). The Node.js question took 69.812s, six model requests, one search, three fetches, and one skill read. Provider requests plus retry backoff accounted for 89.1%; prompt/payload construction took approximately 33ms. Later requests had 91–95% cached input. This is one diagnostic run, not a controlled comparison with Codex.

## Implementation checkpoint (2026-10-10)

General tool graphs, native streaming/replay, workspace leases, approval preparation, durable per-call routing, process cancellation, adaptive bounded reads, content-free context attribution, and compact tree/catalog policies are implemented. Three independent managed settings default off. See [implementation measurements](../../controller-latency-implementation-2026-10-10.md).

Controlled five-run fixtures establish command overlap and dependency ordering. They use real subprocesses with simulated model/network delays, so they do not establish live-provider latency or cache gains. Full live main/child/team comparisons, context-model coverage, richer read-overlap telemetry, and promotion of defaults remain rollout work. Optional lazy retrieval/catalog discovery and static-rule deletion remain deferred until measurements justify them.

## User observations to preserve

The user reports that early harness development imposed small read limits and truncation to support smaller models, and prompts specifically encouraged reading small parts. This can force adjacent or overlapping reads and add a model round trip for each fragment. The optimization must address **both execution limits and model instructions**, rather than only changing a numeric cap or enabling parallelism.

Repository evidence at planning time confirmed these restrictions (the implementation below changes them):

- `agentd/tools/files.py`: up to 2,000 selected lines, followed by a 100,000-character cap.
- `agentd/tools/registry.py`: applies a second 500-output-line cap after the file reader; its tool description advertises 500 lines. Its truncation count describes the already bounded output, not necessarily the actual file length.
- `agentd/chat/controller_prompts.py`: requires ranges on files over 150 lines.
- `agentd/tools/loop.py` and `agentd/tools/verify_phase_sm.py`: teach 100–200-line windows and warn against whole-file reads because of the 500-line cap.
- `agentd/tools/loop.py`: repeated-call correction suggests shifting ranges if the previous result was truncated from history; this can encourage overlapping rereads instead of restoring missing evidence.
- The legacy tool loop independently slices model-facing results using `CRUCIBLE_TOOL_RESULT_MAX_CHARS` (default 100,000). The controller currently appends `out.output` to model history directly.
- `agentd/chat/tool_events.py`: separately truncates persisted UI previews to 4,000 characters. That preview is not the controller's model-facing history. Its comment about matching the model result default is stale; changing preview size alone will not reduce model rereads.

These were verified baseline behaviors. The historical reason for introducing them is the user's account; the additional latency caused by fragmented reads still needs a coding-task baseline.

All `agentd/` and Python test paths below are relative to `services/agentd-py/`; extension/UI paths are relative to `apps/vscode-extension/`.

## Scope and invariants

- Apply controller changes to main agents, controller-driven children, and teams. Audit legacy execution/verification loops for the same read-policy restrictions so different paths do not teach conflicting behavior.
- Accept general tool batches, including commands and network operations. Run independent eligible members concurrently; order dependent work explicitly and serialize conflicting resources/state mutations. Do not infer concurrency safety merely from a tool name or different working directories.
- Preserve child tool filters, workspace/shadow-root boundaries, read-before-edit checks, network approvals, origin restrictions, timeouts, and cancellation.
- Keep stable prompt prefixes and append-only history where possible. Do not rebuild the retrieval seed every iteration or reorder committed history.
- Keep file/result and aggregate request budgets bounded. Larger reads must be proportional to available context; smaller models retain conservative budgets.
- Do not rewrite Python or optimize its millisecond-scale prompt formatter as a primary latency project.
- Approval-policy changes, model/provider migration, retry-policy changes, and automatic search-and-crawl are separate follow-ups. This plan may measure them but does not silently change them.
- Keep existing single-action engines and saved conversations compatible. Upgrade native streaming, schemas, history, and event routing together for multicall support. Retain a capability-gated single-call fallback; never silently discard extra calls. Add initially opt-in rollout flags `CRUCIBLE_TOOL_BATCHING_ENABLED`, `CRUCIBLE_ADAPTIVE_READS_ENABLED`, and `CRUCIBLE_COMPACT_CONTEXT_ENABLED`, with a serial/conservative fallback. Expose managed settings through the existing settings/environment mapping when promoting them.

## Foundation: baseline, bounded delivery, and attribution

**Files:** `agentd/performance.py`, `scripts/profile-controller.py`, `agentd/chat/controller.py`, `agentd/reasoning/engine.py`, focused performance tests; new benchmark documentation/runner under `scripts/` as needed.

- [x] Review the plan with batching/replay, file-read/small-model, and context/measurement reviewers; reconcile their cross-review findings.
- [x] Add numeric per-request context accounting: system prompt, tool catalog/schema, retrieval seed, active skills, history/results, and dynamic instructions. Label estimates versus provider-reported token counts. Record counts/sizes only, not content. Measure the final physical request after native session preparation separately from the logical engine payload: appended dynamic tails can accumulate even when the logical payload contains only the current tail.
- [x] Extend timing to retrieval and controller setup before `ControllerLoop.run`; the current 69.812s capture excludes those steps. Keep controller-only and end-to-end backend measurements distinct.
- [ ] Record model requests, retries, tool calls, file paths/ranges through opaque stable identifiers, delivered lines/characters, truncations, overlap/repeated-read counts, and concurrency high-water marks. Keep source text and private paths out of performance records; normalize profiler function paths to repository-relative or library identifiers.
- [x] Introduce the immutable delivered-read metadata and deterministic per-call/aggregate budget seam needed by Priority 1. Keep existing caps initially; changing the read limits and instructions remains Priority 2. Separate disk-revision freshness from evidence actually delivered to the model.
- [ ] Establish fixed fixtures/scenarios: the Node.js question; a code explanation involving several independent files; two independent search subprocesses to prove real overlap; a change involving a long function beyond line 500; a small-context-model version of the coding task.
- [ ] Use deterministic fake transports/tools for scheduling assertions, and repeated live runs for latency. Keep model, effort, provider, workspace snapshot, task, and approval policy fixed; record cache state and retries separately. Aim for at least five live runs per comparison when feasible, reporting median and range rather than claiming reliable p95 from a tiny sample.
- [ ] Establish answer/change quality checks before optimization: required evidence, citations, correct code change, relevant checks passing, and no omitted required reads. Use isolated benchmark workspaces for edit scenarios.

**Acceptance:** A baseline exposes request count, context composition, fragmented reads, provider latency, approval wait, and pre-controller overhead. Measurements distinguish cached/uncached and retry/no-retry runs.

## Priority 1: general schema-level tool calls with dependency-aware execution

**Decision:** Support multiple model-emitted calls directly, including `run_command`, web search/fetch, reads, and other permitted registry tools. Replace the read-only `batch_reads` facade proposal. Schema/provider support enables a batch; a bounded scheduler decides concurrency and dependencies. The complete contract, examples, resource policy, and validation requirements are in [tool batching design](../specs/2026-10-10-controller-tool-batching-design.md).

**Primary files:** `agentd/reasoning/contracts.py`, controller/action schemas and prompts, `agentd/providers/openai_native.py`, `agentd/providers/openai_transport.py`, strict codecs, `agentd/chat/controller_loop.py`, `agentd/chat/controller.py`, `agentd/tools/registry.py`, `agentd/tools/sources.py`, new focused scheduling module, `agentd/tools/shell.py`, `agentd/tools/search.py`, `agentd/chat/tool_events.py`, and extension `src/controller.ts` plus child event handling. Audit legacy engine/loop integration and provider capability seams.

### Task 1.1: call contracts, schemas, and provider adapters

- [ ] Define an ordered batch with stable logical IDs, tool/args, success dependencies (`depends_on`), completion-order dependencies (`after`), and scheduling intent (`auto`/`parallel`/`serial`). Keep logical IDs separate from provider call IDs. Normalize existing single calls to singleton batches.
- [ ] Add the structured `tool_calls` action and collect all native function-call items, including interleaved streamed arguments. Extend existing native `crucible.tool_call` arguments with scheduling metadata; remove the exactly-one-action instruction for tool groups and enable native multicall only on upgraded supported paths.
- [ ] Reject mixed terminal/edit/controller actions and tool groups before execution. Apply phase, child/team filters, tool visibility, argument/root checks, unique IDs, missing/self dependency and cycle validation to the entire envelope. Invalid graphs execute no members.
- [ ] Replace native single-pending-call/history and single-collected-call assumptions together. Preserve every provider ID, matching output, deterministic order, strict codec behavior, and session-prefix continuation. Add migration fixtures for old saved single-call histories.
- [ ] Reserve member-tool budgets separately from model iterations; singles share the same budget. Start with eight members and four active operations. Establish deterministic aggregate delivery shares before read observation/history injection.

### Task 1.2: dependency scheduler, general tools, and approvals

- [ ] Implement a deterministic DAG ready queue over the existing permission-filtered registry. Known-argument chains such as install → build → test execute without intermediate model turns; failed/denied predecessors block success-dependent descendants while unrelated siblings continue.
- [ ] Run independent commands, searches, and network operations concurrently within existing approvals, origin/provider limits, deadlines, and shared execution limits. Do not make a read-tool allowlist the release boundary. Async signatures alone do not make synchronous file readers overlap.
- [ ] Add harness-owned scheduling/resource metadata. Serialize shared Git/environment/output/port/index/state resources; apply leases to singleton calls and participating children too. Resolve both shadow execution paths and real workspace environments. Model hints cannot remove known conflicts or grant permissions. Unknown tools remain valid batch members with conservative serial scheduling.
- [x] Default unclassified commands to serial in `auto`; support explicit independent `parallel` commands subject to known conflict/exclusive-operation checks. Different CWDs do not prove isolation. Resource exclusion does not imply success dependencies; those require graph edges.
- [ ] Resolve individual approvals before worker/lease acquisition, without blocking approved independent siblings. Track pending approval counts and active members so one completed child approval does not falsely clear another pending gate. Recheck mutable guards immediately before execution. Preserve existing approval policy.
- [x] Teach main/child/team models to batch independent calls, declare known execution dependencies, and wait for results when arguments or newly discovered instructions determine subsequent work. Reading a new skill then issuing speculative commands in the same batch does not establish that its guidance was followed. No result interpolation/expression language in version one.

### Task 1.3: per-call results, cancellation, UI, and evidence

- [x] Reserve stable call indices and persist the entire issued group before dispatch. Emit individual call/result states and join by identity, not completion order or mutable history length. Fix main extension result routing, which currently ignores `call_index` in favor of `openToolEvent[source]`.
- [ ] Persist each completed result promptly; keep explicit pending/approval/running/success/error/denied/timeout/blocked/cancelled/interrupted states. Fix calls without outputs currently marked done. Return a complete status/result for every emitted provider call in original order.
- [x] On stop, cancel approval waits and queued members, kill/reap command process groups and search subprocesses, preserve completed evidence and partial history, then propagate `CancelledError`. Shell timeout cleanup exists, but explicit cancellation cleanup is missing. Do not start diagnostic `after` members after cancellation.
- [x] On reload, retain completed evidence and mark abandoned members interrupted without rerunning commands/mutations. Close provider history slots truthfully for continuation. Cover main/child durable `partial_history` paths.
- [ ] Bound output capture as well as model delivery; concurrent subprocess pipes must not multiply unbounded memory use. Keep status/omission markers and immutable delivered-range metadata inside deterministic aggregate budgets.
- [x] Update planner evidence extraction and existing compaction to join grouped calls/results by ID; cancelled, blocked, and unperformed members are not read evidence. Keep UI preview limits distinct from model history.

**Meaningful checks:** contract/schema/native-stream/history/session tests; scheduler/registry/permission tests; command/search process cleanup; controller durable-history/planner/stop tests; main and child event routing/typechecks; prompt goldens. Use barriers, not fragile timing thresholds. Cover independent commands overlapping, dependent command success/failure and diagnostics, conflicting resources, approval waits/denials, graph validation, mixed actions, budgets, interleaved streaming, out-of-order results, cancellation with process grandchildren, restart without replay, capability fallback, and legacy single calls.

**Acceptance:** One model response can issue reads, commands, and network operations. Independent eligible calls overlap; known dependent command chains complete without intermediate model requests. Every call retains correct identity, authorization, result, and durable status. Data/instruction-dependent follow-up arguments are chosen on a later model turn. Benchmark both parallel work and serial chains, since each can reduce model round trips.

## Priority 2: remove fragmented and redundant reads/steps

**Primary files:** `agentd/tools/files.py`, `agentd/tools/registry.py`, `agentd/tools/sources.py`, `agentd/tools/loop.py`, `agentd/tools/verify_phase_sm.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller_loop.py`, `agentd/prompting/web_research.py`, skill-loading/config seams as needed.

### Task 2.1: one bounded read policy, rather than stacked caps

- [x] Introduce a shared read/result budget policy passed to the actual reader and model-history injection. Remove the registry's second 500-line slice. Do not substitute an unconditional unlimited read.
- [ ] Use `ProviderRuntime.context_capacity` with its configured/catalog/unknown provenance, reserved generation headroom, and conservative estimates of the current final physical request. The latest MAIN context-usage UI snapshot is not the occupancy of a pending batch or child request. Begin with deterministic budgets; make them adaptive only after physical accounting is reliable. Use a conservative fallback when capacity is unknown; retain a hard character/byte ceiling for pathological files.
- [x] For initial rollout, permit whole files that fit the effective budget and explicit ranges larger than 500 lines when they fit. Keep the existing 2,000-line/100,000-character values as initial hard ceilings until benchmark evidence justifies increasing them. Apply smaller effective budgets to smaller contexts.
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
- [x] Add a research stopping instruction: answer once fresh authoritative evidence satisfies the requested claims; fetch additional sources when needed for gaps or conflicts. Preserve uncertainty-driven search and source grounding. Do not hardcode the benchmark answer or globally skip fetching.

**Meaningful checks:** file/registry/tool prompt tests plus new budget/truncation/range tests; read-before-edit and duplicate-after-edit tests; legacy loop/verification tests; prompt goldens for main/child/team; skills and web research prompt tests. Cover a requested 900-line range, a relevant function beyond line 500, very long individual lines, oversized input files, empty/out-of-range reads, small-context fallback, aggregate batch budgets, edits invalidating prior evidence, and missing/compacted evidence.

**Acceptance:** A relevant file/range that fits the effective budget is delivered in one call with no hidden second truncation. Small-context cases stay bounded. Coding benchmarks show reduced adjacent/overlapping reads and model requests while preserving necessary evidence. The Node.js scenario should move toward search → fetch → answer when one fetched source is sufficient; this is a target, not a guaranteed three-call contract.

## Priority 3: shrink irrelevant model context without breaking caching

**Primary files:** `agentd/reasoning/engine.py`, `agentd/chat/controller_prompts.py`, `agentd/chat/controller.py`, `agentd/providers/openai_native.py`, skill catalog/config, MCP tool-source catalog, and existing context/retrieval/history seams discovered during the accounting task.

### Task 3.0: measure marginal savings, then compact meaningful bulk

User direction: break down each prompt/context block and measure its contribution before changing it. Report absolute characters/estimated tokens, percentage of the final physical request, and marginal room recovered by each candidate. Distinguish meaningful savings from negligible ones; preserve necessary instructions and validate quality/cache reuse. The [retained baseline attribution](../../controller-context-baseline-2026-10-10.md) identifies initial candidates, but does not establish latency gains.

- [ ] Capture runtime sizes for core rules, embedded tool catalog, project instructions, skills catalog/bodies, child/persona/team guidance, MCP/session/dispatch/memory/web guidance, retrieval subfields, history/results, dynamic instructions, and native schemas. Keep counts content-free and nested contributions non-additive.
- [ ] Compare candidates individually, then combined, with final physical request estimates, actual provider usage/cache counts, and matched quality checks. Do not delete low-volume rules simply because they are easy to shorten.


- [x] Add deterministic entry and serialized-character caps to the repository structure tree, which currently has only a depth limit. Emit explicit omitted-directory/entry markers and retain directory tools for expansion.
- [x] Exclude generated caches/worktrees/artifacts through the existing ignore policy or explicit workspace configuration. Do not globally exclude legitimate source/test directories merely because the benchmark had generated fixtures under `workspaces/`.
- [x] Apply the new compact seed policy to new threads. Preserve existing pinned retrieval seeds byte-for-byte until the established compaction/refresh flow explicitly replaces their evidence.
- [x] Serialize the embedded tool catalog compactly while preserving every available tool/schema. Measure this cheap change before introducing lazy tool discovery or changing native tool exposure.
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
- [x] Fix stale comments/docs about preview/model limits so future changes operate on the correct layer.

**Meaningful checks:** context-accounting tests, main/child/team prompt goldens, skill/MCP visibility tests, provider session/native history fixtures, durable history/stop/rewind tests, retrieval-seed persistence, deferred retrieval across turns/restart, and small-context recovery scenarios. Confirm code-specific questions still retrieve/read actual evidence and explicitly invoked tools/skills remain discoverable.

**Acceptance:** General-knowledge initial context is measurably smaller; coding context contains relevant rather than generic bulk. No extra classification model request is introduced. Cache reuse remains comparable under matched conditions, and required evidence survives or is recoverable within budget.

## Rollout and completion criteria

- [ ] Land focused commits in order: baseline/accounting and delivery metadata; batch contracts/native and structured adapters; general dependency scheduler/approvals/resource leases; subprocess cleanup/durable per-call evidence/UI/prompts; unified read budgets; read/research/skill behavior; compact tree/catalog, then any justified retrieval reduction. Keep each change independently reviewable.
- [ ] Run relevant tests and required typechecks per change. Re-run broader controller/provider/permission/prompt suites after the combined integration.
- [ ] Repeat the same benchmark scenarios after each priority, then with all three enabled. Report backend wall time, controller time, model requests, retries, read overlap/truncation, context sizes, cache ratio, answer quality, and tool/approval times.
- [ ] Test both a larger-context model and a smaller-context configuration. Do not improve the large model by breaking the original small-model use case.
- [ ] Install the combined change locally and smoke-test main chat plus an authorized child/team scenario. Verify tool pills, approvals, stop/reload, final answers, and generated code.
- [ ] Promote defaults only after correctness checks and measured improvements; document fallback/rollback controls and any remaining unsupported provider path.
- [ ] Preserve a final before/after report. Compare with Codex only in a separate matched experiment using the same task/model/effort where available; do not promise parity from this plan.

**Success:** fewer requests for independent tools and known dependent command chains, fewer reads caused by artificial fragmentation, smaller relevant context, and lower median latency on matched tasks with equivalent correctness. Set numeric improvement thresholds from the repeated baseline, not from the single 69.812s smoke.

## Deferred priorities

Invasive lazy tool/retrieval redesign remains conditional on measured bulk after the simpler context changes. Automatic substitution of tool outputs into later arguments and arbitrary conditional execution expressions are deferred; data-dependent calls use the next model response. Native multicall, per-call live events, commands, and network batching are part of Priority 1.

Retry error classification/server hints, reasoning-effort selection, provider/model comparisons, and streaming final-answer responsiveness remain follow-ups after priorities 1–3. They should reuse the same measurements; changes to approval policy require separate user direction.
