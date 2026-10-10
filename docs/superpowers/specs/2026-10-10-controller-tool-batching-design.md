# General controller tool batching and dependencies

Date: 10 October 2026. Status: design for Priority 1; not implemented. Branch: `codex/controller-latency`.

This replaces the initial read-only `batch_reads` proposal. The controller accepts several tool calls from one model response, across commands, reads, searches, fetches, and other permitted tools. A dependency-aware scheduler decides which can run together. Batching reduces model round trips even when some members must execute serially.

## Contract and provider mapping

Use a provider-neutral ordered `ToolCallBatch` containing calls with:

- `id`: unique logical ID within the batch, namespaced by the persisted batch ID.
- `tool` and `args`: existing registry tool name and arguments, unchanged.
- `depends_on`: predecessor IDs; all must succeed before this call may run.
- `after`: predecessor IDs that must finish, regardless of success, for diagnostics or other explicitly requested follow-up work.
- `execution`: `auto`, `parallel`, or `serial`. This expresses scheduling intent, not permission or a guarantee that resource conflicts can be ignored. Omitted fields normalize to empty dependency lists and `auto`.

Keep the existing single `tool_call` action compatible by normalizing it to a one-member batch. Structured-output providers gain a `tool_calls` action with a `calls` array. Native providers may return several function-call items in one response; collect every complete item, including calls whose streamed argument fragments interleave. They normalize to the same batch.

For the current OpenAI adapter, extend the existing `crucible.tool_call` function arguments with logical ID, dependency, and execution fields, and permit multiple invocations. This retains registry routing and avoids duplicating every underlying tool schema. The API's generated function-call ID is separate from the model's logical dependency ID: persist both, and return each native output using its original provider call ID. Set `parallel_tool_calls=True` only on the upgraded, capability-supported path. The flag enables multiple model calls; the controller scheduler controls actual execution.

Replace the native instruction to take exactly one function action with a rule allowing one group of tool calls, or one exclusive controller action. Answer, edit/patch, mode transitions, submit, clarification, and progress actions cannot be mixed with tool calls in the same response. Reject a mixed response before executing anything. Registry tools that mutate state can still be batch members subject to existing phase permissions and serial scheduling; this does not turn top-level controller actions into tools.

Providers lacking native multicall use the structured batch action if supported. An unupgraded provider/legacy engine keeps its single-call path; never silently execute just the first call of a multicall response. Gate rollout by capability as well as the batching flag. Test strict native codecs, flattened schemas, and single-call compatibility together.

## Independent and dependent commands

Example normalized structured response, with tool arguments abbreviated only by choosing real, simple commands:

```json
{
  "type": "tool_calls",
  "calls": [
    {"id": "backend-tests", "tool": "run_command", "args": {"command": "pytest", "args": ["tests"], "cwd": "services/agentd-py"}, "depends_on": [], "after": [], "execution": "parallel"},
    {"id": "frontend-tests", "tool": "run_command", "args": {"command": "npm", "args": ["test"], "cwd": "apps/vscode-extension"}, "depends_on": [], "after": [], "execution": "parallel"}
  ]
}
```

These commands can overlap when their environments and outputs do not conflict. A different batch can express `install → build → test` through `depends_on`. Arguments are already known, so the scheduler runs the chain without asking the model again between steps. A failing install blocks build and test, while independent siblings continue. A diagnostics call using `after: ["test"]` can inspect logs after either test success or failure. A dependent member's own approval and guards still apply.

Execution dependencies are distinct from data dependencies. If a search discovers a path, an installer prints a newly generated identifier, or a command determines which follow-up to run, return the result and let the model construct the next batch. Version one does not interpolate previous output into arguments or execute model-supplied expressions. Do not invent unknown values merely to fit all work in one response. Newly read skill/project instructions are also an information dependency: the model must see and apply them before choosing affected commands; a graph edge alone cannot establish compliance with unread guidance.

## Validation and bounded scheduling

