# TurboQuant Plus — Qwen 3.6 Compatibility Notes

**Model:** Qwen3.6 35B-A3B Q4_K_M (Ollama blob, `sha256-f5ee307a…`)
**Server:** TheTom/llama-cpp-turboquant, branch `feature/turboquant-kv-cache`
**Build:** macOS M3 Pro, Metal + CPU, built from source
**Result:** loads and runs with turbo3 V-cache + q8_0 K-cache. ~34 t/s decode vs ~22 t/s on Ollama (1.56×).

> **IMPORTANT — build location.** Do NOT keep the checkout/build under `/tmp` — macOS prunes it
> and you lose the patched source *and* the compiled binary on reboot. Use a persistent path.
> These notes use `~/tqp-src`. `scripts/start-tqp.sh` defaults `LLAMA_SERVER` to
> `$HOME/tqp-src/build/bin/llama-server`.

---

## Two layers of setup

Getting Qwen3.6 working on TurboQuant requires changes in **two** places:

1. **The llama-server fork** (`~/tqp-src`) — 5 C++ source patches so the `qwen35moe`
   architecture (a hybrid SSM + full-attention MoE, shipped as a combined text+vision GGUF) loads.
2. **agentd config** (`.env`) — select the **qwen3 transport profile**, or every call returns
   empty content.

Both are mandatory. The fork patches make the model *load*; the profile makes it *answer*.

---

## Source layout note (why old patch line numbers don't match)

This fork is a **recent llama.cpp** that moved each architecture into its own file under
`src/models/`. All QWEN35MOE model code — hparam load, tensor load, and graph builders — lives in
**`src/models/qwen35moe.cpp`** (the `new llama_model_qwen35moe(params)` dispatch in
`src/llama-model.cpp`). Earlier notes that referenced `src/llama-model.cpp:~2822/~7563` were
against the old monolithic layout and no longer apply. Verify each site by content (grep), not by
line number.

---

## Build

```bash
git clone --depth=1 --branch feature/turboquant-kv-cache \
  https://github.com/TheTom/llama-cpp-turboquant "$HOME/tqp-src"

mkdir -p "$HOME/tqp-src/build" && cd "$HOME/tqp-src/build"
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_METAL=ON \
  -DGGML_BLAS=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON \
  -DLLAMA_TURBOQUANT=ON \
  -DCMAKE_OSX_ARCHITECTURES=arm64
cmake --build . --target llama-server -j$(sysctl -n hw.logicalcpu)
```

After applying the patches below, rebuild with just the last line (incremental — only
`qwen35moe.cpp` and the loader recompile).

---

## Fork patches — all in `src/models/qwen35moe.cpp` unless noted

The model fails to load through five sequential errors. Apply all five, then rebuild once.

### Patch 1 — ROPE dimension sections array length

**Error:** `key qwen35moe.rope.dimension_sections has wrong array length; expected 4, got 3`

The Qwen3.6 GGUF stores `rope.dimension_sections` as a **3-element** array (imrope), but
`load_arch_hparams` requested 4.

```cpp
// src/models/qwen35moe.cpp — load_arch_hparams()
// Before:
ml.get_key_or_arr(LLM_KV_ROPE_DIMENSION_SECTIONS, hparams.rope_sections, 4, true);
// After:
ml.get_key_or_arr(LLM_KV_ROPE_DIMENSION_SECTIONS, hparams.rope_sections, 3, true);
```

(`hparams.rope_sections` is a fixed 4-slot array zero-initialised elsewhere; loading 3 leaves the
4th as 0, which the imrope copy at the graph sites expects.)

### Patch 2 — ssm_dt tensor name (no `.bias` suffix)

**Error:** `missing tensor 'blk.0.ssm_dt.bias'`

For the recurrent (gated-delta-net) layers the GGUF names the tensor `blk.{i}.ssm_dt` with no
`.bias`. The loader appended `.bias`.

```cpp
// src/models/qwen35moe.cpp — load_arch_tensors(), recurrent branch
// Before:
layer.ssm_dt = create_tensor(tn(LLM_TENSOR_SSM_DT, "bias", il), { hparams.ssm_dt_rank }, flags);
// After:
layer.ssm_dt = create_tensor(tn(LLM_TENSOR_SSM_DT, il), { hparams.ssm_dt_rank }, flags);
```

### Patch 3 — per-layer KV head count in the tensor loader

**Error:** `attn_k.weight shape mismatch: expected [2048, 0], got [2048, 512]`

