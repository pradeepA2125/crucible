#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/stress/start-backend.sh [--workspace PATH] [--port N] [--out-dir PATH] [--log-dir PATH]
                                  [--backend NAME] [--model MODEL] [--validation-profile smoke|full|strict|none]
                                  [--artifacts-root PATH]

Defaults:
  workspace: repository root
  port:      8000
  out-dir:   <repo>/.tmp/stress-<timestamp>
  backend:   auto-detected from available provider keys
  model:     provider-specific default
  artifacts: <workspace>/.agentd/artifacts
USAGE
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKSPACE="$ROOT"
PORT="8000"
OUT_DIR="$ROOT/.tmp/stress-$(date +%Y%m%d-%H%M%S)"
LOG_DIR=""
BACKEND=""
MODEL=""
VALIDATION_PROFILE="full"
ARTIFACTS_ROOT=""

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
    --out-dir)
      OUT_DIR="${2:?missing value for --out-dir}"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="${2:?missing value for --log-dir}"
      shift 2
      ;;
    --backend)
      BACKEND="${2:?missing value for --backend}"
      shift 2
      ;;
    --model)
      MODEL="${2:?missing value for --model}"
      shift 2
      ;;
    --validation-profile)
      VALIDATION_PROFILE="${2:?missing value for --validation-profile}"
      shift 2
      ;;
    --artifacts-root)
      ARTIFACTS_ROOT="${2:?missing value for --artifacts-root}"
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

if [[ ! -d "$WORKSPACE" ]]; then
  echo "Workspace directory does not exist: $WORKSPACE" >&2
  exit 1
fi

AGENTD_DIR="$ROOT/services/agentd-py"
if [[ ! -x "$AGENTD_DIR/.venv/bin/python" ]]; then
  echo "Missing virtualenv python: $AGENTD_DIR/.venv/bin/python" >&2
  echo "Run bootstrap in the main repo first." >&2
  exit 1
fi

resolve_backend() {
  if [[ -n "$BACKEND" ]]; then
    printf '%s' "$BACKEND"
    return
  fi
  if [[ -n "${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}" ]]; then
    printf 'gemini'
  elif [[ -n "${GROQ_API_KEY:-}" ]]; then
    printf 'groq'
  elif [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    printf 'openrouter'
  elif [[ -n "${WATSONX_API_KEY:-}" ]]; then
    printf 'watsonx'
  elif [[ -n "${OPENAI_API_KEY:-}" ]]; then
    printf 'openai'
  elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    printf 'anthropic'
  elif [[ -n "${HF_TOKEN:-}" ]]; then
    printf 'huggingface'
  else
    printf 'scripted'
  fi
}

resolve_default_model() {
  case "$1" in
    scripted) printf 'scripted' ;;
    gemini) printf '%s' "${AI_EDITOR_GEMINI_MODEL:-gemini-3-flash-preview}" ;;
    groq) printf '%s' "${AI_EDITOR_GROQ_MODEL:-openai/gpt-oss-120b}" ;;
    openrouter) printf '%s' "${AI_EDITOR_OPENROUTER_MODEL:-stepfun/step-3.5-flash:free}" ;;
    watsonx) printf '%s' "${AI_EDITOR_WATSONX_MODEL:-ibm/granite-3-8b-instruct}" ;;
    openai) printf '%s' "${AI_EDITOR_OPENAI_MODEL:-gpt-5}" ;;
    anthropic) printf '%s' "${AI_EDITOR_ANTHROPIC_MODEL:-claude-3-5-sonnet-latest}" ;;
    huggingface) printf '%s' "${AI_EDITOR_HUGGINGFACE_MODEL:-deepseek-ai/DeepSeek-R1:fastest}" ;;
    *)
      echo "Unsupported backend: $1" >&2
      exit 1
      ;;
  esac
}

