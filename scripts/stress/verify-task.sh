#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/stress/verify-task.sh --task-id ID [--base-url URL] [--workspace PATH] [--log-file PATH] [--max-files N] [--out-dir PATH]
USAGE
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_URL="http://127.0.0.1:8000"
WORKSPACE="$ROOT"
TASK_ID=""
LOG_FILE=""
MAX_FILES="999"
OUT_DIR="${TMPDIR:-/tmp}/crucible-verify"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id)
      TASK_ID="${2:?missing value for --task-id}"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2:?missing value for --base-url}"
      shift 2
      ;;
    --workspace)
      WORKSPACE="${2:?missing value for --workspace}"
      shift 2
      ;;
    --log-file)
      LOG_FILE="${2:?missing value for --log-file}"
      shift 2
      ;;
    --max-files)
      MAX_FILES="${2:?missing value for --max-files}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:?missing value for --out-dir}"
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

if [[ -z "$TASK_ID" ]]; then
  echo "--task-id is required" >&2
  usage
  exit 1
fi

for cmd in curl jq rg; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

mkdir -p "$OUT_DIR"
TASK_JSON="$OUT_DIR/task-$TASK_ID.json"
RESULT_JSON="$OUT_DIR/result-$TASK_ID.json"
LOG_SCAN_FILE="$OUT_DIR/logscan-$TASK_ID.txt"

curl -sS "$BASE_URL/v1/tasks/$TASK_ID" >"$TASK_JSON"
curl -sS "$BASE_URL/v1/tasks/$TASK_ID/result" >"$RESULT_JSON"

STATUS="$(jq -r '.status' "$RESULT_JSON")"
MODIFIED_COUNT="$(jq -r '.modified_files | length' "$RESULT_JSON")"
PLAN_STEPS="$(jq -r '(.plan.steps | length) // 0' "$RESULT_JSON")"
PATCH_OPS="$(jq -r '(.patch.candidates[0].patch_ops | length) // (.patch.patch_ops | length) // 0' "$RESULT_JSON")"
DIAG_COUNT="$(jq -r '.diagnostics | length' "$RESULT_JSON")"

echo "task_id=$TASK_ID"
echo "status=$STATUS"
echo "modified_files=$MODIFIED_COUNT"
echo "plan_steps=$PLAN_STEPS"
echo "patch_ops=$PATCH_OPS"
echo "diagnostics=$DIAG_COUNT"

if [[ "$MODIFIED_COUNT" -gt "$MAX_FILES" ]]; then
  echo "FAIL: modified_files ($MODIFIED_COUNT) exceeds max_files ($MAX_FILES)" >&2
  exit 1
fi

if [[ "$STATUS" == "READY_FOR_REVIEW" || "$STATUS" == "SUCCEEDED" ]]; then
  if [[ "$PLAN_STEPS" -le 0 || "$PATCH_OPS" -le 0 ]]; then
    echo "FAIL: expected non-empty plan and patch for status=$STATUS" >&2
    exit 1
  fi
fi

if [[ -n "$LOG_FILE" && -f "$LOG_FILE" ]]; then
  if rg -n "invalid_type|Polling failed|client has been closed" "$LOG_FILE" >"$LOG_SCAN_FILE"; then
    echo "FAIL: detected transport/polling errors in log file $LOG_FILE" >&2
    cat "$LOG_SCAN_FILE" >&2
    exit 1
  fi
  echo "log_scan=ok"
fi

echo "==> workspace diff summary"
(
  cd "$WORKSPACE"
  git status --short
  git diff --name-only
)

echo "PASS: task verification checks completed"