Qwen3.6 is hybrid: recurrent layers have `n_head_kv = 0`, full-attention layers have `n_head_kv = 2`,
stored as a per-layer array. The `LLAMA_LOAD_LOCALS` globals `n_embd_k_gqa`/`n_embd_v_gqa` resolve to
layer 0 (recurrent → 0), so full-attention tensors were allocated with size 0. Use the **per-layer**
accessors. There are two call sites — the trunk full-attention block and the MTP block:

```cpp
// src/models/qwen35moe.cpp — both create_tensor_qkv(...) calls
// Before:
create_tensor_qkv(layer, il, n_embd, n_embd_head_k * n_head * 2, n_embd_k_gqa, n_embd_v_gqa, flags);
// After:
create_tensor_qkv(layer, il, n_embd, n_embd_head_k * n_head * 2,
                  hparams.n_embd_k_gqa(il), hparams.n_embd_v_gqa(il), flags);
```

(The MTP-block call uses `0` instead of `flags` as the last arg — apply the same `(il)` change there.)

### Patch 4 — partial load for the vision-encoder tensors

**Error:** `done_getting_tensors: wrong number of tensors; expected 1194, got 733`

The Ollama Qwen3.6 GGUF is a **combined text+vision** model: ~733 text tensors (`blk.*`) + ~461
vision tensors (`v.blk.*`). The text loader only registers the text tensors.

This fork's loader **already supports** a partial mode —
`llama_model_loader::done_getting_tensors(bool partial)` only throws on `n_created < n_tensors`
when `partial == false`. The fix is to pass `partial = true` for this architecture at the call site:

```cpp
// src/llama-model.cpp — after the model's tensor-load loop (~line 1409)
// Before:
ml.done_getting_tensors();
// After:
ml.done_getting_tensors(arch == LLM_ARCH_QWEN35MOE);
```

(Scoped to QWEN35MOE so strict tensor-count checking is preserved for every other architecture.)

### Patch 5 — per-layer KV head count in the graph builders

**Error:** `GGML_ASSERT(ggml_nelements(a) == ne0*ne1*ne2) failed` in `ggml_reshape_3d`,
from the attention graph builder.

The graph-context global `n_head_kv` is `hparams.n_head_kv(0)` = 0 (layer 0 is recurrent). The K/V
reshapes in the full-attention builders used that stale global. There are **two** builders
(`build_layer_attn` for the trunk, and the MTP builder), each with a K reshape and a V reshape —
four sites total:

```cpp
// src/models/qwen35moe.cpp — all four ggml_reshape_3d K/V sites
// Before:
Kcur = ggml_reshape_3d(ctx0, Kcur, n_embd_head, n_head_kv, n_tokens);
Vcur = ggml_reshape_3d(ctx0, Vcur, n_embd_head, n_head_kv, n_tokens);
// After:
Kcur = ggml_reshape_3d(ctx0, Kcur, n_embd_head, hparams.n_head_kv(il), n_tokens);
Vcur = ggml_reshape_3d(ctx0, Vcur, n_embd_head, hparams.n_head_kv(il), n_tokens);
```

(`hparams` is a member of the graph context, so `hparams.n_head_kv(il)` is in scope.)

---

## Starting the server

Must be started with `-fit off` (the device-memory fitting probe internally builds the graph and
would hit the per-layer KV path before the real graph is needed). `scripts/start-tqp.sh` encodes
all flags. To serve Qwen3.6, point it at the Ollama GGUF blob:

```bash
GGUF="$HOME/.ollama/models/blobs/sha256-f5ee307a2982106a6eb82b62b2c00b575c9072145a759ae4660378acda8dcf2d" \
  bash scripts/start-tqp.sh
```

Find the blob digest for any Ollama model from its tag manifest:
`~/.ollama/models/manifests/registry.ollama.ai/library/<model>/<tag>` → the `model`-mediaType layer's
`digest` → `~/.ollama/models/blobs/sha256-<digest>`.

Key flags (in the script): `-c 65536`, `-np 1`, `--n-gpu-layers 99`, `--flash-attn on`,
`-ctk q8_0`, `--cache-type-v turbo3`, `--jinja`, `-fit off`.

Verify: `curl -s localhost:11435/v1/models` lists the served blob, and a non-empty completion comes
back (use a generous `max_tokens` — see the profile note; a tiny cap traps the thinking phase).

---

## agentd config — the transport profile (MANDATORY)

