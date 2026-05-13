# TurboQuant Plus — Qwen 3.6 Compatibility Notes

**Model:** Qwen3.6 35B-A3B Q4_K_M (Ollama blob, sha256-f5ee307a...)  
**Server:** TheTom/llama-cpp-turboquant, branch `feature/turboquant-kv-cache`, tag `feature-turboquant-kv-cache-b9082`  
**Build:** macOS M3 Pro, Metal + CPU, built from source  
**Result:** 34.2 t/s decode with turbo3 V-cache + q8_0 K-cache vs 22.0 t/s on Ollama (1.56×)

---

## Background

The pre-built binary from atomicmilkshake/llama-cpp-turboquant is a different fork — CUDA-only, Windows/Linux. For macOS Metal you must build from TheTom's source at `feature/turboquant-kv-cache`.

The Ollama-distributed Qwen3.6 GGUF is a **combined text+vision model** (Qwen3.6-VL). It uses a newer GGUF format introduced after April 16 2026 with several breaking changes vs what the TheTom fork expected. Five separate bugs had to be fixed before the model loaded and ran correctly.

---

## Build

```bash
git clone --depth=1 --branch feature/turboquant-kv-cache \
  https://github.com/TheTom/llama-cpp-turboquant /tmp/tqp-src

mkdir /tmp/tqp-src/build && cd /tmp/tqp-src/build
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_METAL=ON \
  -DGGML_BLAS=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON \
  -DLLAMA_TURBOQUANT=ON \
  -DCMAKE_OSX_ARCHITECTURES=arm64
cmake --build . --target llama-server -j$(sysctl -n hw.logicalcpu)
```

---

## Patch 1 — ROPE dimension sections array length

**File:** `src/llama-model.cpp`  
**Error:**
```
key qwen35moe.rope.dimension_sections has wrong array length; expected 4, got 3
```

**Root cause:** The Qwen3.6 GGUF stores `rope.dimension_sections` as a 3-element array `[11, 11, 10]` (sums to 32 = half the head dim of 64). The code was calling `get_key_or_arr(..., n=4, ...)` requiring exactly 4 elements.

**Fix:**
```cpp
// src/llama-model.cpp  ~line 2822
// Before:
ml.get_key_or_arr(LLM_KV_ROPE_DIMENSION_SECTIONS, hparams.rope_sections, 4, true);
// After:
ml.get_key_or_arr(LLM_KV_ROPE_DIMENSION_SECTIONS, hparams.rope_sections, 3, true);
```

---

## Patch 2 — ssm_dt tensor name (missing .bias suffix)

**File:** `src/llama-model.cpp`  
**Error:**
```
missing tensor 'blk.0.ssm_dt.bias'
```

**Root cause:** For the QWEN35MOE architecture (the recurrent/SSM layers), the GGUF has a tensor named `blk.{i}.ssm_dt` with no `.bias` suffix. The loader was calling `tn(LLM_TENSOR_SSM_DT, "bias", i)` which appended `.bias` to the name.

Note: `QWEN3NEXT` (line ~7501) and `QWEN35` (line ~7626) cases use the `.bias` form correctly for their own variants — only QWEN35MOE was wrong.

**Fix:**
```cpp
// src/llama-model.cpp  ~line 7563 (QWEN35MOE block only)
// Before:
layer.ssm_dt = create_tensor(tn(LLM_TENSOR_SSM_DT, "bias", i), { hparams.ssm_dt_rank }, 0);
// After:
layer.ssm_dt = create_tensor(tn(LLM_TENSOR_SSM_DT, i), { hparams.ssm_dt_rank }, 0);
```

---

## Patch 3 — Per-layer KV head count in tensor loader

**File:** `src/llama-model.cpp`  
**Error:**
```
attn_k.weight shape mismatch: expected [2048, 0], got [2048, 512]
```

