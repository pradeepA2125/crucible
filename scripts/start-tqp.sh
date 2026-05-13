#!/usr/bin/env bash
# Start the TurboQuant llama-server (TheTom/llama-cpp-turboquant, feature/turboquant-kv-cache).
# Model: Qwen3.6 35B-A3B Q4_K_M from Ollama blob store.
# Context: 65536 tokens, single slot, turbo3 V-cache, q8_0 K-cache.
set -euo pipefail

LLAMA_SERVER="${LLAMA_SERVER:-/tmp/tqp-src/build/bin/llama-server}"
GGUF="${GGUF:-$HOME/.ollama/models/blobs/sha256-f5ee307a2982106a6eb82b62b2c00b575c9072145a759ae4660378acda8dcf2d}"
PORT="${TURBOQUANT_PORT:-11435}"
CTX="${TURBOQUANT_CTX:-65536}"
LOG="${TURBOQUANT_LOG:-/tmp/tqp-server.log}"

if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "llama-server not found at $LLAMA_SERVER" >&2
  echo "Build it: cd /tmp/tqp-src/build && cmake --build . --target llama-server -j$(sysctl -n hw.logicalcpu)" >&2
  exit 1
fi

if [[ ! -f "$GGUF" ]]; then
  echo "GGUF not found at $GGUF" >&2
  exit 1
fi

pkill -f "llama-server.*$PORT" 2>/dev/null || true
sleep 1

echo "==> starting TurboQuant server on port $PORT (ctx=$CTX)"
"$LLAMA_SERVER" \
  -m "$GGUF" \
  --port "$PORT" \
  --host 127.0.0.1 \
  -c "$CTX" \
  -np 1 \
  --n-gpu-layers 99 \
  --flash-attn on \
  -ctk q8_0 \
  --cache-type-v turbo3 \
  --jinja \
  -fit off \
  > "$LOG" 2>&1 &

echo "PID=$!"
echo "==> waiting for server..."
until grep -q "server is listening\|error loading" "$LOG" 2>/dev/null; do sleep 2; done

if grep -q "error loading" "$LOG"; then
  echo "==> server failed to start — check $LOG" >&2
  exit 1
fi

echo "==> TurboQuant server ready on http://127.0.0.1:$PORT"
echo "    log: $LOG"
