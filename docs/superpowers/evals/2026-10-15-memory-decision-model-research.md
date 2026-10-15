# Open decision models for memory relevance — 15 October 2026

## Target and findings

Pradeep selected **16 GB MacBook Airs as the local-judge deployment target**, with the existing fallback path for 8 GB machines. This records a design target, not an implemented RAM-based routing policy. The available development host has 36 GiB RAM; it is not a measured 16 GB Air benchmark.

**Later owner decision:** Nimble is excluded from consideration. The subsequent [Kev versus current BGE comparison](2026-10-15-memory-kev-vs-reranker.md) measures relevance and repeated GPU timing, and recommends Kev as the stronger relevance candidate for the next local iteration while retaining the known failures. The Nimble material below remains a historical research record.

**Laya follow-up completed:** The [extended comparison](2026-10-15-memory-laya-vs-kev-vs-reranker.md) tests English, multilingual and typed-decisions Laya locally. All three over-select negative examples with the shared relevance rubric; the tested catalog variant does too. These findings preserve the Kev recommendation and do not change live recall.

Research shortlist: **Laya 322M/421M and Kev-0.8B**, plus the separate **Nano-Jev 33.4M** as a low-footprint relevance baseline. The original **NanoJev 0.6B** has a game-focused released checkpoint. **Kev-4B and Lev-4B** are optional quality comparisons, pending a measured Mac runtime/memory budget. The verified official **OpenJev 27B** is too large for this default target: its smallest published Mac build is about 15 GB of weights before runtime/OS/editor overhead. This is a hardware-fit inference, not a measured failure on an Air.

At the end of the initial research stage, no decision-model weights had been downloaded or executed. Public metadata and small source/configuration files were fetched, and an isolated MLX environment was prepared under `/private/tmp/crucible-decision-models-2026-10-15/`. The large OpenJev download was not started after the Air constraint was clarified. Pradeep subsequently authorized **Kev-0.8B and Nimble 9B** local tests; the completed [experiment report](2026-10-15-memory-decision-model-experiment.md) supersedes the research-only status for those two configurations. No memory examples were sent to a new hosted provider, and no production code, settings, spec or durable-memory rows were changed by that comparison.

## Initial research priority

1. **Kev-0.8B first:** the most direct small typed-decision candidate with an explicit Apple Silicon/MLX path. This is a benchmark priority, not a measured accuracy lead on Crucible.
2. **Laya next:** a smaller encoder-based alternative; compare the English and multilingual checkpoint budgets where relevant rather than assuming one wins.
3. **Nano-Jev 33.4M as a cheap relevance baseline:** easy to distinguish footprint from actual usefulness; it remains a cross-encoder.

Keep 4B models and **Bespoke Nimble 9B** as optional quality comparisons after measuring the small tier. Nimble has an Apple Silicon implementation and a quantized v3 GGUF, but its runtime footprint and license depend on the checkpoint; see below. OpenJev 27B is excluded from the default 16 GB target.

## Current baseline

