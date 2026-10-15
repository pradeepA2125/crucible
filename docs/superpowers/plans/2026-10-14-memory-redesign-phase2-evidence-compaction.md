# Memory redesign phase 2 — evidence rendering and compaction budgets

Date: 2026-10-14. Design: `../specs/2026-10-14-memory-recall-redesign-design.md`.

## Scope and rollout

Implement the common renderer and sequential compaction folds. Preserve compaction triggers,
hot-history selection, raw segment persistence, existing recall and consolidation. Keep this
change independently gated by `CRUCIBLE_MEMORY_COMPACT_EVIDENCE`, initially false. The numerical
caps remain provisional until summary quality and resumed-task behavior are evaluated.

## Work

- [x] Render history and typed segments identically, retaining source IDs, tool signatures,
  status/errors, file paths and delivered ranges. Mark unavailable legacy metadata honestly.
- [x] Apply the specified file/command/search/edit/text caps with explicit omission markers.
  Exclude summary/recall/skill/AGENTS bodies without discarding unrelated mentions or errors.
  Keep attributed agent reports for compaction; support their exclusion for future curation.
  Gated compaction now includes bounded thoughts; its quality gate remains open.
- [x] Count the prior anchor, serialized transcript, summary system prompt and transport reserve
  using the conservative three-character estimator. Target 200k and reject input over the
  estimated 500k hard limit or the configured provider window minus the response reserve.
- [x] Split on logical action/tool-result turn boundaries and fold in order. Commit each good
  anchor; a later failure retains the last good anchor and the unsummarized raw suffix in
  the next model context. Reject an indivisible oversized turn
  before sending it, preserve its raw source, and keep image tombstones.
- [x] Add focused renderer, budget, repeated-fold, failure and factory-gate tests; run existing
  memory, rewind, image, provider projection and prompt regression checks.
- [x] Run a local read-only comparison against actual controller histories and frozen raw
  segments, record reductions and known losses, and replay compaction/folding without sending
  additional production session data to a hosted model.

## Evaluation limits

The prior approved hosted spike used 20 redacted UI-session spans. Actual controller-history
rendering and budget replays here can validate mechanics and input reduction; they cannot
establish LLM summary coverage or successful task continuation. Keep the new flag off until
that separate quality gate passes. Phase 3 supplies the common local-model capability.

## Implementation handoff

The renderer is in `agentd/memory/evidence.py`, turn-budget selection in
`agentd/memory/folding.py`, and integration in `memory/compactor.py`, `memory/harness.py` and
`memory/config.py`. Gated compaction includes bounded thoughts. The curator requests thoughts,
exclude framed agent reports and select rows using the recorded priorities.

Every model fold includes the prior anchor and full summary prompt in its estimated budget.
The factory adds JSON serialization and 2,048 estimated tokens of transport headroom. Fold
budgets re-read the context limit between calls, and each good anchor is committed before
processing the next chunk. Call IDs survive segment persistence for matching same-tool batches.
An oversized indivisible turn or summary degrades with raw evidence retained.

The real-data check is recorded in
`../discussions/2026-10-14-phase2-evidence-rendering-check.md`: about 70.1% smaller on retained
controller histories, 16.6% smaller on old untyped segments, and substantial literal-name loss
under the 300-character file cap. All sampled turns fit their estimated budgets. Mock anchors
check folding mechanics only; they do not score summaries or actual resumed work.

Validation: 563 regression tests passed. Ruff and strict mypy passed on the six phase 2 source
modules and new tests (mypy checks source modules). The live backend and default compaction path
were not changed. The next implementation phase is local-models capability; the compaction
quality/continuation gate remains required before enabling its flag.

## Phase 6 findings, 2026-10-15

The initial caps lost resume details in paired real-provider checks. Current provisional caps
are file reads 2,000 head + 1,000 tail, successful commands 1,600 + 3,200, failed commands
800 + 1,200, and edits 4,000 + 1,000; thoughts are included. The new read-only size report is
`.crucible/state/eval/memory-phase6-2026-10-15/evidence-sizes-v2.json`: typed histories now
reduce 56.64%, legacy untyped segments 16.57%. Preserve the previous 70.1% report as historical.

A later-fold failure had retained raw evidence on disk but dropped its unsummarized suffix
from the next model request. This is fixed and tested, including retry without repeating the
committed prefix. A hosted model also answered a historical question instead of summarizing
it; the gated summarizer now requires summary tags and a known note heading, retries once,
and fails closed with raw history retained. The prompt explicitly treats historical requests
as evidence. This is a format safeguard, not semantic coverage validation.

Paired UI-export reconstructions still show missing files and imperfect judges; these are
not five actual recorded compaction boundaries or successful project continuations. See the
phase 6 plan for current results and remaining release gates. Defaults remain off.