Validate the whole envelope before any side effects: size, unique IDs, argument shapes, tool visibility, phase/child permissions, statically knowable root boundaries, available tool budget, missing/self dependencies, and cycles across both dependency lists. Invalid envelopes produce a repairable validation result with no executed members. Do not partly execute an invalid graph. A call cannot reference another batch; completed earlier results are ordinary context.

Start with eight calls per batch and four active tool operations, configurable through the existing settings seam. Reserve member budget once; each member counts as a tool call, while the response counts as one model iteration. Dependencies blocked before execution do not masquerade as executed calls; record reserved versus executed counters explicitly and release unused reservations. Share concurrency limits across batches/children at the existing workspace execution owner where appropriate, rather than allowing every child to multiply subprocess limits independently.

Use a ready queue over the directed acyclic graph. Choose ready members in original call order for deterministic dispatch. Run each through the existing permission-filtered registry, including duplicate/evidence checks, phase restrictions, shadow-root resolution, command/network approvals, timeouts, and read observers. Never bypass guards by calling builtin implementations directly. Recheck runtime-sensitive conditions immediately before execution: files and phase state can change after envelope validation.

Do not use a read-tool allowlist as the batching contract. Define harness-owned scheduling metadata/resource rules:

| Operation | Scheduling behavior |
|---|---|
| Reads/listing and ordinary search | Concurrent where implementation supports it; coordinate against conflicting workspace writes. |
| Web search/fetch | Concurrent within existing per-origin/provider limits, approvals, connection limits, and deadlines. |
| Independent commands | Concurrent when explicitly marked independent or classified by a trusted command adapter, after conflict checks. Include tests/builds where their outputs and environments permit it. |
| Commands sharing a package environment, generated output, port, Git state, or other mutable resource | Serialize through resource leases or explicit dependency edges. Dependencies requiring success must be explicit; a lock provides exclusion, not success gating. |
| Registry edits, skill activation, todo/team mutations, lazy index updates | Participate in the batch but serialize relevant shared state; keep existing mutation/evidence guards. |
| Unknown MCP tools or unclassified operations | Accepted if permitted; conservatively serial until a trusted adapter supplies scheduling/resource metadata. Model hints do not override that classification. |

For shell commands, default unclassified `auto` calls to serial. Explicit `parallel` permits independent commands, but does not bypass known conflicts or exclusive operations such as Git mutation and dependency/environment installation. Resolve resource keys against actual shadow and real workspace roots: `run_command` executes in shadow CWD while using real-workspace binaries and virtual environments. Different CWDs alone do not prove isolation. Tool adapters derive known leases; model-supplied resource hints can add conflicts, not remove harness-derived ones. Arbitrary shell effects cannot be proven from a command string; document this limitation and teach models to declare parallelism only for independent work. Scheduling metadata is coordination, not a replacement for the command security boundary.

Apply leases consistently to single calls and batch members, including participating children sharing a workspace. Avoid overclaiming protection from external processes/user edits. Resolve approvals before acquiring execution slots and resource leases, then revalidate and acquire atomically; an approval wait must not occupy a worker or hold workspace locks. Existing approval UI may serialize prompts, while approved independent work continues. Derive child/team status from all pending approval gates and running members; one gate completing must not clear another member’s waiting state. The registry currently gates inside execution; introduce a prepare/approve/execute seam that preserves those guards rather than duplicating or bypassing them. Do not introduce blanket batch approval or change approval policy. Long-lived child dispatch/wait tools must release controller-state leases before waiting for child work that needs those same resources, preventing scheduler deadlocks.

A member waiting for approval does not count as running. Denial fails that member and blocks its success-dependent descendants; unrelated ready work continues. Cancellation stops the whole batch, including diagnostic `after` members not yet started. Do not launch cleanup tools after a user stops the turn. Turn deadlines and per-tool deadlines remain distinct.

## Outcomes, durable history, UI, and evidence

Reserve batch/member IDs and stable call indices before dispatch. Persist the complete issued call group before executing members. Do not compute indices from a mutable history length inside concurrent workers. Maintain states `pending`, `waiting_approval`, `running`, `success`, `error`, `denied`, `timed_out`, `blocked`, `cancelled`, and `interrupted`, with explicit dependency reasons and execution timestamps.

