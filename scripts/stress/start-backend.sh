#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/stress/start-backend.sh [--workspace PATH] [--port N] [--out-dir PATH] [--log-dir PATH]
                                  [--backend NAME] [--model MODEL] [--validation-profile smoke|full|strict|none]
                                  [--artifacts-root PATH]

Defaults:
  workspace:    repository root
  port:         8000
  out-dir:      <repo>/.tmp/stress-<timestamp>  (uvicorn stdout log only)
  backend:      auto-detected from available provider keys
  model:        provider-specific default
  artifacts:    <workspace>/.crucible/state/artifacts
  db:           <workspace>/.crucible/state/agentd.sqlite3
  chat_db:      <workspace>/.crucible/state/chat.sqlite3
  log_file:     <workspace>/.crucible/state/agentd.log
  shadow_root:  <workspace>/.crucible/state/shadows
USAGE
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Shared indexer-build helpers (indexer_bin_path, ensure_indexer_binary).
# shellcheck source=scripts/stress/_indexer.sh
source "$ROOT/scripts/stress/_indexer.sh"

# Ensure Homebrew bin dirs are on PATH for subprocesses (ripgrep, etc.).
# When start-backend.sh is launched from an IDE shell whose PATH was assembled
# without `brew shellenv`, asyncio.create_subprocess_exec("rg", ...) errors with
# "ripgrep not found at 'rg'" even though `rg` exists on the user's interactive
# PATH. Prepend the canonical Homebrew bin dirs unconditionally — harmless when
# absent, fixes the lookup when present.
for _brew_dir in /opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin; do
  if [[ -d "$_brew_dir" && ":$PATH:" != *":$_brew_dir:"* ]]; then
    PATH="$_brew_dir:$PATH"
  fi
done
export PATH