**Root cause:** Qwen3.6 is a hybrid model — recurrent (SSM) layers have `n_head_kv = 0` and full-attention layers have `n_head_kv = 2`. The GGUF stores this as a per-layer array `[0,0,0,2,0,0,0,2,...]`. The global `n_head_kv()` (no layer arg) returns the value for layer 0 = 0. The tensor creation code was using this global value for all layers, so full-attention layer tensors were being allocated with size 0 when they actually need size 512 (= 256 × 2).

**Fix:**
```cpp
// src/llama-model.cpp  ~lines 7549-7556 (QWEN35MOE full-attention block)
// Before:
if (!hparams.is_recurrent(i)) {
    create_tensor_qkv(layer, i, n_embd, n_embd_head_k * n_head * 2,
                      n_embd_k_gqa, n_embd_v_gqa, 0);
    ...
}
// After:
if (!hparams.is_recurrent(i)) {
    const int64_t n_embd_k_gqa_i = hparams.n_embd_k_gqa(i);
    const int64_t n_embd_v_gqa_i = hparams.n_embd_v_gqa(i);
    create_tensor_qkv(layer, i, n_embd, n_embd_head_k * n_head * 2,
                      n_embd_k_gqa_i, n_embd_v_gqa_i, 0);
    ...
}
```

---

## Patch 4 — Vision encoder tensor count mismatch

**File:** `src/llama-model-loader.cpp`  
**Error:**
```
done_getting_tensors: wrong number of tensors; expected 1194, got 733
```

**Root cause:** The Ollama Qwen3.6 GGUF is a combined text+vision model containing 1194 tensors: ~733 text model tensors (`blk.*`) and ~461 vision encoder tensors (`v.blk.*`). The QWEN35MOE loading path only registers text tensors. The strict equality check `n_created != n_tensors` then fails because 733 ≠ 1194.

**Fix:** Change strict equality to allow the file to contain more tensors than registered (extra tensors are harmlessly skipped):

```cpp
// src/llama-model-loader.cpp  line 1319
// Before:
void llama_model_loader::done_getting_tensors() const {
    if (n_created != n_tensors) {
        throw std::runtime_error(format("...expected %d, got %d", n_tensors, n_created));
    }
// After:
void llama_model_loader::done_getting_tensors() const {
    if (n_created > n_tensors) {
        throw std::runtime_error(format("...expected %d, got %d", n_tensors, n_created));
    }
    if (n_created < n_tensors) {
        LLAMA_LOG_INFO("%s: %d tensors in file not registered (e.g. vision encoder in text-only mode)\n",
                       __func__, n_tensors - n_created);
    }
```

---

## Patch 5 — Per-layer KV head count in graph builder

**File:** `src/models/qwen35moe.cpp`  
**Error:**
```
/tmp/tqp-src/ggml/src/ggml.c:3675: GGML_ASSERT(ggml_nelements(a) == ne0*ne1*ne2) failed
```
Stack: `ggml_reshape_3d` ← `llm_build_qwen35moe::build_layer_attn`

**Root cause:** The graph context member `n_head_kv` is initialised from `hparams.n_head_kv(0)` (layer 0 default) which is 0 for this hybrid model. `build_layer_attn` is only called for full-attention layers (where the per-layer value is 2), but it used the stale global `n_head_kv = 0` when reshaping `Kcur` and `Vcur`:

```cpp
Kcur = ggml_reshape_3d(ctx0, Kcur, n_embd_head, n_head_kv, n_tokens);  // n_head_kv = 0 → assert fail
```

**Fix:**
```cpp
// src/models/qwen35moe.cpp  ~line 148
// Before:
Kcur = ggml_reshape_3d(ctx0, Kcur, n_embd_head, n_head_kv, n_tokens);
...
Vcur = ggml_reshape_3d(ctx0, Vcur, n_embd_head, n_head_kv, n_tokens);
// After:
const int64_t n_head_kv_il = hparams.n_head_kv(il);
Kcur = ggml_reshape_3d(ctx0, Kcur, n_embd_head, n_head_kv_il, n_tokens);
...
Vcur = ggml_reshape_3d(ctx0, Vcur, n_embd_head, n_head_kv_il, n_tokens);
```