The transport profile is selected by **`TURBOQUANT_MODEL_FAMILY`** (read by
`TurboQuantTransport.from_env()` in `agentd/providers/turboquant_transport.py`), **not** by
`AI_EDITOR_TURBOQUANT_MODEL` (which is only the request label and is ignored by a single-model
llama-server).

`.env`:
```bash
AI_EDITOR_TURBOQUANT_MODEL="qwen3.6:35b-a3b-q4_K_M"   # label only (log clarity)
TURBOQUANT_MODEL_FAMILY=qwen3                          # selects the PROFILE — required
TURBOQUANT_THINKING_BUDGET=8192                        # >0 enables the <think> block
```

If `TURBOQUANT_MODEL_FAMILY` is unset it defaults to **`devstral`**, which uses devstral sampling
(temp 0.3, top_k 40) instead of Qwen's (temp 0.6, top_k 20) and has no thinking hook → Qwen3.6 thinks
unbounded and `content` comes back empty. The `qwen3` profile (`Qwen3Profile`) is required.

**Thinking control** — `TURBOQUANT_THINKING_BUDGET` (env override for the profile's `thinking_budget`):
- `0` → `chat_template_kwargs.enable_thinking=False`; the model emits the JSON answer directly, no
  `<think>` block. Fastest; lowest reasoning quality.
- `>0` → `enable_thinking=True, preserve_thinking=True, thinking_budget_tokens=N`; the model reasons
  in a `<think>` block then emits JSON. Reasoning streams via `reasoning_content`, separate from the
  JSON in `content`.

  ⚠ **This llama-server build does NOT honor `thinking_budget_tokens` as a cap.** Observed: with
  N=8192 the model decoded 12k+ reasoning tokens and kept going toward the `TURBOQUANT_MAX_TOKENS`
  ceiling (32768) — i.e. it only self-terminates, the budget is ignored. So `thinking_budget` is
  effectively an **on/off switch**, not a cap. On a bandwidth-bound M3 Pro (~20 t/s at long context)
  a single think can take 10-20 min, stalling the tool loop. **Recommended: keep thinking OFF
  (`=0`) for the agent loop** until the build enforces the budget (or speculative decoding is added).
  Enable only for one-shot, latency-tolerant calls.

This works **only** with `response_format: {type: "json_object"}` (the transport's setting). A strict
`json_schema` grammar blocks the model from emitting `</think>` and traps it in thinking — see the
transport section below.

Switch back to devstral by setting `MODEL_FAMILY=devstral` (the thinking budget is then ignored).

---

## Transport — structured output (already implemented)

These were resolved in `turboquant_transport.py` and are noted for completeness:

- **`json_object`, not `json_schema`.** A strict `json_schema` grammar blocks the model from emitting
  `</think>`, trapping it in thinking. The transport uses `response_format: {type: "json_object"}`
  and enforces the schema via the system prompt. (`use_json_object=True`.)
- **No tight `max_tokens`.** `-1` is silently ignored by llama-server (falls back to the CLI `-n`
  default). The transport sends a large cap (`_DEFAULT_MAX_TOKENS=32768`, overridable via
  `TURBOQUANT_MAX_TOKENS`) so the answer is never truncated. With the qwen3 profile disabling
  thinking, headroom is ample regardless.
- **`reasoning_content` separated from `content`.** The streaming handler routes
  `delta.reasoning_content` to the thinking channel and `delta.content` to the answer, returning
  `(thinking, content)`.

---

## Route note — `content` vs `message`

The chat message route reads `request.get("content") or request.get("message", "")`. Clients send
`{"content": "..."}`; reading only `message` silently stored empty user turns.

---

## Benchmark

Hardware: Apple M3 Pro, 36 GB unified memory. Model: Qwen3.6 35B-A3B Q4_K_M (same GGUF, same binary).
5 unique prompts after a discarded warm-up.

| Backend | Avg decode | KV cache format |
|---|---|---|
| TurboQuant (turbo3 + q8_0) | **~34 t/s** | V: turbo3 (~3-bit), K: q8_0 |
| Ollama (default) | ~22 t/s | V: f16, K: f16 |
| **Speedup** | **1.56×** | — |

The gain is reduced KV-cache memory bandwidth during decode (the V-cache is read every token for
every attention layer; ~3× compression → proportionally less data moved).

Note: token *generation* is memory-bandwidth-bound on the M3 Pro (~150 GB/s); the GPU compute is
underutilised during decode regardless of backend. Speculative decoding (a small draft model) is the
lever to reclaim that idle compute — not currently configured.