resolve_validation_commands() {
  case "$VALIDATION_PROFILE" in
    none)
      printf '[]'
      ;;
    smoke)
      printf '[{"stage":"syntax","name":"smoke-pass","command":"true"}]'
      ;;
    full)
      if [[ -n "${AI_EDITOR_VALIDATION_COMMANDS_JSON:-}" ]]; then
        printf '%s' "$AI_EDITOR_VALIDATION_COMMANDS_JSON"
      else
        # Let CommandValidator auto-detect project commands instead of bypassing
        # validation with a no-op command.
        printf '__AUTO_DETECT__'
      fi
      ;;
    strict)
      if [[ -n "${AI_EDITOR_VALIDATION_COMMANDS_JSON:-}" ]]; then
        printf '%s' "$AI_EDITOR_VALIDATION_COMMANDS_JSON"
      else
        printf '__STRICT_MISSING__'
      fi
      ;;
    *)
      echo "Unsupported validation profile: $VALIDATION_PROFILE" >&2
      exit 1
      ;;
  esac
}

BACKEND="$(resolve_backend)"
if [[ -z "$MODEL" ]]; then
  MODEL="$(resolve_default_model "$BACKEND")"
fi
if [[ -z "$LOG_DIR" ]]; then
  LOG_DIR="$OUT_DIR/logs"
fi
if [[ -z "$ARTIFACTS_ROOT" ]]; then
  ARTIFACTS_ROOT="$WORKSPACE/.agentd/artifacts"
fi

mkdir -p "$OUT_DIR" "$LOG_DIR" "$WORKSPACE/.agentd" "$ARTIFACTS_ROOT"
SNAPSHOT_PATH="$WORKSPACE/.ai-editor/index-snapshot.json"
LOG_FILE="$LOG_DIR/agentd.log"
VALIDATION_COMMANDS_JSON="$(resolve_validation_commands)"

if [[ "$VALIDATION_COMMANDS_JSON" == "__STRICT_MISSING__" ]]; then
  echo "strict validation profile requires AI_EDITOR_VALIDATION_COMMANDS_JSON to be set" >&2
  exit 1
fi

case "$BACKEND" in
  gemini)
    if [[ -z "${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}" ]]; then
      echo "GEMINI_API_KEY or GOOGLE_API_KEY is required for gemini backend" >&2
      exit 1
    fi
    ;;
  groq)
    if [[ -z "${GROQ_API_KEY:-}" ]]; then
      echo "GROQ_API_KEY is required for groq backend" >&2
      exit 1
    fi
    ;;
  openrouter)
    if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
      echo "OPENROUTER_API_KEY is required for openrouter backend" >&2
      exit 1
    fi
    ;;
  watsonx)
    if [[ -z "${WATSONX_API_KEY:-}" ]]; then
      echo "WATSONX_API_KEY is required for watsonx backend" >&2
      exit 1
    fi
    ;;
  openai)
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
      echo "OPENAI_API_KEY is required for openai backend" >&2
      exit 1
    fi
    ;;
  anthropic)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "ANTHROPIC_API_KEY is required for anthropic backend" >&2
      exit 1
    fi
    ;;
  huggingface)
    if [[ -z "${HF_TOKEN:-}" ]]; then
      echo "HF_TOKEN is required for huggingface backend" >&2
      exit 1
    fi
    ;;
  scripted)
    ;;
  *)
    echo "Unsupported backend: $BACKEND" >&2
    exit 1
    ;;
esac

echo "==> starting backend"
echo "workspace=$WORKSPACE"
echo "port=$PORT"
echo "backend=$BACKEND"
echo "model=$MODEL"
echo "snapshot=$SNAPSHOT_PATH"
echo "db_path=$WORKSPACE/.agentd/agentd.sqlite3"
echo "shadow_root=$WORKSPACE/.agentd/shadows"
echo "artifacts_root=$ARTIFACTS_ROOT"
echo "validation_profile=$VALIDATION_PROFILE"
if [[ "$VALIDATION_COMMANDS_JSON" == "__AUTO_DETECT__" ]]; then
  echo "validation_commands=auto-detect"
