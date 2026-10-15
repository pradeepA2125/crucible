# Full-memory-content reranking experiment — 2026-10-15

## Result

Including the full memory detail helps some actual task matches, especially when the scorer receives the short claim plus detail without topic/applicability headers. It does not solve the actual fresh-session request. Including every field can instead promote a questionable task-scope memory. No production code, settings, durable-memory rows or thresholds were changed.

## Controlled method

The live implementation already scores the short memory content, preceded by topic and applicability condition. The longer `detail` field is absent from that representation (`services/agentd-py/agentd/memory/models.py:156`, used at `memory/recall_v2.py:364`). This experiment tests that omission explicitly.

- Same cached local BGE reranker snapshot, CPU, one thread, sigmoid activation and 512-token pair limit. No network/provider requests. [Model card](https://huggingface.co/BAAI/bge-reranker-base).
- Eight actual saved memories; two actual captured requests and two constructed negative controls from the preceding experiment. Candidate pools and predeclared diagnostic labels held fixed.
- Three document representations: topic + condition + short claim (production representation); short claim + full detail (body only); and topic + condition + short claim + full detail (all fields).
- Three query formats: raw current request only, bounded query/previous/recap, and bounded recap/previous/query. Missing fields omitted; the known usage-limit system warning excluded.
- Within each case/query format the query string is identical across all three memory representations. The budget reserves the longest complete memory text, so adding detail never silently drops part of that memory or changes the retained query. All pairs fit within 512 tokens; current-request text in the long continuation is clipped in the combined formats, as recorded.
- Fixed diagnostic cutoffs 0.50 and 0.65, carried from the preceding experiment; no threshold fitting on these four requests. At most four selected.
- Protocol was written before model loading/scoring. Full inputs, scores and copied runner are in `.crucible/state/eval/memory-full-content-2026-10-15/`.

## Recap → previous → query, cutoff 0.50

| Request | Short claim + headers | Claim + full detail, no headers | All fields |
| --- | --- | --- | --- |
| Actual continuation | Static fallback | Static fallback, marketing-memory boundary, mobile accessibility | Static fallback |
| Actual fresh-session requirements query | None | None | Landing-only/no-delegation task-scope rule only; questionable as a permanent preference |
| Caption-only negative control | Static fallback + accessibility, both irrelevant | None | Static fallback, irrelevant |
| Health-route negative control | None | None | None |

The richer body-only representation retrieves three useful memories for the continuation and avoids the caption-only false positives at this cutoff. The release/experimental-claims memory still fails there. This is a small diagnostic improvement, not a broad precision/recall estimate.

## All candidate scores with recap-first combined input

### Actual continuation

| Memory | Short claim + headers | Claim + full detail | All fields |
| --- | ---: | ---: | ---: |
| Finish interrupted audits using already returned repository evidence. | 0.00010 | 0.00017 | 0.00008 |
| Provide an actual static fallback for reduced motion. | 0.97703 | 0.98239 | 0.83415 |
| Preserve mobile usability and reduced-motion accessibility. | 0.22594 | 0.51921 | 0.11451 |
| Keep landing work scoped and directly implemented. | 0.27800 | 0.46603 | 0.07572 |
| Keep memory implementation details out of marketing copy. | 0.31150 | 0.86381 | 0.19194 |
| Ground installation instructions and links in repository evidence. | 0.06199 | 0.00025 | 0.03568 |
| Separate shipped capabilities from experimental memory claims. | 0.13933 | 0.11416 | 0.21412 |
| Exclude sensitive runtime data from repository research. | 0.00218 | 0.00019 | 0.00192 |

### Actual fresh-session request

| Memory | Short claim + headers | Claim + full detail | All fields |
| --- | ---: | ---: | ---: |
| Finish interrupted audits using already returned repository evidence. | 0.00365 | 0.00346 | 0.00529 |
| Keep memory implementation details out of marketing copy. | 0.02985 | 0.03312 | 0.00437 |
| Keep landing work scoped and directly implemented. | 0.34493 | 0.33660 | 0.69376 |
| Separate shipped capabilities from experimental memory claims. | 0.00949 | 0.18154 | 0.33459 |
| Preserve mobile usability and reduced-motion accessibility. | 0.31733 | 0.28407 | 0.35117 |
| Ground installation instructions and links in repository evidence. | 0.06177 | 0.18095 | 0.06449 |
| Exclude sensitive runtime data from repository research. | 0.00917 | 0.00360 | 0.01131 |
| Provide an actual static fallback for reduced motion. | 0.01230 | 0.00004 | 0.00682 |

### Constructed caption-only negative control

| Memory | Short claim + headers | Claim + full detail | All fields |
| --- | ---: | ---: | ---: |
| Finish interrupted audits using already returned repository evidence. | 0.00061 | 0.00004 | 0.00026 |
| Provide an actual static fallback for reduced motion. | 0.94905 | 0.40447 | 0.83931 |
| Preserve mobile usability and reduced-motion accessibility. | 0.68436 | 0.06101 | 0.12201 |
| Keep landing work scoped and directly implemented. | 0.02345 | 0.00326 | 0.02068 |
| Keep memory implementation details out of marketing copy. | 0.02916 | 0.00296 | 0.00678 |
| Ground installation instructions and links in repository evidence. | 0.01100 | 0.00006 | 0.00320 |
| Separate shipped capabilities from experimental memory claims. | 0.01475 | 0.12241 | 0.16604 |
| Exclude sensitive runtime data from repository research. | 0.00301 | 0.00005 | 0.00199 |

### Constructed health-route negative control

| Memory | Short claim + headers | Claim + full detail | All fields |
| --- | ---: | ---: | ---: |
| Finish interrupted audits using already returned repository evidence. | 0.00017 | 0.00004 | 0.00011 |
| Provide an actual static fallback for reduced motion. | 0.38561 | 0.16093 | 0.25806 |
| Preserve mobile usability and reduced-motion accessibility. | 0.17299 | 0.03158 | 0.02586 |
| Keep landing work scoped and directly implemented. | 0.04648 | 0.01056 | 0.00800 |
| Keep memory implementation details out of marketing copy. | 0.01329 | 0.01107 | 0.00319 |
| Ground installation instructions and links in repository evidence. | 0.00462 | 0.00005 | 0.00184 |
| Separate shipped capabilities from experimental memory claims. | 0.00560 | 0.05785 | 0.09772 |
| Exclude sensitive runtime data from repository research. | 0.00138 | 0.00004 | 0.00144 |

## Query-only isolation check

Using the same actual continuation request alone, the short representation selects nothing at 0.65. Claim + full detail selects static fallback (0.97608) and accessibility (0.70265); their short-representation scores were 0.37306 and 0.49756. Thus richer memory body text can help without changing the query-combination strategy.

For the actual fresh-session query, richer body text still selects nothing at either cutoff. Adding all metadata and detail instead makes the questionable task-scope rule pass: 0.73147 with raw query-only input, versus 0.31900 for the short representation. The release-claims and accessibility lessons remain below cutoff.

## Interpretation and limits

The evidence supports that excluding detail was one contributing limitation for some task queries. It does not support that missing detail was the sole cause of low scores, nor that adding all stored fields is universally better. Both query formulation and memory representation materially affect the cross-encoder scores.

Body-only full text is the next representation worth broader evaluation. It remains a hypothesis selected after seeing these diagnostic results. This set has only four requests, previously inspected baseline scores, local manual applicability labels, and no independent reviewer. No new untouched validation split, end-to-end controller test or production deadline measurement was performed. The fresh-session failure remains open.