---

## Starting the Server

The server must be started with `-fit off` (disables the device-memory fitting probe, which internally calls `llama_init_from_model` and would hit the same `build_layer_attn` path before the graph is actually needed):

```bash
/tmp/tqp-src/build/bin/llama-server \
  -m ~/.ollama/models/blobs/sha256-f5ee307a... \
  --port 11435 \
  --host 127.0.0.1 \
  -c 65536 \
  -np 1 \
  --n-gpu-layers 99 \
  --flash-attn on \
  -ctk q8_0 \
  --cache-type-v turbo3 \
  --jinja \
  -fit off
```

Use the `scripts/start-tqp.sh` convenience script which encodes these flags.

Key flags:
- `-ctk q8_0` — K-cache in int8 (reduces KV memory, faster bandwidth)
- `--cache-type-v turbo3` — V-cache in TurboQuant 3-bit format (~3× compression)
- `-np 1` — single slot; maximises available context per conversation
- `-c 65536` — 65k context window (462 MiB KV vs 58 MiB at 8k)
- `-fit off` — skip device-memory probe that triggers an early graph build

---

## Transport — Structured Output Issue

After the server was running, the agentd `TurboQuantTransport` hit a separate class of issue when making JSON structured-output calls.

### Problem: `json_schema` traps thinking in an infinite loop

The initial transport used `response_format: {type: "json_schema", json_schema: {...}, strict: true}`. Qwen3.6 is a thinking model — it emits `<think>...</think>` tokens before the answer. llama-server routes these to `reasoning_content` and routes the grammar-constrained output to `content`.

With `json_schema` strict grammar active, the model cannot emit `</think>` because it is not valid JSON. The grammar blocks the close tag, so the model loops in thinking indefinitely. A single call generated 14,100 tokens with no end.

**Fix:** Switch to `response_format: {type: "json_object"}`. With `json_object` there is no grammar constraint, so the model can freely emit and close `</think>`, then output the JSON object into `content`. Schema compliance is enforced via the system prompt instead.

### Problem: `max_tokens: -1` ignored by llama-server

`-1` is not a valid value in the OpenAI-compatible endpoint — llama-server silently falls back to its CLI `-n` default (was 1024). When the thinking phase exceeds the budget, `content` is empty and the call fails. Server now launched without `-n` (unlimited), and the transport omits `max_tokens` entirely so there is no cap.

### Problem: `reasoning_content` fallback for `content`

In some edge cases `content` can be empty even when the call succeeds (e.g. if the model finishes in thinking phase but the JSON is the last thing emitted). `_extract_text` now scans `reasoning_content` for a trailing JSON object as a last resort.

---

## Route Bug — `content` vs `message` key

The chat message route in `api/routes.py` was reading `request.get("message", "")` but every client sends `{"content": "..."}`. This silently stored empty strings for every user message, so the model always saw an empty conversation and replied "Please provide a query".

**Fix:**
```python
# api/routes.py
# Before:
message = request.get("message", "")
# After:
message = request.get("content") or request.get("message", "")
```

---

## Benchmark Results

Hardware: Apple M3 Pro, 36 GB unified memory  
Model: Qwen3.6 35B-A3B Q4_K_M (same weights, same binary GGUF)  
Method: 5 unique prompts after one warm-up round (warm-up discarded)

| Backend | Avg decode | KV cache format |
|---------|-----------|-----------------|
| TurboQuant (turbo3+q8_0) | **34.2 t/s** | V: turbo3 (~3-bit), K: q8_0 |
| Ollama (default) | 22.0 t/s | V: f16, K: f16 |
| **Speedup** | **1.56×** | — |

The gain is purely from reduced KV-cache memory bandwidth during decode. The V-cache is read on every token for every attention layer — compressing it 3× means proportionally less data moved per token.
