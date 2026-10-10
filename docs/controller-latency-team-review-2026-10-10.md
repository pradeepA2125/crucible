# Controller latency: agent-team review

Date: 10 October 2026. Branch: `codex/controller-latency`. The native web and profiling work was committed as `9508f45` and fast-forwarded into local `main` before this branch was created. The team reviewed the plan and code without editing implementation files.

Three reviewers covered batching/provider/replay safety, file reads/small models/edit evidence, and context/caching/measurement. They exchanged findings and agreed on a smaller first rollout. The [revised plan](superpowers/plans/2026-10-10-controller-latency.md) preserves the user's order: **1 batching → 2 redundant reads/steps → 3 context reduction**.

## Subsequent user direction

The user superseded the initial narrow batching recommendation: use schema-level multiple tool calls for commands, network tools, reads, and other permitted operations, with explicit dependencies. The [current plan](superpowers/plans/2026-10-10-controller-latency.md) and [general batching design](superpowers/specs/2026-10-10-controller-tool-batching-design.md) implement that scope. Native provider/history migration and per-call UI routing are now required Priority 1 work. The table and sequence below record the original review, not the current batching scope.

Follow-up batching review confirmed the separate logical/provider IDs, whole-graph validation, success-gated dependencies, and next-model-turn requirement for data-dependent arguments. It also identified missing shell cancellation cleanup and child approval status that must account for multiple pending gates. New skill guidance is an instruction dependency: reading it in the same batch does not prove speculative later commands follow it.

## Original review decisions

| Perspective | Finding | Plan decision |
|---|---|---|
| Latency versus architectural complexity | A batch envelope removes several model round trips while retaining one native provider call. Full multicall changes history, codecs, UI, and replay together. | Start with `batch_reads` over the permission-filtered registry. Keep the existing provider action protocol. |
| Concurrency safety | Local reads/listing are synchronous despite async signatures. Search subprocesses actually await. Some retrieval tools lazily mutate indexes. | Initial allowlist is read/list/search only. Measure fewer model cycles for file reads and true overlap for searches. Defer network and mutable retrieval tools. |
| Budgets and small models | Eight independently bounded results can overflow one request. The latest main-agent context meter omits pending results and child context. | Allocate deterministic aggregate shares first; use existing capacity provenance and final physical estimates before adaptation. Count member calls separately from model iterations. |
| Grounding and truncation | The registry applies 500 lines after the reader applies 2,000 lines/100k characters. Observers record successful reads by path, including empty reads. | Add delivered-range/revision/root metadata, remove the redundant cap and small-window guidance, and separate source freshness from evidence actually delivered. |
| Cancellation and durability | Pending calls are rendered done even without results; naive concurrent appends collide on call indices; stop can lose sibling results. | One pending outer pill first, ordered member states, persist partial evidence, and close interrupted members truthfully before cancellation propagates. |
| Context relevance | A general Node.js question carried a very large repository tree and tool catalog. | Cap/compact the tree and compact catalog JSON before lazy retrieval/tool-schema redesign. |
| Cache behavior | Native session preparation appends earlier dynamic tails to preserve prefixes, so logical payload sizes omit physical duplication. | Account for both logical components and final physical requests; preserve old pinned seeds and cached prefixes. |
| Skills and retention | Existing skill persistence, forced seeding, and controller compaction already exist. Parent skill activation replaces, child activation accumulates. | Reuse these mechanisms; audit redundant skill loading separately from file-read fragmentation. |

## Verified caveats

- `agentd/providers/openai_native.py` has a single pending history slot and synthesized call IDs. `agentd/providers/openai_transport.py` collects one call. Flipping `parallel_tool_calls` alone would drop or misrepresent calls.
- `apps/vscode-extension/src/controller.ts` routes main tool results through one mutable `openToolEvent[source]`; it does not use `call_index` like the child handler. Per-member live pills need this fixed first; an aggregate outer pill avoids that initial migration.
- `agentd/chat/tool_events.py` currently sets `done=True` when no result exists. Pending and interrupted state need explicit treatment.
- `agentd/chat/controller.py::_explore_context_from_history` expects single adjacent calls/results; it must expand completed batch evidence.
- `agentd/tools/search.py` must kill/reap `rg` on timeout and cancellation before concurrent searches are enabled.
- `agentd/tools/files.py` reads the entire file into memory before output caps. A separate input bound or streaming strategy is required for pathological files.
- `agentd/tools/sources.py` records successful reads by path; an empty/out-of-range read can currently update the watermark. `agentd/subagents/write_log.py` tracks file revision freshness, which is distinct from model-visible coverage.
- `agentd/tools/loop.py` encourages shifting ranges to avoid a duplicate guard after missing/truncated evidence. Recovery should permit the actual missing read when appropriate, rather than manufacture overlapping requests.
- `agentd/providers/runtime.py::context_capacity` already supplies capacity provenance. `agentd/chat/context_usage.py` describes the latest main physical request; it is unsuitable as a shared current occupancy for children or pending batches.
- `agentd/chat/controller_loop.py` already invokes compaction. `agentd/chat/controller.py` already persists active skills and supports forced skill seeding; a new generic preload could evict specialized parent guidance.

## Concrete context evidence

The retained **earlier** implicit Node.js smoke, at 13:32:59, reported 51,064 initial input tokens. This is the same question as the later profiling run, but a different execution; these sizes must not be attributed to the 69.812s capture.

Artifact: `.crucible/state/artifacts/chat/chat-20775551f291/fb78c315be144cce9ec1438d4d89076e/controller-turn-00.json`.

| Component | Size |
|---|---:|
| System instructions, raw characters | 77,444 |
| System instructions, JSON-serialized characters | 83,356 |
| Tool catalog, pretty JSON, 48 tools | 44,883 |
| Tool catalog, compact JSON | 32,947 |
| User payload, serialized characters | 108,611 |
| Retrieval seed, serialized characters | 103,442 |
| Repository tree, serialized characters | 86,734 |
| Repository tree entries | 1,773 |

The catalog included 21 Playwright MCP tools. The tree included generated workspace fixtures and `.ruff_cache`; file contents in that seed were empty. `agentd/retrieval/artifact_client.py` limits tree depth to five but lacks a total entry/character cap. A bounded tree with explicit omissions and expansion through directory tools is simpler than immediately changing seed persistence.

These are character counts, not a token breakdown. Compact serialization saves nearly 12k catalog characters; it does not establish a proportional latency improvement. Follow-up accounting must inspect the final dispatched native body because session preparation retains earlier dynamic tails.

## Measurement and delivery sequence

1. Capture coding baselines and logical/physical context accounting; add bounded delivery metadata.
2. Implement the batch facade, budgets, subprocess cleanup, durable partial results, and evidence extraction.
3. Remove stacked read caps and conflicting prompt instructions; validate revision/visibility-aware rereads and small-context limits.
4. Compact the tree and catalog, preserve pinned seeds, then decide whether deeper lazy retrieval/discovery is still justified.
5. Compare matched tasks for model requests, latency, context, cache reuse, read overlap/truncation, retries, and correctness.

The web smoke alone cannot demonstrate the cost of fragmented code reads. Include multiple independent files, two independent search subprocesses, a relevant function beyond line 500, and a small-context configuration. Do not add threads merely to make a synthetic file-read overlap test pass, or hardcode benchmark behavior to obtain a three-call answer.

No speedup or Codex parity is promised before these matched measurements. The original review deferred native multicall, per-member live pills, and network batching; the subsequent user direction above brings them into Priority 1. Retry-policy and model/effort changes remain separate decisions.
