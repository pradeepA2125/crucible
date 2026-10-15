# Laya added to the Kev/BGE relevance comparison — 15 October 2026

## Decision

**Keep Kev-0.8B as the recommended candidate for the next local relevance iteration.** The tested Laya checkpoints recover many positives by accepting too many unrelated memories. Their smaller footprint or lower latency does not offset that failure for a memory injection gate. BGE remains the fastest and most selective current policy, but misses more useful memory. No live replacement or configuration change was made. Nimble remains excluded.

## Shared test and implementation

- Same 16 frozen requests and 68 query-memory pairs, unchanged labels and four-memory delivery cap. There are 14 core positives, six secondary positives, 42 negatives and six task-scope ambiguities. This is an exploratory diagnostic set, not independent held-out validation.
- Each new arm has five warm repetitions of every request, with shuffled order. Repeated timings are not additional labeled examples. BGE and Kev numbers come from the completed comparison on the same 36 GiB development Mac; they were not rerun or changed for this addition. No actual 16 GB Air measurements are claimed.
- Official Laya SDK 0.4.7 was installed without dependencies into a temporary directory. The installed product Torch 2.14.1 / Transformers environment was reused read-only. Each checkpoint ran in a separate process on verified MPS, using its native eager runtime and default precision policy. No router or multiple resident Laya checkpoints were used.
- English 421M: batch one query-memory state per candidate with predict_batch, max_len=512 and head_max_len=192. The native state room for our shared boolean rubric is 358 tokens. Protect the memory body, then fit conversation context with explicit head/tail clipping. State and body clips are recorded. This prevents a long catalog from hiding later memories.
- Multilingual 322M: test both batched one-memory states and the same full-catalog/one-boolean-per-memory format used by Kev. Explicit max_len=8192 and head_max_len=256; all supplied states fit. This is a longer-context checkpoint, not an English model silently run beyond its advertised input budget.
- Typed-decisions 421M: a separately frozen follow-up after the base checkpoints showed over-selection. It uses its published 1,024-token window, head_max_len=192, batched pairs, the same labels and rubric, and no private fine-tuning. Its prior tuning covers published workflows, not this memory task. This follow-up is additional model selection on reused diagnostics, not confirmation on unseen data.
- Every memory includes claim, full detail and applicability before any declared input bounding. Same utility rubric as Kev; default boolean decision is P(true)>0.5, then rank positives by that score and cap at four. BGE retains its actual 0.65 policy. Probabilities are not assumed calibrated for our task.
- English and typed SDK initialization warned that a choice:11+ temperature was clamped. That bucket is unused: these tests use binary noul questions. The SDK defaults were retained. No per-task temperature fit or prompt tuning was performed.
- Raw results, usage/token-drop reports, checkpoint revisions and weight hashes, configs, protocol files and script copies are saved under `.crucible/state/eval/memory-laya-comparison-2026-10-15/`. Original BGE/Kev evidence remains under `.crucible/state/eval/memory-kev-vs-reranker-2026-10-15/`.
- No private data left this computer, no production package/settings/database was changed, and all evaluation processes finished. Model downloads and isolated SDK files remain under `/private/tmp/crucible-decision-models-2026-10-15/`.

## Results

Counts are after the same cap four. Ambiguities are separate from negative-labeled selections. RSS is sampled whole-process peak and cannot establish total unified-memory headroom. Timing covers a whole user request’s candidate pool, includes the native input shaping and scoring call, and excludes cold loading, priming, result-file writes, retrieval and final answer generation. BGE and Laya are in-process; Kev includes localhost HTTP. Runtime/precision differences are part of these deployment configurations.

| Configuration | Core recovered | Secondary recovered | Negative-labeled selected | Ambiguous selected | Request median | Request p95 | Sampled peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|
| BGE, current policy | 4/14 | 2/6 | 0/42 | 0/6 | 30 ms | 174 ms | 1.09 GiB |
| Kev, full catalog / 8 slots | 9/14 | 1/6 | 2/42 | 1/6 | 387 ms | 938 ms | 3.46 GiB |
| Laya English 421M, bounded pairs | 11/14 | 2/6 | 27/42 | 4/6 | 214 ms | 839 ms | 2.85 GiB |
| Laya multilingual 322M, full pairs | 11/14 | 4/6 | 28/42 | 5/6 | 94 ms | 430 ms | 2.54 GiB |
| Laya multilingual 322M, catalog | 10/14 | 2/6 | 30/42 | 6/6 | 171 ms | 820 ms | 2.54 GiB |
| Laya typed-decisions 421M, pairs | 13/14 | 3/6 | 28/42 | 4/6 | 217 ms | 1042 ms | 2.63 GiB |