# Auto-source .env — check this dir first, then the main worktree root (git common dir).
if [[ -z "${_CRUCIBLE_ENV_LOADED:-}" ]]; then
  _env_file=""
  if [[ -f "$ROOT/.env" ]]; then
    _env_file="$ROOT/.env"
  else
    # In a git worktree the common git dir lives in the main repo; go up from there.
    _git_common="$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$_git_common" ]]; then
      # --git-common-dir may be absolute or relative; resolve to main repo root.
      if [[ "$_git_common" = /* ]]; then
        _main_root="$(dirname "$_git_common")"
      else
        _main_root="$(cd "$ROOT/$_git_common/.." 2>/dev/null && pwd || true)"
      fi
      [[ -f "$_main_root/.env" ]] && _env_file="$_main_root/.env"
    fi
  fi
  if [[ -n "$_env_file" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$_env_file"
    set +a
    export _CRUCIBLE_ENV_LOADED=1
    echo "==> sourced env from $_env_file"
  fi
fi

WORKSPACE="$ROOT"
PORT="8000"
OUT_DIR="$ROOT/.tmp/stress-$(date +%Y%m%d-%H%M%S)"
LOG_DIR=""
BACKEND=""
MODEL=""
VALIDATION_PROFILE="full"
ARTIFACTS_ROOT=""
SCOPE_POLICY="ask"
SCOPE_TRIGGER="any"
SCOPE_REMEMBER="task"
SCOPE_TIMEOUT_SEC=""
TURBOQUANT_TIMEOUT_SEC="1200"
SKIP_INDEXER_BUILD="0"

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
    --scope-policy)
      SCOPE_POLICY="${2:?missing value for --scope-policy}"
      shift 2
      ;;
    --scope-trigger)
      SCOPE_TRIGGER="${2:?missing value for --scope-trigger}"
      shift 2
      ;;
    --scope-remember)
      SCOPE_REMEMBER="${2:?missing value for --scope-remember}"
      shift 2
      ;;
    --scope-timeout-sec)
      SCOPE_TIMEOUT_SEC="${2:?missing value for --scope-timeout-sec}"
      shift 2
      ;;
    --turboquant-timeout-sec)
      TURBOQUANT_TIMEOUT_SEC="${2:?missing value for --turboquant-timeout-sec}"
      shift 2
      ;;
    --skip-indexer-build)
      SKIP_INDEXER_BUILD="1"
      shift
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
    gemini) printf '%s' "${CRUCIBLE_GEMINI_MODEL:-gemini-3-flash-preview}" ;;
    groq) printf '%s' "${CRUCIBLE_GROQ_MODEL:-openai/gpt-oss-120b}" ;;
    openrouter) printf '%s' "${CRUCIBLE_OPENROUTER_MODEL:-stepfun/step-3.5-flash:free}" ;;
    watsonx) printf '%s' "${CRUCIBLE_WATSONX_MODEL:-ibm/granite-3-8b-instruct}" ;;
    openai) printf '%s' "${CRUCIBLE_OPENAI_MODEL:-gpt-5}" ;;
    anthropic) printf '%s' "${CRUCIBLE_ANTHROPIC_MODEL:-claude-3-5-sonnet-latest}" ;;
    huggingface) printf '%s' "${CRUCIBLE_HUGGINGFACE_MODEL:-deepseek-ai/DeepSeek-R1:fastest}" ;;
    ollama) printf '%s' "${CRUCIBLE_OLLAMA_MODEL:-glm-4.7-flash:latest}" ;;
    turboquant) printf '%s' "${CRUCIBLE_TURBOQUANT_MODEL:-qwen3.6:35b-a3b-q4_K_M}" ;;
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
      if [[ -n "${CRUCIBLE_VALIDATION_COMMANDS_JSON:-}" ]]; then
        printf '%s' "$CRUCIBLE_VALIDATION_COMMANDS_JSON"
      else
        # Let CommandValidator auto-detect project commands instead of bypassing
        # validation with a no-op command.
        printf '__AUTO_DETECT__'
      fi
      ;;
    strict)
      if [[ -n "${CRUCIBLE_VALIDATION_COMMANDS_JSON:-}" ]]; then
        printf '%s' "$CRUCIBLE_VALIDATION_COMMANDS_JSON"
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
  ARTIFACTS_ROOT="$WORKSPACE/.crucible/state/artifacts"
fi

mkdir -p "$OUT_DIR" "$LOG_DIR" "$WORKSPACE/.crucible/state" "$ARTIFACTS_ROOT"
# NOTE: do NOT place --workspace under a dir named like an indexer IGNORED_DIR
# (.tmp, target, dist, .git, node_modules, .venv, …). is_ignored_path in
# indexer-rs/src/service.rs matches those names anywhere in the ABSOLUTE path, so
# an ignored ANCESTOR silently filters every file: the watcher runs but the graph
# snapshot stays at 0 nodes (vector retrieval still works). Use workspaces/… etc.
SNAPSHOT_PATH="$WORKSPACE/.crucible/index-snapshot.json"
LOG_FILE="$LOG_DIR/agentd.log"             # uvicorn stdout (tee'd)
BACKEND_LOG_FILE="$WORKSPACE/.crucible/state/agentd.log"   # structured backend log
CHAT_DB_PATH="$WORKSPACE/.crucible/state/chat.sqlite3"
DB_PATH="$WORKSPACE/.crucible/state/agentd.sqlite3"
SHADOW_ROOT="$WORKSPACE/.crucible/state/shadows"
VALIDATION_COMMANDS_JSON="$(resolve_validation_commands)"

if [[ "$VALIDATION_COMMANDS_JSON" == "__STRICT_MISSING__" ]]; then
  echo "strict validation profile requires CRUCIBLE_VALIDATION_COMMANDS_JSON to be set" >&2
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
  ollama)
    # Local — no API key. Verify the daemon is reachable so we fail fast.
    OLLAMA_URL="${OLLAMA_HOST:-http://localhost:11434}"
    if ! curl -sf "${OLLAMA_URL%/}/api/tags" >/dev/null 2>&1; then
      echo "Ollama daemon not reachable at $OLLAMA_URL — start it with 'ollama serve'." >&2
      exit 1
    fi
    ;;
  turboquant)
    # Local llama-server with TurboQuant KV-cache compression. Verify it's reachable.
    TQ_URL="${TURBOQUANT_HOST:-http://localhost:11435}"
    if ! curl -sf "${TQ_URL%/}/health" >/dev/null 2>&1; then
      echo "TurboQuant server not reachable at $TQ_URL — start it with start-tqp.sh first." >&2
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
echo "db_path=$DB_PATH"
echo "chat_db_path=$CHAT_DB_PATH"
echo "shadow_root=$SHADOW_ROOT"
echo "artifacts_root=$ARTIFACTS_ROOT"
echo "validation_profile=$VALIDATION_PROFILE"
if [[ "$VALIDATION_COMMANDS_JSON" == "__AUTO_DETECT__" ]]; then
  echo "validation_commands=auto-detect"
else
  echo "validation_commands=configured"
fi
echo "backend_log=$BACKEND_LOG_FILE"
echo "uvicorn_log=$LOG_FILE"

# Start uvicorn in the background so we can wait for it to be ready before
# pre-warming the semantic index (guaranteeing no cold-start on the first task).
(
  cd "$AGENTD_DIR"
  export CRUCIBLE_REASONING_BACKEND="$BACKEND"
  export CRUCIBLE_WORKSPACE_PATH="$WORKSPACE"
  export CRUCIBLE_DB_PATH="$DB_PATH"
  export CRUCIBLE_CHAT_DB_PATH="$CHAT_DB_PATH"
  export CRUCIBLE_SHADOW_ROOT="$SHADOW_ROOT"
  export CRUCIBLE_LOG_FILE="$BACKEND_LOG_FILE"
  export CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH="$SNAPSHOT_PATH"
  export CRUCIBLE_ARTIFACTS_ROOT="$ARTIFACTS_ROOT"
  export CRUCIBLE_SHELL_POLICY="${CRUCIBLE_SHELL_POLICY:-ask}"
  # Default-on feature flags (2026-07-02): reactive controller + its tool surface
  # (skills catalog, MCP servers from .crucible/mcp.json, gated write_doc).
  # Override any of these via env to opt out.
  export CRUCIBLE_CHAT_CONTROLLER="${CRUCIBLE_CHAT_CONTROLLER:-1}"
  export CRUCIBLE_SKILLS_ENABLED="${CRUCIBLE_SKILLS_ENABLED:-1}"
  export CRUCIBLE_MCP_ENABLED="${CRUCIBLE_MCP_ENABLED:-1}"
  export CRUCIBLE_DOC_WRITE_ENABLED="${CRUCIBLE_DOC_WRITE_ENABLED:-1}"
  export CRUCIBLE_SEMANTIC_RETRIEVAL="${CRUCIBLE_SEMANTIC_RETRIEVAL:-true}"
  # UX decision (chat UI redesign): the step gate is the conscious approval moment
  # on the large path — review every step by default. Override via env to opt out.
  export CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT="${CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT:-false}"
  if [[ "$VALIDATION_COMMANDS_JSON" == "__AUTO_DETECT__" ]]; then
    unset CRUCIBLE_VALIDATION_COMMANDS_JSON
  else
    export CRUCIBLE_VALIDATION_COMMANDS_JSON="$VALIDATION_COMMANDS_JSON"
  fi

  [[ -n "$SCOPE_POLICY" ]]      && export CRUCIBLE_SCOPE_POLICY="$SCOPE_POLICY"
  [[ -n "$SCOPE_TRIGGER" ]]     && export CRUCIBLE_SCOPE_TRIGGER="$SCOPE_TRIGGER"
  [[ -n "$SCOPE_REMEMBER" ]]    && export CRUCIBLE_SCOPE_REMEMBER="$SCOPE_REMEMBER"
  [[ -n "$SCOPE_TIMEOUT_SEC" ]] && export CRUCIBLE_SCOPE_TIMEOUT_SEC="$SCOPE_TIMEOUT_SEC"

  case "$BACKEND" in
    gemini)
      export CRUCIBLE_GEMINI_MODEL="$MODEL"
      ;;
    groq)
      export CRUCIBLE_GROQ_MODEL="$MODEL"
      ;;
    openrouter)
      export CRUCIBLE_OPENROUTER_MODEL="$MODEL"
      ;;
    watsonx)
      export CRUCIBLE_WATSONX_MODEL="$MODEL"
      export WATSONX_URL="${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}"
      ;;
    openai)
      export CRUCIBLE_OPENAI_MODEL="$MODEL"
      ;;
    anthropic)
      export CRUCIBLE_ANTHROPIC_MODEL="$MODEL"
      ;;
    huggingface)
      export CRUCIBLE_HUGGINGFACE_MODEL="$MODEL"
      ;;
    ollama)
      export CRUCIBLE_OLLAMA_MODEL="$MODEL"
      [[ -n "${OLLAMA_HOST:-}" ]] && export OLLAMA_HOST="$OLLAMA_HOST"
      ;;
    turboquant)
      export CRUCIBLE_TURBOQUANT_MODEL="$MODEL"
      export TURBOQUANT_HOST="${TURBOQUANT_HOST:-http://localhost:11435}"
      export CRUCIBLE_TURBOQUANT_TIMEOUT_SEC="$TURBOQUANT_TIMEOUT_SEC"
      ;;
    scripted)
      ;;
  esac

  # Run uvicorn directly from the venv WITHOUT activating it. Activation
  # prepends .venv/bin to PATH and that PATH is inherited by every child
  # subprocess (incl. agent's run_command), causing the agent to silently use
  # the backend's pytest/ruff/mypy instead of the workspace's. Bypass that.
  ./.venv/bin/uvicorn agentd.main:app --port "$PORT" --reload 2>&1 | tee "$LOG_FILE"
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
if [[ "${CRUCIBLE_SEMANTIC_RETRIEVAL:-}" =~ ^(1|true|yes|on)$ ]]; then
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

# Self-updating index: launch the incremental indexer watcher. It re-indexes changed source
# files → rewrites the snapshot (atomic) → notifies the backend via CRUCIBLE_BACKEND_URL, which
# delta-re-embeds.
#
# LSP ON here: the watcher is where Calls/Implements/Inherits edges get resolved to workspace
# symbols (pyright/tsserver/rust-analyzer answer textDocument/definition + implementation). Those
# resolved edges are what make graph_neighbor_files and the query_graph tool useful — without them
# the symbol graph is keyword/tree-sitter only and the planner can't navigate cross-file structure.
# The watcher is a single long-lived process (one LSP warmup per backend launch, then incremental
# per-file resolution), so the memory/warmup cost is paid once, not per task. A language server
# that fails to start is disabled gracefully and the other languages still resolve. Override the
# LSP commands/timeouts below via env if your toolchain differs.
# Resolve the watcher binary from the SHARED cargo target dir (see _indexer.sh) and
# build it on demand. Previously this pointed at <this-repo>/services/indexer-rs/target,
# which is empty in a fresh git worktree — so the watcher (and all LSP-resolved graph
# edges) silently never started. Now a missing/stale binary is built once into a shared
# cache (cargo no-ops when warm); pass --skip-indexer-build to bypass.
_INDEXER_BIN="$(indexer_bin_path)"
_WATCHER_PID=""
if [[ "${CRUCIBLE_SEMANTIC_RETRIEVAL:-}" =~ ^(1|true|yes|on)$ ]]; then
  if [[ "$SKIP_INDEXER_BUILD" == "1" ]]; then
    echo "==> indexer build skipped (--skip-indexer-build); using $_INDEXER_BIN if present"
  elif ! ensure_indexer_binary "$ROOT/services/indexer-rs" >/dev/null; then
    echo "==> indexer: could not ensure binary — watcher will be skipped (graph retrieval off; vector retrieval still works)" >&2
  fi
fi
if [[ "${CRUCIBLE_SEMANTIC_RETRIEVAL:-}" =~ ^(1|true|yes|on)$ && -x "$_INDEXER_BIN" ]]; then
  # Single-writer guard: reap any existing indexer watcher on THIS snapshot before launching ours.
  # Two watchers racing the same snapshot file clobber each other — the loser overwrites the
  # winner's LSP-resolved Calls/Inherits edges back to unresolved `external:` placeholders, which
  # silently degrades graph retrieval and the query_graph tool. (Found 2026-06-14: a stale watcher
  # from a prior launch kept overwriting the fresh LSP-resolved snapshot; incremental per-file
  # re-resolution never rebuilds the full graph, so it stayed degraded.)
  # NOTE: the `|| true` is load-bearing under `set -euo pipefail`. With no stale watcher
  # (the normal clean-env case) `grep` matches nothing → exits 1 → pipefail fails the
  # pipeline → set -e aborts the script BEFORE the watcher launches, leaving the backend
  # orphaned and graph retrieval off. Tolerate the empty match explicitly.
  _STALE_WATCHERS="$(pgrep -af 'crucible-indexer index' 2>/dev/null \
    | grep -F -- "--snapshot-path $SNAPSHOT_PATH" | awk '{print $1}' || true)"
  if [[ -n "$_STALE_WATCHERS" ]]; then
    echo "==> reaping stale indexer watcher(s) on this snapshot: $(echo "$_STALE_WATCHERS" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $_STALE_WATCHERS 2>/dev/null || true
    sleep 1
  fi
  CRUCIBLE_BACKEND_URL="http://localhost:${PORT}" \
    CRUCIBLE_LSP_ENABLED="${CRUCIBLE_LSP_ENABLED:-true}" \
    CRUCIBLE_LSP_PY_CMD="${CRUCIBLE_LSP_PY_CMD:-pyright-langserver --stdio}" \
    CRUCIBLE_LSP_TS_CMD="${CRUCIBLE_LSP_TS_CMD:-typescript-language-server --stdio}" \
    CRUCIBLE_LSP_RS_CMD="${CRUCIBLE_LSP_RS_CMD:-rust-analyzer}" \
    CRUCIBLE_LSP_STARTUP_TIMEOUT_MS="${CRUCIBLE_LSP_STARTUP_TIMEOUT_MS:-180000}" \
    CRUCIBLE_LSP_REQUEST_TIMEOUT_MS="${CRUCIBLE_LSP_REQUEST_TIMEOUT_MS:-20000}" \
    RUST_LOG="${RUST_LOG:-crucible_indexer::resolver=info,crucible_indexer::lsp=info,crucible_indexer::service=info}" \
    "$_INDEXER_BIN" index --workspace "$WORKSPACE" --snapshot-path "$SNAPSHOT_PATH" --watch true \
    >> "$LOG_DIR/indexer-watch.log" 2>&1 &
  _WATCHER_PID=$!
  echo "==> indexer watch started (self-updating index, LSP-resolved edges): pid=$_WATCHER_PID log=$LOG_DIR/indexer-watch.log"
elif [[ "${CRUCIBLE_SEMANTIC_RETRIEVAL:-}" =~ ^(1|true|yes|on)$ ]]; then
  echo "==> indexer watch NOT started — binary unavailable at $_INDEXER_BIN (build failed, or --skip-indexer-build with no prebuilt binary). Graph retrieval is disabled; vector retrieval still works." >&2
fi

# Don't orphan child processes (backend + watcher) on exit/interrupt.
trap '[[ -n "$_WATCHER_PID" ]] && kill "$_WATCHER_PID" 2>/dev/null; kill "$_SERVER_PID" 2>/dev/null' EXIT INT TERM

echo "==> backend ready — submitting tasks is now safe"
wait "$_SERVER_PID"
