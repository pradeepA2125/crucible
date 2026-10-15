# Applicability-only reranker experiment — 15 October 2026

Adding `applies_when` changed scores but did not change selected memories in any of eight diagnostic requests at the existing 0.65 cutoff. It did not fix either broad saved-guidance failure. No production code, runtime settings, curator prompt, spec or durable-memory rows were changed.

## Method

- Offline cached BGE reranker snapshot `2cfc18c9415c912f9d8155881c133215df768a70`; CPU, one thread, sigmoid, 512-token pairs. No provider request or network model download.
- Compare current `content + detail` against `content + "Applies when: " + applies_when + detail`; no topic or other metadata added. Same RCQ order, warning filtering, tokenizer, 256-token memory cap, cutoff 0.65 and top-four selection cap.
- Eight fixed candidate pools (52 case/candidate pairs): four previous landing diagnostics, the captured live inspector request, and three constructed inspector requests: concrete preview positive, unrelated health-route negative, stale-inspector-context landing-typo negative.
- Protocol and labels frozen before scoring. Previous landing labels reused. In the positive inspector cases, read-only isolation and reproducible previews were primary positives; three task constraints were labeled scope-ambiguous rather than definite negatives. Negative-control candidates were labeled irrelevant.
- Two comparisons: normal production-shaped context budgeting separately per representation, and a control using exactly the same bounded query for both representations. The latter isolates added document text from changed query budgeting. No memory documents were shortened in these cases.
- Baseline replay of the captured inspector scores matched to a maximum absolute difference of 5.075708031654358e-08.
- Local artifacts: `.crucible/state/eval/memory-applies-when-2026-10-15/{protocol.json,runner.py,results.json,sha256.json}`.

## Selections at 0.65

| Request | Claim + detail | With applicability |
|---|---|---|
| Actual landing continuation | Provide an actual static fallback for reduced motion.; Keep memory implementation details out of marketing copy. | Provide an actual static fallback for reduced motion.; Keep memory implementation details out of marketing copy. |
| Actual fresh landing requirements request | None | None |
| Caption-only control | None | None |
| Health-route control | None | None |
| Actual inspector saved-guidance request | None | None |
| Constructed concrete inspector preview | Make inspector previews reproducible across browser sessions. | Make inspector previews reproducible across browser sessions. |
| Unrelated health-route request, no history | None | None |
| Landing typo after inspector context | None | None |

Both the normal-budget comparison and the identical-query control produced the same selections between variants in all eight cases. This does not establish identical behavior outside these diagnostics.

## Actual inspector-request scores

| Memory | Current body | Body + applicability |
|---|---:|---:|
| Work directly and preserve unrelated existing changes. | 0.000125 | 0.002874 |
| Keep inspector access read-only and scope-isolated. | 0.007106 | 0.008695 |
| Avoid private data and durable-memory test manipulation. | 0.000134 | 0.003155 |
| Make inspector previews reproducible across browser sessions. | 0.009824 | 0.010062 |
| Keep preview support outside committed product paths. | 0.000331 | 0.004664 |

All five remain below 0.65. Applicability raised the three task-constraint scores in this actual request; it did not filter them out. Their absolute scores are still low, and the useful preview/isolation memories are still rejected.

## Other informative score changes

| Case / memory | Current body | Body + applicability |
|---|---:|---:|
| Actual landing continuation / Preserve mobile usability and reduced-motion accessibility. | 0.519206 | 0.349302 |
| Actual fresh landing requirements request / Keep landing work scoped and directly implemented. | 0.336599 | 0.508635 |
| Caption-only control / Provide an actual static fallback for reduced motion. | 0.404468 | 0.592633 |
| Constructed concrete inspector preview / Keep inspector access read-only and scope-isolated. | 0.318855 | 0.204749 |
| Constructed concrete inspector preview / Make inspector previews reproducible across browser sessions. | 0.963975 | 0.965688 |

Some useful memories scored lower, and some irrelevant ones scored higher. The concrete preview positive remains selected in both versions; read-only isolation is still missed. This is mixed movement, not an accuracy improvement.

## Interpretation and limits

Keep the live scorer unchanged on this evidence. Applicability is useful metadata, but merely concatenating it is not an enforced task/lifecycle boundary. The observed conditions can also legitimately match a later inspector-preview task. Selection at a different cutoff would require a separately calibrated evaluation; no cutoff was fitted here.

This is an exploratory, reused diagnostic set with three constructed additions, manually assigned labels, and no untouched benchmark or independent review. It evaluates fixed-candidate reranking, not retrieval, curation quality, three-second live deadlines, or downstream answers. No general accuracy percentage or claim of equivalence is warranted.

Source seams: `services/agentd-py/agentd/memory/recall_v2.py:367` builds the current documents; `memory/recall_context.py:91` bounds pairs; the frozen runner changes only the experimental document representation.
