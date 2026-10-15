# Combined-context recall implementation — October 15, 2026

The owner approved adopting the recap-first, claim-plus-detail variant for this iteration,
accepting the known fresh-chat limitation. The branch implementation and canonical spec now
use that variant. It initially completed as a source change; the subsequent authorized local
installation is recorded below.

## Behavior

- Candidate retrieval still searches Q/C/R separately, with the existing scope, shown-set,
  keyword, semantic and entity rules.
- Local reranking uses one pair per candidate: **recap → previous substantive answer → current
  request**, against **short claim + detail**. Topic/applicability headers are excluded from
  this scoring document; the catalog and search indexes retain them.
- Missing signals are omitted. Previous-answer selection skips system notices, progress,
  breadcrumbs and unfinished messages. New generated error closings persist `system_notice`
  metadata, including when finalizing an existing tool-pills message. Known older generated
  usage/error/stop strings are filtered for compatibility. A legitimate task warning is retained.
- The existing primary cutoff remains **0.65**, inclusive, with up to four delivered memories.
  Scoring retains the three-second deadline/model lock and one turn-start LLM fallback budget;
  mid-turn failure cannot start another LLM call.
- Actual model-tokenizer budgeting keeps pairs within 512 tokens. Memory bodies get at most
  256 tokens, with head/tail shortening for oversized bodies; the tested eight bodies fit whole.
  Present signal budgets use Q/C/R weights 65/20/15. These weights allocate text space, not scores.
- Traces contain a single `combined` score, the actual bounded combined query, shortened signal
  names/memory IDs and maximum pair length. The inspector renders these diagnostics; older
  traces without the new optional fields still parse.

Implementation evidence: `services/agentd-py/agentd/memory/recall_context.py:14`,
`services/agentd-py/agentd/memory/reranker.py:86`,
`services/agentd-py/agentd/memory/recall_v2.py:364`,
`services/agentd-py/agentd/chat/controller.py:1080`,
`services/agentd-py/agentd/chat/controller.py:1218`,
`services/agentd-py/agentd/chat/storage.py:523`, and
`apps/vscode-extension/webview-ui/src/memory/RecallTraceTab.tsx:115`.
Canonical behavior: `../specs/2026-10-14-memory-recall-redesign-design.md:242`.

## Validation

- Backend regression suite: **90 passed** across recall/context/reranker, controller persistence
  and provider failures, v2 integration, prompt goldens, historical judge evaluation, strict
  schema codecs and memory routes.
- Follow-up integration/cache/rewind/transcript-order suite: **18 passed**, overlapping the
  earlier suite. Includes the real controller selecting an older substantive answer after
  a system notice and a progress message; these counts are not additive distinct-test totals.
- Editor-client HTTP suite: **60 passed**. Recall inspector component suite: **7 passed**.
- Focused Ruff checks passed; strict mypy passed for the three recall/reranker modules.
- All three TypeScript checks and the production client/extension/webview build passed. The
  build reported its non-failing large graph-chunk advisory.

Regression coverage checks one body pair per candidate, RCQ order, missing/warning fields,
inclusive cutoff, score order, delivery cap, invalid scorer results, bounded Unicode inputs,
notice persistence with/without tool pills, and retained LLM fallback restrictions.

## Cached-model implementation replay

The implemented `Reranker.score_context` was run offline on CPU with the same cached BGE
snapshot and fixed eight-memory pools used in the full-content experiment. All four requests
preserved that experiment's selections at 0.65:

| Saved request | Branch selections | Maximum pair tokens |
| --- | --- | ---: |
| Actual landing continuation | Static fallback (0.98239), marketing-memory boundary (0.86381) | 447 |
| Actual fresh-chat requirements | None | 159 |
| Constructed caption-only control | None | 126 |
| Constructed health-route control | None | 132 |

Actual paired tokenizer lengths matched the recorded estimates. No memory body was shortened;
only the long continuation's Q signal was shortened. The replay excludes its captured usage
warning; it does not reconstruct an older substantive answer. The separate controller
integration test verifies that fallback selection. No hosted provider request was made.

Local reproducibility bundle: `.crucible/state/eval/memory-combined-implementation-2026-10-15/`
contains the copied runner, scores/queries/selected IDs and SHA-256 manifest. Inputs come from
the frozen `memory-full-content-2026-10-15/protocol.json` bundle. This is a regression replay,
not a new held-out quality result or a measurement of the live model deadline/device.

The earlier [combined-context](2026-10-15-memory-combined-context-experiment.md) and
[full-content](2026-10-15-memory-full-content-experiment.md) reports remain historical experiment
records. Their statements about the then-live implementation are not current branch behavior.
Fresh-chat misses, topic drift and independent calibration remain open quality work. The
experimental flags remain default-off; no curation/compaction scheduling or thresholds changed.

## Subsequent authorized local installation

The owner then requested installation. `scripts/dev/install-local.sh --no-backend` rebuilt,
packaged and installed the extension successfully. Installed extension and memory webview
asset SHA-256 hashes matched the current build. The managed Python environment already imports
this checkout, so no environment deletion/reinstallation was needed. All 70 local conversations
were checked for active work before reloading VS Code; none were active. Reloading started a
new managed backend process. Its health check passed, both local models reported ready, memory
was enabled and the curator endpoint reported enabled. The actual process environment confirmed
the experimental recall/curator flags remained true. Provider and context window remained
ChatGPT and 500,000 respectively. The VS Code Crucible connection indicator showed the new
backend port. No new hosted test request was made. This verifies installation/startup, not a
new recall-quality result. Install log: `/private/tmp/memory-combined-local-install-2026-10-15.log`.
