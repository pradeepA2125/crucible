# Kev and Nimble relevance experiment — 15 October 2026

## Result and scope

Both models were run locally on the same frozen relevance cases. Catalog scoring with one independent boolean per memory recovered more core positives than isolated-pair scoring in this small diagnostic set. Neither model recovered every relevant memory. Eight-slot shared-prefix execution substantially reduced catalog latency; Nimble still exceeded the current three-second deadline on the larger requests. No live recall implementation, model selection, settings or durable-memory records were changed.

## Input and output contract

The task is multi-label selection: several memories or none can apply. Each candidate receives its own boolean question, not a single choice that forces one memory to win. The shared conversation input contains the current request first, then available recap and substantive closing answer. The captured usage-limit closing warning is omitted. Each memory contains claim, full detail and applies_when. Topic and provenance do not participate in this experiment. Repository isolation and retirement remain upstream responsibilities; fixed candidate pools were supplied here, so this is not an end-to-end retrieval test.

Primary form: one conversation plus one memory per request, with one relevance question. Catalog form: one conversation plus every candidate, with a separate named relevance question for each candidate. Seeing other memories can change scores even though questions have independent answers. Sending a catalog is therefore a semantic variant, not merely transport batching. True isolated pairs could also be batched as separate inference rows; concurrent isolated-row throughput was not measured here.

The native /v1/systemone noul field returns P(true). The application can retain this value and derive 1/0. The diagnostic rule was P(true) > 0.5, a binary argmax; ties abstain. This is not a calibrated production threshold, and BGE’s 0.65 threshold was not reused. No generated reasoning or explanation is produced. A typed probability distribution is not proof of accuracy.

Example adapter output shape (illustrative identifiers; actual isolated Nimble inspector-preview score rounded):

```json
{"memories": [{"id": "preview-guidance", "relevant": true, "p_yes": 0.974671}]}
```

## Protocol and evidence

- Frozen protocol: `.crucible/state/eval/memory-decision-models-2026-10-15/protocol.json`; SHA-256 recorded in `protocol.sha256` and every model result.
- 16 requests / 68 query-memory pairs: eight reused diagnostics (52 pairs) plus eight newly constructed diagnostics (16 pairs). Frozen labels contain 14 core positives, six secondary positives, 42 negatives and six task-scope ambiguities. Ambiguities are counted separately.
- The new examples were authored and labeled before inference. They are not independently labeled held-out evidence. Reused examples have already informed earlier design work; do not infer production accuracy from these rates.
- Both models saw the same content, rubric and labels. Context character budgets were recorded; no context field exceeded them. Inputs include full detail and applicability and use current-request-first ordering, whereas the saved BGE baseline used its production bounded recap-first 512-token pair. This compares decision paths, not a weights-only substitution.
- Pinned public GGUF model revisions, download SHA-256 hashes and byte counts are in `manifest.json`. The configurations are Kev BF16 and Nimble v3 Q4_K_M, not precision-matched weights. No original-runtime numerical parity benchmark was performed.
- Runtime: official llama.cpp b11642, build commit 2639a3b4e, Metal on the available 36 GiB development Mac. This is not a 16 GB MacBook Air measurement.
- Baseline server: one slot, 8,192 context tokens. Performance follow-up: eight slots and 65,536 total context tokens, retaining 8,192 per slot. No case was supplied a 65k-token input. The eight-slot follow-up was added after observing single-slot latency; all frozen payloads and decisions stayed the same.
- Raw inputs, responses, errors, server logs, sampled RSS, script copies and derived metrics are retained in that evaluation directory. HTTP timeout was 180 seconds to observe slow decisions; the production three-second deadline was measured against, not enforced. No missing decision is converted into an invented zero.
- Downloaded weights and binaries remain under `/private/tmp/crucible-decision-models-2026-10-15/`; obsolete partial downloads were removed. All temporary model servers stopped after evaluation. No private examples were uploaded to a hosted provider.

## Quality on all 16 requests

Each cell is found/eligible, except negatives and ambiguities which are selected/eligible. “Core” and “secondary” are the frozen human-style usefulness assessments, not model-generated labels.

| Configuration | Core before cap | Secondary before cap | Negative-labeled selections | Ambiguous selections | Core after cap 4 | Secondary after cap 4 | Negatives after cap 4 | Ambiguous after cap 4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kev-0.8B BF16, isolated pairs / 1 slot | 6/14 | 2/6 | 4/42 | 3/6 | 6/14 | 1/6 | 3/42 | 2/6 |
| Kev-0.8B BF16, catalog / 1 slot | 10/14 | 2/6 | 2/42 | 1/6 | 9/14 | 1/6 | 2/42 | 1/6 |
| Kev-0.8B BF16, catalog / 8 slots | 10/14 | 2/6 | 2/42 | 1/6 | 9/14 | 1/6 | 2/42 | 1/6 |
| Nimble-9B-v3 Q4_K_M, isolated pairs / 1 slot | 8/14 | 1/6 | 1/42 | 2/6 | 8/14 | 1/6 | 1/42 | 2/6 |
| Nimble-9B-v3 Q4_K_M, catalog / 1 slot | 9/14 | 1/6 | 1/42 | 1/6 | 9/14 | 1/6 | 1/42 | 1/6 |
| Nimble-9B-v3 Q4_K_M, catalog / 8 slots | 9/14 | 1/6 | 1/42 | 1/6 | 9/14 | 1/6 | 1/42 | 1/6 |

## Saved BGE baseline: comparable eight reused requests

