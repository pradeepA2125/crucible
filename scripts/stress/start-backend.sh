#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/stress/start-backend.sh [--workspace PATH] [--port N] [--log-dir PATH] [--model MODEL] [--max-tokens N]

Required env:
  GROQ_API_KEY, OPENROUTER_API_KEY, or WATSONX_API_KEY
EOF
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKSPACE="$ROOT/workspaces/shadow-forge-stress"
PORT="8000"
LOG_DIR="$ROOT/.tmp/stress-$(date +%Y%m%d-%H%M%S)"

# Default values based on available keys
if [[ -n "${WATSONX_API_KEY:-}" ]]; then
  BACKEND="watsonx"
  MODEL="${AI_EDITOR_WATSONX_MODEL:-deepseek-ai/deepseek-r1}"
  MAX_TOKENS=""
elif [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  BACKEND="openrouter"
  MODEL="stepfun/step-3.5-flash:free"
  MAX_TOKENS=""
elif [[ -n "${GROQ_API_KEY:-}" ]]; then
  BACKEND="groq"
  MODEL="openai/gpt-oss-120b"
  MAX_TOKENS="16384"
else
  echo "Error: Set GROQ_API_KEY, OPENROUTER_API_KEY, or WATSONX_API_KEY before starting backend." >&2
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      WORKSPACE="${2:?missing value for --workspace}"
      shift 2
      ;;
    --port)
      PORT="${2:?missing value for --port}"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="${2:?missing value for --log-dir}"
      shift 2
      ;;
    --model)
      MODEL="${2:?missing value for --model}"
      shift 2
      ;;
    --max-tokens)
      MAX_TOKENS="${2:?missing value for --max-tokens}"
      shift 2
      ;;
    --backend)
      BACKEND="${2:?missing value for --backend}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

AGENTD_DIR="$ROOT/services/agentd-py"
if [[ ! -x "$AGENTD_DIR/.venv/bin/python" ]]; then
  echo "Missing virtualenv python: $AGENTD_DIR/.venv/bin/python" >&2
  echo "Run bootstrap in the main repo first." >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$WORKSPACE/.agentd"
SNAPSHOT_PATH="$WORKSPACE/.ai-editor/index-snapshot.json"
LOG_FILE="$LOG_DIR/agentd.log"

echo "==> starting backend"
echo "workspace=$WORKSPACE"
echo "port=$PORT"
echo "backend=$BACKEND"
echo "model=$MODEL"
if [[ -n "$MAX_TOKENS" ]]; then
  echo "max_tokens=$MAX_TOKENS"
fi
echo "snapshot=$SNAPSHOT_PATH"
echo "db_path=$WORKSPACE/.agentd/agentd.sqlite3"
echo "shadow_root=$WORKSPACE/.agentd/shadows"
echo "log_file=$LOG_FILE"

(
  cd "$AGENTD_DIR"
  export AI_EDITOR_REASONING_BACKEND="$BACKEND"
  if [[ "$BACKEND" == "groq" ]]; then
    export AI_EDITOR_GROQ_MODEL="$MODEL"
    export AI_EDITOR_GROQ_MAX_TOKENS="${MAX_TOKENS:-4096}"
  elif [[ "$BACKEND" == "openrouter" ]]; then
    export AI_EDITOR_OPENROUTER_MODEL="$MODEL"
  elif [[ "$BACKEND" == "watsonx" ]]; then
    export AI_EDITOR_WATSONX_MODEL="$MODEL"
    # These are picked up by WatsonxJsonTransport directly from env if not passed to ctor
    export WATSONX_API_KEY="${WATSONX_API_KEY}"
    export WATSONX_PROJECT_ID="${WATSONX_PROJECT_ID}"
    export WATSONX_URL="${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}"
  fi
  export AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH="$SNAPSHOT_PATH"
  export AI_EDITOR_DB_PATH="$WORKSPACE/.agentd/agentd.sqlite3"
  export AI_EDITOR_SHADOW_ROOT="$WORKSPACE/.agentd/shadows"
  export AI_EDITOR_VALIDATION_COMMANDS_JSON='[
    {"stage":"syntax","name":"py-compile","command":"cd services/agentd-py && python -m compileall -q agentd tests","timeout_sec":120},
    {"stage":"test","name":"py-tests","command":"cd services/agentd-py && python -m pytest -q","timeout_sec":300},
    {"stage":"type","name":"ts-editor-client-typecheck","command":"npm run -w @ai-editor/editor-client typecheck","timeout_sec":240},
    {"stage":"type","name":"ts-extension-typecheck","command":"npm run -w @ai-editor/vscode-extension typecheck","timeout_sec":240}
  ]'
  source .venv/bin/activate
  uvicorn agentd.main:app --port "$PORT" 2>&1 | tee "$LOG_FILE"
)
