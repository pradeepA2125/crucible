# Memory redesign phase 5 — idle curation

Date: 2026-10-15. Design: `../specs/2026-10-14-memory-recall-redesign-design.md`.

- [x] Add an independently default-off curator gate and current-provider binding.
- [x] Assemble typed evicted and hot evidence by stable source sequence, deduplicate overlaps,
  exclude summaries/instructions/agent frames/memory echoes, and cap calls at 24k estimated
  tokens including prompt/catalog overhead. Split at user-turn boundaries; prioritize within
  an oversized indivisible turn and label omissions.
- [x] Implement the durable lesson prompt, strict source/length/kind validation, exact and
  available semantic dedup, audited same-scope merging and user-said protection.
- [x] Sweep at startup/every five minutes, skip active parent/child work, select idle (30m) or
  long (100k stripped chars) threads, cap concurrency at two, persist watermark/failure/backoff.
- [x] Invalidate jobs on activity/rewind; never publish a stale awaited response. Keep SQLite
  operations on the owning loop, with idempotent publication across the separate databases.
- [x] Expose background status/errors in the inspector; run focused scheduler/publication/
  rewind/integration checks and retain the phase 6 quality gate.

Source implementation is authorized. Shipping/default enablement still requires the real
curator evaluation in the spec; mechanical tests alone cannot establish semantic quality.

## Implementation handoff

Source phase complete behind `CRUCIBLE_MEMORY_RECALL_V2=1` plus the independently
**default-off** `CRUCIBLE_MEMORY_CURATOR_V2=1`. `CRUCIBLE_MEMORY_CURATOR_MODEL` overrides the
current configured model; validated provider swaps update summarization, recall and curation
callbacks. The main controller's worker is registered at startup/shutdown; child run histories
are not selected, and active child work also blocks selection of its human thread.

`memory/curation_input.py` combines hot/evicted sequenced rows, prefers raw evictions over
shortened overlap, strips frames/private echoes/instruction bodies, and keeps source order.
Chunks split on user-turn boundaries. A large single turn uses newest-first priority selection
with explicit omission IDs. JSON escaping counts toward the budget; complete prompt/schema/
context/catalog is checked against 24k estimated tokens before every model call. Catalog entries
are removed from the least-related end if needed to fit; removed entries cannot be merge targets.

`memory/curator.py` validates strict proposals, semantic/procedural kinds, source membership and
an exact delivered source quote, rejects recognizable status notes and never repairs candidates.
These guards do not prove semantic entailment: unsupported causes and code/status quality still
need the spec's human-labelled evaluation. Automatic writes use repository scope. Curator
revisions target only offered live `agent_inferred` records in the same scope/revision; direct
user assertions are protected. A revision can improve detail/condition without replacing the
short claim. Exact dedup always applies; available vectors add the .92 cosine check.

The workspace stores activity time, watermark, rewind generation, failures and retry times.
The global store atomically commits mutations/audit and a batch idempotency marker. A failed
workspace-watermark update therefore cannot duplicate publication on retry. Awaited search/model/
embedding responses recheck activity/rewind before publication. Backoff is 5m, 30m, then 3h.
`/v1/memory/curation` checks workspace/thread membership; the inspector Curation tab shows
status/counts/backoff/error separately from chat.

Validation: 412 backend memory/rewind/provider/controller/schema checks passed before the last
condition-revision refinement; its focused curator checks also passed. Editor-client 59, host
memory bridge 5, inspector 10 tests passed; all three TypeScript checks passed. Ruff and strict
mypy cover the new core/evaluation modules. No runtime restart, default enablement, or new real
session upload occurred. The real curator quality gate remains open in phase 6.