Normalize success from structured tool status, including nonzero command exits, rather than parsing output prose. Extend result contracts to retain exit code and timeout/denial/cancellation causes where the existing `is_error` flag is too coarse. A no-op duplicate response is not automatically successful new read/edit evidence. Errors, denials, timeouts, or blocked predecessors block `depends_on` descendants. An `after` edge releases on a terminal state, including a blocked call. Ordinary sibling errors do not cancel unrelated work. Preserve every outcome and return model-visible results in original call order regardless of completion order.

Emit per-call live events keyed by stable identity, and persist each completed result promptly. Fix main extension `forwardToolResult`, which currently routes through mutable `openToolEvent[source]` despite receiving `call_index`; child routing already provides a useful comparison. Fix calls without results currently marked done. Reload must show pending/interrupted truthfully and attach out-of-order results to the correct pill.

Native history must preserve an assistant call group followed by matching outputs for every emitted provider call, including blocked/denied/interrupted members. Replace the single pending-call slot in `openai_native.py`, the single collected call in `openai_transport.py`, and their synthetic-ID assumptions together. Persist provider IDs rather than synthesizing new IDs on each replay. Legacy saved single-call histories migrate through a deterministic compatibility adapter. Keep prefix/session continuation behavior stable, and measure the final physical request after `SessionInputs` preparation.

Persist partial controller history before propagating `CancelledError`; preserve completed sibling results and mark unfinished calls honestly. On process restart, recover completed evidence and mark abandoned work interrupted; never automatically replay a command or mutation. Provider continuation must close interrupted call slots with explicit outputs if needed, without pretending execution occurred.

Kill and reap command process groups and search subprocesses on timeout and cancellation. Current `run_command` has process-group timeout cleanup, but cancellation needs an explicit cleanup path: `CancelledError` is not caught by its `except Exception`. Cancellation tests must cover grandchildren/pipelines, approval waits, queued dependencies, and stop/reload paths for main and children.

Allocate a deterministic aggregate result budget and per-call shares before execution/delivery. Bound subprocess output capture as well as the final text; concurrent pipes can otherwise multiply memory usage. Reserve room for every status/ID and explicit omission marker. Observers receive immutable metadata for content actually delivered, not full results subsequently truncated. Update planner evidence extraction and compaction to join by call ID rather than adjacent single call/result pairs. UI previews remain distinct from model evidence.

## Implementation sequence and validation

1. Add call/batch/result contracts, schema validation, counters, and deterministic delivered-result budgets. Establish single-call compatibility fixtures.
2. Upgrade native streaming/codecs/history/session handling and structured-output schemas together, with capability gating. Test complete/interleaved calls, mixed terminal actions, original provider IDs, and replay.
3. Implement registry scheduling metadata, dependency ready queue, shared limits/leases, and per-member guards/approvals. Cover commands and network tools in the first release; conservative serial classification is allowed, a read-only batch wrapper is not the release boundary.
4. Add command/search cancellation cleanup, durable results, stable UI routing, planner evidence, and compaction integration. Integrate main/child/team prompts and audit legacy paths.
5. Run focused correctness tests, then matched command/read/web benchmarks and local chat/team smoke before promoting defaults.

Use barrier-controlled tools/processes for overlap tests, not millisecond assumptions. Test independent commands actually overlapping; install/build/test success gating; failed predecessors with diagnostics; serial resource conflicts; approval denial/wait; duplicate IDs/cycles/missing dependencies; phase/child permissions; mixed success; out-of-order stream/result events; aggregate/output memory limits; cancellation/restart without replay; provider continuation; and flag-off behavior. Validate prompt guidance across main, child, and teams. Local synchronous file reads do not overlap merely because their functions are async; measure reduced model cycles separately.

Acceptance: one model response can request reads, commands, and network operations; independent eligible calls overlap; known dependent command chains complete without intermediate model requests; failures and cancellation retain truthful individual outcomes. Calls whose arguments require new evidence use a later model response. All existing authorization and edit-evidence rules remain enforced. Compare model request counts and wall time for both parallel batches and intentionally serial dependency chains.