## Input loss and repeatability

| Laya arm | Contexts explicitly clipped, out of 68 pairs | Memory bodies clipped | Hidden SDK state tokens dropped | Same selections in all five repeats |
|---|---:|---:|---:|---|
| Laya English 421M, bounded pairs | 18 | 0 | 0 | yes |
| Laya multilingual 322M, full pairs | 0 | 0 | 0 | yes |
| Laya multilingual 322M, catalog | 0 | 0 | 0 | yes |
| Laya typed-decisions 421M, pairs | 0 | 0 | 0 | yes |

Catalog clipping has no one-state-per-memory count; its entry is zero because no state was clipped. Both multilingual modes and the typed checkpoint had complete supplied memory text. Their negative selections therefore cannot be explained solely by losing memory bodies or the English model’s shorter window. We have not established why the models misjudge the examples; they return typed decisions, not explanations.

## Actual saved-memory requests

| Configuration | Fresh landing guidance, core found | Inspector preview/isolation guidance, core found |
|---|---:|---:|
| BGE, current policy | 0/2 | 0/2 |
| Kev, full catalog / 8 slots | 0/2 | 2/2 |
| Laya English 421M, bounded pairs | 1/2 | 0/2 |
| Laya multilingual 322M, full pairs | 2/2 | 2/2 |
| Laya multilingual 322M, catalog | 1/2 | 1/2 |
| Laya typed-decisions 421M, pairs | 2/2 | 2/2 |

## Could a stricter Laya threshold fix it?

The following is an optimistic post-hoc sweep across every observed score boundary on these same labels. It is not independent validation. Keep at most two negative-labeled selections, matching Kev’s default tally, and report maximum core recovery:

| Configuration | Best core recovered | Secondary recovered | Negative-labeled selected |
|---|---:|---:|---:|
| Laya English 421M, bounded pairs | 1/14 | 0/6 | 2/42 |
| Laya multilingual 322M, full pairs | 0/14 | 2/6 | 2/42 |
| Laya multilingual 322M, catalog | 1/14 | 0/6 | 2/42 |
| Laya typed-decisions 421M, pairs | 5/14 | 0/6 | 2/42 |

Kev’s fixed default reaches 9/14 core with 2/42 negative-labeled selections. A stricter cutoff alone does not make these tested Laya inputs competitive. Full grids and exact diagnostic cutoffs are retained in `analysis.json`; none was installed or proposed as a calibrated deployment threshold.

## Interpretation and limits

One frozen negative is disputed: the landing scope/workflow memory repeats constraints in the current continuation request. Removing that pair cannot explain the dozens of other Laya negative selections; the original labels and separate sensitivity counts are preserved. The six task-scope ambiguities are not counted as definite false positives.

The published checkpoints, input representation and this fixed rubric were tested; this does not rule out a domain-specific Laya fine-tune or a different validated formulation. Also, these checkpoints use a native Torch runtime while Kev uses GGUF/Metal: results concern these practical configurations, not precision-matched model architectures.

For the current task, prefer Kev’s better balance of useful recall and rejection. BGE is the efficiency choice. Keep the existing fresh-landing miss and Kev’s remaining audit-memory false positive visible, and independently validate the selected policy on fresh labels and an actual 16 GB Air before general release.

## Primary model/runtime references

- [English Laya model card](https://huggingface.co/convaiinnovations/laya)
- [Multilingual checkpoint](https://huggingface.co/convaiinnovations/laya-multilingual)
- [Typed-decisions checkpoint](https://huggingface.co/convaiinnovations/laya-typed-decisions)
- [Official Laya SDK](https://github.com/NandhaKishorM/laya)
- [Earlier Kev/BGE comparison](2026-10-15-memory-kev-vs-reranker.md)