else
  echo "validation_commands=configured"
fi
echo "log_file=$LOG_FILE"

# Start uvicorn in the background so we can wait for it to be ready before
# pre-warming the semantic index (guaranteeing no cold-start on the first task).
(
  cd "$AGENTD_DIR"
  export AI_EDITOR_REASONING_BACKEND="$BACKEND"
  export AI_EDITOR_DB_PATH="$WORKSPACE/.agentd/agentd.sqlite3"
  export AI_EDITOR_SHADOW_ROOT="$WORKSPACE/.agentd/shadows"
  export AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH="$SNAPSHOT_PATH"
  export AI_EDITOR_ARTIFACTS_ROOT="$ARTIFACTS_ROOT"
  if [[ "$VALIDATION_COMMANDS_JSON" == "__AUTO_DETECT__" ]]; then
    unset AI_EDITOR_VALIDATION_COMMANDS_JSON
  else
    export AI_EDITOR_VALIDATION_COMMANDS_JSON="$VALIDATION_COMMANDS_JSON"
  fi

  case "$BACKEND" in
    gemini)
      export AI_EDITOR_GEMINI_MODEL="$MODEL"
      ;;
    groq)
      export AI_EDITOR_GROQ_MODEL="$MODEL"
      ;;
    openrouter)
      export AI_EDITOR_OPENROUTER_MODEL="$MODEL"
      ;;
    watsonx)
      export AI_EDITOR_WATSONX_MODEL="$MODEL"
      export WATSONX_URL="${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}"
      ;;
    openai)
      export AI_EDITOR_OPENAI_MODEL="$MODEL"
      ;;
    anthropic)
      export AI_EDITOR_ANTHROPIC_MODEL="$MODEL"
      ;;
    huggingface)
      export AI_EDITOR_HUGGINGFACE_MODEL="$MODEL"
      ;;
    scripted)
      ;;
  esac

  source .venv/bin/activate
  uvicorn agentd.main:app --port "$PORT" 2>&1 | tee "$LOG_FILE"
) &
_SERVER_PID=$!

# Wait for backend to become healthy.
_health_url="http://localhost:${PORT}/health"
echo "==> waiting for backend on port $PORT ..."
for _i in $(seq 1 60); do
  if curl -sf "$_health_url" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -sf "$_health_url" >/dev/null 2>&1; then
  echo "Backend did not become healthy within 60 s" >&2
  kill "$_SERVER_PID" 2>/dev/null || true
  exit 1
fi
echo "==> backend healthy"

# Pre-warm the semantic index synchronously — no task can be submitted until
# this completes, so the first task is guaranteed to have a warm index.
if [[ "${AI_EDITOR_SEMANTIC_RETRIEVAL:-}" =~ ^(1|true|yes|on)$ ]]; then
  _build_url="http://localhost:${PORT}/v1/index/build"
  _status_url="http://localhost:${PORT}/v1/index/status"
  echo "==> semantic index pre-warm: triggering build for $WORKSPACE ..."
  if ! curl -sf -X POST "$_build_url" \
      -H "Content-Type: application/json" \
      -d "{\"workspace_path\": \"$WORKSPACE\"}" >/dev/null; then
    echo "==> semantic index pre-warm: trigger failed (non-fatal, check backend log)" >&2
  else
    echo "==> semantic index pre-warm: waiting for completion ..."
    for _j in $(seq 1 120); do
      _building=$(curl -sf "$_status_url" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('building', True))" 2>/dev/null \
        || echo "True")
      if [[ "$_building" == "False" ]]; then
        echo "==> semantic index pre-warm: ready"
        break
      fi
      sleep 1
    done
  fi
fi

echo "==> backend ready — submitting tasks is now safe"
wait "$_SERVER_PID"
