# Combined-context memory reranking experiment — 2026-10-15

## Decision

Combined context improves recall on the reused synthetic evaluation split, especially with recap → previous substantive answer → current query ordering. It is not sufficient to replace the live rule: it still misses the actual fresh-session request and admits irrelevant memory when old context conflicts with a new request. No production code, settings, memory rows or thresholds were changed for this experiment. No provider requests or uploads were made.

## Method

The user proposed concatenating recap, previous answer and query, omitting missing fields and system warnings, then reranking once. I tested this offline with the same cached `BAAI/bge-reranker-base` snapshot, sigmoid activation and 512-token pair limit used by recall v2. Scoring ran on CPU with one Torch thread to avoid concurrent device work with the live backend.

The frozen-before-scoring protocol and complete scores are under `.crucible/state/eval/memory-combined-context-2026-10-15/`: `protocol.json`, `results.json`, `combined-memory-experiment.py`, and `combined-memory-followup.py`. These scripts use local cached weights only. The matrix hash matches the earlier separate-score evaluation.

- Existing labeled matrix: 40 synthetic cases, five candidates each, all eight Q/C/R relevance patterns. Original split retained: 24 development cases and 16 reused held-out cases. The latter is NOT a new untouched benchmark; its earlier results were already known.
- Real diagnostics: the captured landing continuation and independent fresh-session probe, with their actual eight saved candidates. No anchor or repository information was added to the fresh probe.
- Two predeclared negative controls: change only a static illustration caption, and locate the health route. Both retained prior landing/accessibility context to test topic leakage.
- Compared Q → C → R and R → C → Q, with labeled fields and missing fields omitted. The captured usage-limit closing was removed based on its known system-warning origin. A production warning classifier was not implemented.
- Initial bounded format: at most 380 input-context tokens, reserving the complete longest memory text inside the 512-token pair limit. Q/C/R shares were 65/20/15 percent, redistributed over present fields; clipped fields retained head and tail. No synthetic example needed clipping. The long real continuation did.
- Each format used the same development-only cutoff grid: 0.01, 0.03, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.65, 0.80, 0.90. Objective, fixed before scores: minimize harmful injections, maximize needed-memory precision, then maximize hits. Delivery cap remained four. Both formats selected 0.50. Also report 0.65 to separate input-format changes from cutoff changes.
- Real diagnostic labels were recorded before new combined scores, but after the baseline failures had been inspected. They are exploratory. Release-claims and accessibility lessons were primary positives; marketing-details, static-fallback and accurate-installation lessons were useful secondary candidates. Their semantic applicability remains a manual judgment.

## Reused held-out matrix results

There are 12 needed memories across 16 cases. “Irrelevant” below counts delivered candidates labeled harmless/unneeded; harmful injection counts were zero for every listed method.

| Method | Cutoff | Needed found | Irrelevant delivered | Needed-memory precision |
| --- | --- | ---: | ---: | ---: |
| Current separate Q/C/R gate | Q ≥ .65 OR C and R ≥ .80 | 6/12 | 0 | 100% |
| Combined Q → C → R | .50, selected on development | 9/12 | 1 | 90% |
| Combined R → C → Q | .50, selected on development | 12/12 | 1 | 92.3% |
| Combined Q → C → R | .65, fixed comparison | 8/12 | 1 | 88.9% |
| Combined R → C → Q | .65, fixed comparison | 11/12 | 0 | 100% |
| Earlier fitted separate weighted rule | .20; weights .60/.25/.15 | 12/12 | 0 | 100% |

The extra combined-context delivery at .50 was the case where only the recap matched a provider-change memory while the current query explicitly requested a static illustration caption edit. This is exactly the context-overrides-query failure the current separate rule tries to prevent. The earlier weighted result is included as a previously evaluated comparator, not a newly selected deployment recommendation.

## Actual live diagnostic results

At each combined format's development-selected .50 cutoff:

