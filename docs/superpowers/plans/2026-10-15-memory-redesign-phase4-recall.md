# Memory redesign phase 4 — recap, recall and explicit tools

Date: 2026-10-15. Design: `../specs/2026-10-14-memory-recall-redesign-design.md`.

- [x] Persist bounded recap and restore it with rewind; require it on the v2 main agent's
  terminal actions and supply the prior recap without changing the legacy schema.
- [x] Implement scoped Q/C/R keyword/semantic/entity candidate union and explicit read/search/
  remember/forget tools, with read-only child access and audited immediate writes.
- [x] Implement off-loop reranker scoring with a deadline and explicit activation/truncation;
  add the bounded LLM judge with ID validation, empty results and timeout handling.
- [x] Enforce at most one LLM judge call per user message. Mid-turn reranker failure cannot
  initiate a second judge call; disable subsequent local triggers until the next user message.
- [x] Append injection messages once, persist shown IDs through history/checkpoints, and
  re-append only current-turn entries removed by compaction. Preserve provider cache prefixes.
- [x] Detect bounded working-set/error triggers and emit versioned delivery traces; expose
  v2 traces in the inspector and editor-client contracts.
- [x] Run focused and integration tests, schema codecs, goldens, rewind/cache and no-local
  tests; mark the scoring constants provisional until phase 6's held-out evaluation.

The new pipeline is selected only by `CRUCIBLE_MEMORY_RECALL_V2`. The old path stays available
until the evaluation and rollout review pass. Phase 5 adds idle curation separately.

## Implementation handoff

The new pipeline lives beside the retained legacy implementation in `memory/recall_v2.py` and
`memory/tool_source_v2.py`. It is wired into the controller only when the v2 flag is on.
Recap and private memory state have additive thread/checkpoint columns. Shown IDs are stored
inside injected history messages and therefore restore with the verbatim history checkpoint.
The judge budget is keyed by the checkpoint's original user-message ID, shared by the main
agent and child assignments, and committed before a provider call. Replies arriving during a
notice turn open their own recall state. Mid-turn reranker failure has no LLM fallback.

Delivery rechecks liveness/revision after judging. A correction/forget while scoring cannot
publish a stale entry. Compaction revalidates cached entries and re-appends the most recent
four current-turn entries once, preserving the per-message cap; older removed entries become
eligible for later retrieval. Explicit `remember(..., supersedes=id, origin=user_requested)`
adds audited immediate corrections without relaxing same-scope supersession.

The strict-provider judge schema uses parallel ID/reason arrays (normalized to a reason map in
traces) rather than arbitrary dynamic property names. The cross-encoder specifies sigmoid and
512-token truncation, using the installed library's activation parameter name. Primary API:
https://www.sbert.net/docs/package_reference/cross_encoder/model.html.

Validation so far: 388 memory/rewind/controller/schema/golden checks passed; focused real-loop
and strict-codec checks passed, along with editor-client/inspector tests and all TypeScript
checks. A separate v2 prompt golden was added; legacy goldens remain unchanged. Strict mypy and
ruff cover the new pipeline/core. Thresholds are provisional; phase 6 owns fitting and quality
review. The inspector trace supports v2 while its legacy browse tab remains a baseline view
through this dark rollout. No live backend restart, hosted corpus upload or default flip.

## Approved October 15 recall iteration

The branch now uses R → C → Q combined context and claim plus detail for the local reranker,
without topic/applicability headers. Missing fields and operational notices are omitted; the
last substantive completed closing is selected from the transcript. The primary cutoff stays
0.65, with four delivered entries, the existing deadline and one turn-start LLM fallback.
Actual tokenizer budgets and any shortening are visible in v2 traces. Candidate retrieval,
experimental flags and the legacy path retain their existing behavior.

This implements the owner-approved variant from the combined-context and full-content replay
reports. Fresh-chat misses and broader independent calibration remain open; this is an
iteration improvement, not completion of the quality gates. Implementation validation is in
`../evals/2026-10-15-memory-combined-recall-implementation.md`.

## Superseding catalog iteration

The owner subsequently chose candidate catalogs with main-model applicability decisions,
retaining BGE for ordering and removing the cutoff and runtime LLM judge. See
`2026-10-15-memory-catalog-recall.md` and
`../evals/2026-10-15-memory-catalog-recall-implementation.md`. Earlier judge/cap statements
above document the prior implementation, not the current v2 behavior.