Crucible uses `BAAI/bge-reranker-base`. Reading the cached safetensors header counted **278,044,931 parameters**; the file is **1,112,206,140 bytes**, predominantly FP32. Those are verified local measurements, not process RSS. The live context–memory pair limit is 512 tokens. [Official model card](https://huggingface.co/BAAI/bge-reranker-base).

## Candidates

| Model | Published size / interface | Fit for this experiment | Important limit |
|---|---|---|---|
| Laya | English 421M; multilingual 322M; typed-decisions 421M. Encoder plus decision head, typed choice/yes-no/score, Apache-2.0 | Small enough to investigate alongside the current embedder/editor; compare compact single-checkpoint configurations | General memory relevance is unverified; do not equate calibrated training with calibrated deployment |
| Kev-0.8B | Qwen3.5 base + adapter and pointer head; prefill-only typed decisions; Apache-2.0 | Practical sub-1B candidate with an explicit MLX serving path | The authors report substantial out-of-domain weaknesses and option-order sensitivity |
| Kev-4B / 9B | Same family at larger sizes | Optional quality ceiling after the small-model tests | Default model-card Mac guidance is larger than our target; quantized fit and parity need measurement |
| NanoJev, C-Tianyu | Qwen3-0.6B + decision heads; zero answer-token decoding | Research candidate for training/customization | Current release is trained/evaluated on Maze, Snake and ViZDoom; that does not establish memory-relevance skill |
| Nano-Jev, sdmlai | 33.4M MiniLM cross-encoder; relevance/sufficiency/groundedness; MIT v1 weights | Very small RAG-specific comparator | Still a cross-encoder, not an architectural escape from reranking; Wikipedia/QA training differs from procedural memories |
| Lev, Interfaze | Qwen3.5-4B + LoRA/readouts; typed decisions; Apache-2.0 adapter | Optional decision-model comparison | Published real-time path requires CUDA; no verified 16 GB Air latency/peak-memory result found |
| Bespoke Nimble | Qwen3.5-9B + LoRA; typed boolean/choice/rubric decisions | Optional larger comparator with a documented Apple Silicon path; v3 GGUF Q4_K_M is 6.32 GB of weights | Main 9B and v2 are Apache-2.0; v3 is CC BY-NC 4.0. Official MLX scorer does not support quantized weights; GGUF uses a separate runtime. No measured Air result |
| OpenJev, official `openjev/openjev` | 27B; 4-bit MLX about 15 GB; typed next-token option readout | Larger research reference, excluded from default Air shortlist | CC BY-NC 4.0 weights; a commercial distribution needs a separate licensing decision |

These are separate projects; their names and sizes should not be conflated. A distinct decision model named **Lea/LEIA** matching the described size family was not verified. **Lev** is a close spelling match and does have a verified 4B checkpoint. **Kev** is the verified family containing 0.8B, 4B and 9B variants. **Laya** is not verified as a 4B family.

### Laya

The official repository lists 421M English and typed-workflow checkpoints and a 322M multilingual checkpoint. Default context budgets differ: 512 for English and 1,024 for the others; the multilingual encoder can be configured for longer input. A constrained memory/request pair is more appropriate than sending full chat history. Load one chosen checkpoint rather than preloading the whole router family. The project's own limits say the base models are near chance on its typed-decisions benchmark, while the stronger workflow score belongs to the checkpoint fine-tuned on that workflow data. This makes Laya a plausible compact platform to test or specialize, not a verified zero-shot solution. [Repository](https://github.com/NandhaKishorM/laya), [model card and limitations](https://huggingface.co/convaiinnovations/laya).

### Kev

Kev's small models use a causal backbone in prefill-only mode with an option pointer head, rather than generating an explanation. The 0.8B card documents MLX support on Apple Silicon, trained-state length versus validated length, and distribution-shift/calibration limits. It explicitly warns against interpreting broad benchmark gains as general competence. Start with bounded memory decisions; assess 4B only if the small tier misses the accuracy bar. The published 4B 8-bit experiment reports a several-GB scoring process on a 24 GB Mac, plus a larger one-time conversion peak; it does **not** establish a safe 16 GB Air default. [0.8B card](https://huggingface.co/jaredpalmer/kev-0.8b), [family](https://github.com/jaredpalmer/kev), [8-bit experiment](https://github.com/jaredpalmer/kev/issues/162).

### The two NanoJev projects

The original [C-Tianyu checkpoint](https://huggingface.co/C-Tianyu/NanoJev) currently publishes a unified game-trained model with structured decision heads. Its game results do not transfer automatically to memory selection. [Source and contract](https://github.com/TianyuCodings/NanoJev).

The separate [sdmlai/nano-jev](https://huggingface.co/sdmlai/nano-jev) explicitly supports passage relevance, answer sufficiency and groundedness. Its card reports a sharp drop on unseen datasets and calibration error around 0.17–0.18 there; its “partially relevant” label follows a QA-specific definition. It is useful as a cheap comparator, but its 33.4M size and typed outputs do not prove superiority to BGE on our task. [Implementation](https://github.com/shubham10divakar/nano-jev).

### Lev and OpenJev

[Lev's model card](https://huggingface.co/interfaze-ai/lev) identifies the 4B backbone, adapter/readout files, distribution-sensitive calibration and a CUDA-focused real-time runtime. [The repository](https://github.com/InterfazeAI/lev) and model card currently show different aggregate benchmark figures; pin release and adapter strength before comparing. No figure here is treated as an independent memory benchmark.

[OpenJev's official card](https://huggingface.co/openjev/openjev) identifies 27B weights and separate measured quantizations. Its [4-bit Mac build](https://huggingface.co/openjev/openjev-MLX-4bit) is around 15 GB and non-commercially licensed. Do not substitute a similarly named wrapper or community model without recording the artifact identity. A typed output prevents malformed free-form responses, not incorrect relevance decisions. GPU latency claims are not Mac latency results.

### Bespoke Nimble

Added after Pradeep identified this family. The [main 9B checkpoint](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B) and [9B-v2](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B-v2) publish Apache-2.0 adapters on Qwen3.5-9B. They score allowed answer-token logits for booleans, choices and rubric levels without generating explanations. The roughly 165 MiB adapter is not the complete model: it requires the 9B base. Main currently uses T=1.0; v2 uses a transferred temperature of approximately 2.179, not an independently fitted v2 calibration. Pin the checkpoint and its prompt/scoring contract; these distributions do not inherit BGE's 0.65 acceptance rule.

The [official repository](https://github.com/bespokelabsai/nimble) documents Apple Silicon/MLX inference, including shared-context processing across independent fields. However, its MLX scorer explicitly does not support quantized weights; unquantized weights alone need about 18 GB, before runtime overhead. That path is unsuitable for a 16 GB default. Loading the scorer without explicit model settings loads a 4B Qwen baseline, not a trained Nimble checkpoint. No smaller Nimble checkpoint was verified in this research.

The [9B-v3 model card](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B-v3) uses **CC BY-NC 4.0**, unlike the Apache checkpoints. Its [ggml-org conversion](https://huggingface.co/ggml-org/Bespoke-Nimble-9B-v3-GGUF) lists Q4_K_M at **6.32 GB**, Q8_0 at 9.53 GB and BF16 at 17.9 GB. The decision interface is `/v1/systemone`, rather than ordinary text generation. The linked [llama.cpp API change](https://github.com/ggml-org/llama.cpp/pull/29818) merged on October 2; the model card identifies this runtime path, but we have not installed or verified Nimble inference through it. Quantized operation on a 16 GB Air is plausible from weight size alone, not established: measure activations, context, OS/editor/embedder contention and decision accuracy after quantization.

The official repository also advertises newer Nimble-v4 API models and vendor benchmark scores. This research does not establish downloadable v4 weights or performance on Crucible memories. At the initial research stage, no Nimble weights had been downloaded or executed. The later authorized comparison tested v3 Q4_K_M locally, as recorded in the linked experiment report; no private examples were uploaded.

## Evaluation needed before choosing a primary judge

1. Keep retrieval, scope isolation, retirement checks and the generative fallback unchanged. Replace only the experimental relevance decision in an offline/shadow harness.
2. Use the saved real failures, useful procedural constraints, explicit memory questions, concrete requests, topic switches and unrelated controls. Add a new independently labeled validation set; the eight reused diagnostics alone cannot select a production winner reliably.
3. Evaluate the full claim, detail and applicability against a current-request-led bounded context. Conditions and source evidence remain data, not authorization. Include “not needed” and ambiguous cases; a multi-choice decision across memories must permit selecting none and more than one when appropriate.
4. Test semantic equivalents, option renaming/reordering, negation, missing recap/closing, and task-local versus recurring rules. Do not assume a model's probability is calibrated merely because its card uses that word.
5. Report required-memory recall, irrelevant deliveries, abstention/coverage, per-case failures and measured cold/warm latency. Record tokenizer truncation and every failed request; never turn model failure into an invented zero relevance score.
6. Measure the complete local process on an actual 16 GB Air: loaded and peak memory, load/conversion spikes, p50/p95 per trigger with realistic candidate counts, and contention with VS Code/embedder/background curation. Weight arithmetic alone is not a memory-budget result. The current hot path has a three-second model deadline and a sub-second performance target in the spec; alternative models must be measured against those constraints.

Research establishes plausible candidates, not a replacement winner. The subsequent [local comparison](2026-10-15-memory-decision-model-experiment.md) measured Kev-0.8B BF16 and Nimble-9B-v3 Q4_K_M on the 36 GiB development host: catalog scoring with eight inference slots delivered 9/14 core positives after cap 4 for each model. Kev's maximum request time was about 1.02 seconds; Nimble's was about 8.31 seconds. The report preserves ambiguous and disputed labels, configuration differences and all raw outcomes. Neither model was deployed, and actual 16 GB Air behavior remains unmeasured.