| Request | Live separate rule | Bounded combined Q → C → R | Bounded combined R → C → Q |
| --- | --- | --- | --- |
| Actual landing continuation | Nothing delivered | Static fallback + mobile accessibility | Static fallback |
| Actual fresh-session requirements query | Nothing delivered | Nothing delivered | Nothing delivered |
| Caption-only negative control with old landing context | Not replayed here | Static fallback, irrelevant to this request | Static fallback + accessibility, both irrelevant here |
| Health-route lookup negative control | Not replayed here | Nothing delivered | Nothing delivered |

Selected bounded-format scores:

| Memory | Continuation Q→C→R | Continuation R→C→Q | Fresh-session query | Caption-only Q→C→R | Caption-only R→C→Q |
| --- | ---: | ---: | ---: | ---: | ---: |
| Static reduced-motion fallback | .8690 | .9770 | .0123 | .8857 | .9490 |
| Mobile/reduced-motion accessibility | .5018 | .2259 | .3173 | .4857 | .6844 |
| Released versus experimental claims | .4356 | .1393 | .0095 | .0339 | .0148 |
| Memory details out of marketing | .4258 | .3115 | .0298 | .0903 | .0292 |
| Landing-only/no-delegation task scope | .4183 | .2780 | .3449 | .0498 | .0234 |
| Research privacy | .3607 | .0022 | .0092 | .0025 | .0030 |
| Accurate installation instructions | .2443 | .0620 | .0618 | .0086 | .0110 |
| Temporary audit recovery instruction | .0027 | .0001 | .0037 | .0004 | .0006 |

The fresh-session query still fails. Furthermore, lowering its cutoff to admit the accessibility lesson would first admit the questionable landing-only/no-delegation memory; the release-claims lesson remains near .01. Merely reducing a cutoff is not a complete fix.

## Warning and truncation diagnostics

Keeping the warning in the initial bounded Q→C→R input selected nothing for the actual continuation; removing it and redistributing context budgets selected two useful memories. That does NOT isolate warning removal alone: it also changes the retained Q/R text.

A follow-up controlled diagnostic removed the warning without reallocating field budgets; it still selected nothing at .50. Therefore the full gain cannot be attributed solely to filtering the warning.

A second follow-up used full concatenation with the model's normal pair truncation instead of field budgets:

- Filtered continuation Q→C→R: accessibility .6038 and static fallback .4655; only accessibility passes .50.
- Filtered continuation R→C→Q: static fallback .5335 and accessibility .3899; only static fallback passes .50.
- Filtered pairs fit within 491 tokens. Including the warning reached 525 tokens before model truncation, so truncation was required.
- Fresh-session and negative-control results were unchanged because those inputs were short.

This exposes substantial sensitivity to ordering and truncation. A production implementation must define its budget policy rather than silently concatenate arbitrary histories.

## Reproducibility and limits

Replaying raw Q-only scores on the same actual candidates reproduced the live scores with maximum absolute error about 0.0000033. This supports that the observed differences come from changed inputs, not an unrelated model or device mismatch. Synthetic combined batches of five memories had median CPU scoring latency around 0.11 seconds; these are offline observations, not a production deadline guarantee.

This experiment holds candidate retrieval fixed, reuses a small synthetic dataset, and has no independent label reviewer. It measures reranker selection, not whether the downstream agent follows delivered memory. The negative controls are constructed diagnostics, not a broad real-world false-positive estimate.

## Conclusion

The user's combined-context proposal is a useful candidate: at the same .65 cutoff, R→C→Q improves the reused split from 6/12 to 11/12 without an extra delivery there. At the fitted .50 cutoff it reaches 12/12 but introduces topic leakage. The actual fresh-session failure remains, and the negative control demonstrates that even a high combined score can be irrelevant to the current action.

Keep system warnings out of substantive context, but do not deploy concatenation alone as the fix. Next evaluation should test an intent-focused contextual query or an explicit applicability judgment against new labeled positive/negative cases, including missing-context requests and topic changes. Preserve the distinction between helpful background and permission to act.