The saved BGE rule recovered 1/8 core and 2/6 secondary positives, selected 0/32 negatives and 0/6 ambiguities. These are already capped production selections. The new eight constructed requests were not rerun through BGE, and no BGE latency comparison is claimed.

| Configuration, cap 4 | Core | Secondary | Negative-labeled | Ambiguous |
|---|---:|---:|---:|---:|
| Kev-0.8B BF16, isolated pairs / 1 slot | 3/8 | 1/6 | 3/32 | 2/6 |
| Kev-0.8B BF16, catalog / 1 slot | 5/8 | 1/6 | 2/32 | 1/6 |
| Kev-0.8B BF16, catalog / 8 slots | 5/8 | 1/6 | 2/32 | 1/6 |
| Nimble-9B-v3 Q4_K_M, isolated pairs / 1 slot | 5/8 | 1/6 | 1/32 | 2/6 |
| Nimble-9B-v3 Q4_K_M, catalog / 1 slot | 5/8 | 1/6 | 1/32 | 1/6 |
| Nimble-9B-v3 Q4_K_M, catalog / 8 slots | 5/8 | 1/6 | 1/32 | 1/6 |

## Two actual saved-memory requests

The fresh landing request asked for standing content and accessibility constraints from saved memory only. The inspector request asked which saved guidance applied to reproducible preview data and active-store isolation. The two core memories for each request are the same ones assessed before this run.

| Request | BGE core recovered | Kev isolated | Kev catalog, 8 slots | Nimble isolated | Nimble catalog, 8 slots |
|---|---:|---:|---:|---:|---:|
| Fresh landing guidance | 0/2 | 0/2 | 0/2 | 2/2 | 2/2 |
| Inspector preview/isolation guidance | 0/2 | 0/2 | 2/2 | 1/2 | 1/2 |

## Latency and memory

Times cover the complete candidate pool for one user request. For isolated scoring they sum the serial pair calls; for catalog scoring they are one multi-question HTTP request. p95 is the nearest-rank 95th percentile across only 16 requests (therefore the maximum); this is not a stable tail estimate. RSS is a sampled process metric and does not prove total unified-memory headroom on a 16 GB Air. Cold loading, downloads and source conversion are excluded from request time.

| Configuration | Request p50 | Request p95/max | Requests over 3 s | Sampled peak server RSS |
|---|---:|---:|---:|---:|
| Kev-0.8B BF16, isolated pairs / 1 slot | 0.426 s | 2.180 s | 0/16 | 2.64 GiB |
| Kev-0.8B BF16, catalog / 1 slot | 0.750 s | 3.841 s | 1/16 | 2.64 GiB |
| Kev-0.8B BF16, catalog / 8 slots | 0.376 s | 1.017 s | 0/16 | 3.37 GiB |
| Nimble-9B-v3 Q4_K_M, isolated pairs / 1 slot | 4.335 s | 17.595 s | 8/16 | 6.50 GiB |
| Nimble-9B-v3 Q4_K_M, catalog / 1 slot | 12.948 s | 60.668 s | 16/16 | 6.50 GiB |
| Nimble-9B-v3 Q4_K_M, catalog / 8 slots | 3.524 s | 8.311 s | 8/16 | 8.57 GiB |

Eight-slot versus one-slot catalog numerical parity:

- Kev-0.8B BF16: selection changes in 0/16 requests; maximum absolute probability difference 0.00000000.
- Nimble-9B-v3 Q4_K_M: selection changes in 0/16 requests; maximum absolute probability difference 0.00100471.

## Label audit and interpretation

One inherited negative merits review: in the landing continuation, “Keep landing work scoped and directly implemented” repeats restrictions expressly present in the current request. A relevance judge can reasonably regard it as applicable even if curation should not have made a one-task instruction durable. The frozen tally above is unchanged. A sensitivity calculation excluding only that case-memory pair is recorded in `final-analysis.json`; it reduces the negative denominator to 41 and is not a new held-out score. No other labels were changed after seeing results.

The inspector isolation memory also has a narrower applicability clause about extending inspection plumbing, while the broad recall request asks about future previews. That may contribute to rejection, but these models do not emit reasons and this experiment does not establish the cause. Missing detail is not the explanation here: full claim, detail and applicability were supplied.

Recommendation: keep one decision per memory and permit several or zero positives. For these models, evaluate the catalog form with actual shared-prefix execution rather than assuming that one API call is automatically fast. Kev is the smaller/faster candidate here and recovers more frozen core positives before the delivery cap (10 versus 9); after cap 4 they both recover 9/14 core and 1/6 secondary positives. Nimble recovers the previously missed fresh landing guidance and is more selective on the frozen negatives, but is substantially slower. Neither is established as the production replacement. Independently review labels and validate a fixed rubric/cutoff on new requests before changing live recall. Nimble v3 weights are non-commercial; the Apache main/v2 checkpoints are separate deployment candidates and were not tested in this run.

## Primary references

- [Kev-0.8B model card and interface](https://huggingface.co/jaredpalmer/kev-0.8b)
- [Kev BF16 GGUF conversion](https://huggingface.co/ggml-org/Kev-0.8B-GGUF)
- [Nimble v3 model and non-commercial license](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B-v3)
- [Nimble v3 quantized conversion and decision endpoint](https://huggingface.co/ggml-org/Bespoke-Nimble-9B-v3-GGUF)
- [Nimble reference implementation](https://github.com/bespokelabsai/nimble)
- [llama.cpp decision implementation at the tested build](https://github.com/ggml-org/llama.cpp/blob/2639a3b4e/tools/server/server-decision.cpp)
