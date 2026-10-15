# Kev versus the current BGE reranker — 15 October 2026

Follow-up: [Laya was added to this comparison](2026-10-15-memory-laya-vs-kev-vs-reranker.md), including English, multilingual and typed-decisions checkpoints. The recommendation remains Kev for the next local relevance iteration; the tested Laya variants over-selected negative examples.

## Recommendation

**Choose Kev-0.8B catalog scoring as the stronger relevance candidate for the next local iteration.** On the frozen diagnostics it recovered 9/14 core memories versus 4/14 for the current BGE policy, after the same four-memory cap. Its complete-request latency remained below one second in all 80 timed observations. BGE is substantially faster and lighter. This recommendation concerns the tested decision paths; it does not establish universal model superiority or authorize a live switch. Nimble is excluded by Pradeep’s decision and was not run in this comparison.

Both still miss the fresh landing-memory request. Kev also loses some useful memories and admits one clearly irrelevant audit instruction. These are open failures, not solved by its average recall gain.

## Compared configurations and controls

- Same frozen 16 requests and 68 candidate-memory pairs from the preceding decision-model test: 14 core positives, six secondary positives, 42 negative-labeled pairs and six ambiguous pairs. Each model was measured five times per input mode; repeated timings are not additional labeled examples.
- BGE uses the installed runtime’s actual `Reranker.score_context`, `Signals.payload`, bounded input formatter and sigmoid model loader, with cached BAAI/bge-reranker-base weights. Its verified device was `mps:0`; CPU thread count was six. The earlier CPU replay is not used as the latency baseline.
- Current BGE: recap → previous meaningful answer → query, claim plus detail, 512-token pair limit, memory budget 256, cutoff >=0.65 and cap four. See [production call and deadline](</Users/pradeepkumar/projects/AI editor/services/agentd-py/agentd/memory/recall_v2.py:364>), [bounded input formatter](</Users/pradeepkumar/projects/AI editor/services/agentd-py/agentd/memory/recall_context.py:97>), and [model loader](</Users/pradeepkumar/projects/AI editor/services/agentd-py/agentd/model_loading.py:51>).
- BGE applicability control additionally includes applies_when; the production token budgets and 0.65 cutoff remain unchanged.
- Kev uses the verified BF16 ggml-org/Kev-0.8B-GGUF checkpoint and llama.cpp b11642 / Metal, eight slots, 8,192 context tokens per slot. Its full catalog form is the same frozen current-query-first rubric and full claim/detail/applicability input already evaluated. Each candidate has its own boolean; acceptance is P(true)>0.5 and cap four.
- Kev matched bounded control receives **exactly the retained conversation text and memory-body text from BGE’s applicability arm**, verified case by case. Its JSON/native decision interface and boolean rubric remain different. This separates some input-window/content advantage from judge behavior; it is not a byte-identical architecture comparison.
- Five warm repetitions per case and arm, order shuffled by the same recorded seed. Every input shape is primed before timing. Timings cover the whole candidate pool, including the native input formatter or local HTTP request/normalization; result-file writes and RSS sampling are excluded. BGE runs in-process, matching production; Kev includes loopback HTTP overhead. Cold startup, shared-backend queue contention, retrieval and final answer generation are excluded.
- Models ran sequentially on the same 36 GiB development Mac. No actual 16 GB Air performance claim is made. RSS is sampled process memory, not complete system unified-memory accounting. Neither model was run alongside the other.
- Inputs, labels, arms and fixed threshold grids were recorded before the scored comparison in `.crucible/state/eval/memory-kev-vs-reranker-2026-10-15/protocol.json`. One startup device-name guard incorrectly rejected `mps:0` before any cases were scored; its artifact was preserved, the guard corrected, and the unchanged protocol completed. No scored request failed.
- All decisions, five raw timing runs, selected IDs, truncated fields, actual retained text, scripts, protocol hash and process samples are retained in that directory. No hosted uploads, live model-selection changes, installs into the product runtime, settings changes or database writes were performed. The temporary Kev server stopped after evaluation.

## Relevance and resource results

All relevance counts are after cap four. “Negative-labeled” preserves the frozen labels and includes one disputed pair described below.

| Configuration | Core found | Secondary found | Negative-labeled selected | Ambiguous selected | Request median | Request p95 | Maximum | Sampled process RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BGE, current policy | 4/14 | 2/6 | 0/42 | 0/6 | 30 ms | 174 ms | 178 ms | 1.09 GiB |
| BGE, applicability control | 4/14 | 2/6 | 1/42 | 0/6 | 35 ms | 187 ms | 192 ms | 1.09 GiB |
| Kev, full catalog / 8 slots | 9/14 | 1/6 | 2/42 | 1/6 | 387 ms | 938 ms | 961 ms | 3.46 GiB |
| Kev, matched bounded catalog | 6/14 | 1/6 | 1/42 | 2/6 | 390 ms | 939 ms | 980 ms | 3.46 GiB |

The RSS measurement is for the complete process covering both modes of each model, not a per-arm memory allocation. All 80 timed observations per arm were below the existing three-second model deadline. Selected-memory sets were identical across all five repetitions within each arm. Exact score spreads and threshold analyses are in `analysis.json`.

## Actual failed-request checks

| Request | BGE current core found | Kev full catalog core found |
|---|---:|---:|
| Fresh landing: saved content/accessibility guidance | 0/2 | 0/2 |
| Inspector: reproducible previews and active-store isolation | 0/2 | 2/2 |

## Does changing only BGE’s cutoff close the gap?

The pre-recorded diagnostic threshold grid below uses the same labels and cap four. It is sensitivity analysis, not a calibrated deployment recommendation.

| Model/input | Threshold | Core found | Secondary found | Negative-labeled selected | Ambiguous selected |
|---|---:|---:|---:|---:|---:|
| BGE, current policy | 0.1 | 11/14 | 3/6 | 6/42 | 0/6 |
| BGE, current policy | 0.25 | 10/14 | 2/6 | 4/42 | 0/6 |
| BGE, current policy | 0.5 | 5/14 | 2/6 | 1/42 | 0/6 |
| BGE, current policy | 0.65 | 4/14 | 2/6 | 0/42 | 0/6 |
| Kev, full catalog / 8 slots | 0.4 | 11/14 | 3/6 | 15/42 | 4/6 |
| Kev, full catalog / 8 slots | 0.5 | 9/14 | 1/6 | 2/42 | 1/6 |
| Kev, full catalog / 8 slots | 0.6 | 1/14 | 0/6 | 0/42 | 0/6 |
| Kev, full catalog / 8 slots | 0.65 | 0/14 | 0/6 | 0/42 | 0/6 |

Lowering BGE’s cutoff recovers useful memories but also increases negative selections. An optimistic post-hoc sweep across every observed BGE score, constrained to at most two negative-labeled selections, reached the following maxima on these same inspected cases:

- BGE, current policy: 6/14 core, 2/6 secondary and 1/42 negative-labeled selections. The selected cutoff is retained in the raw analysis, not proposed for production.
- BGE, applicability control: 5/14 core, 2/6 secondary and 1/42 negative-labeled selections. The selected cutoff is retained in the raw analysis, not proposed for production.

Thus the current-policy core-recall gain is not explained solely by choosing a looser number for Kev. However, selection is still sensitive to the rubric, probability calibration, text budget and labels; these results do not prove that a tuned reranker on new data could never match Kev.

## Label caveat, costs and decision

The disputed negative is “Keep landing work scoped and directly implemented” for the landing continuation: the current request explicitly repeats that restriction. Kev selected it, which is defensible relevance behavior even if its original durable extraction was too broad. Removing only this pair from assessment changes Kev’s full-catalog negative tally from 2/42 to 1/41; BGE current stays 0/41. Frozen metrics are not rewritten. The remaining Kev full-catalog negative is an interrupted read-only audit instruction, which conflicts with the active implementation task. The one selected ambiguity is reported separately.

Kev’s full inputs and different decision rubric both matter: constraining its retained text reduces core recall from 9/14 to 6/14. It still exceeds BGE’s applicability control at the fixed existing cutoff, 4/14. This supports Kev as a practical next iteration, while limiting claims about the model weights in isolation.

For the goal of delivering more useful memory within the existing deadline, **Kev is the better tested candidate**. For minimum latency and process footprint, **BGE wins**. Accept the larger cost only with the known fresh-request miss and remaining irrelevant/ambiguous selections visible. Before general release, review labels independently and validate the fixed policy on fresh requests and a real 16 GB Air. No live switch was made by this assessment.
