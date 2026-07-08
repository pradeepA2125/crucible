#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/stress/run-constrained-task.sh --goal "text" [--workspace PATH] [--base-url URL] [--out-dir PATH] [--accept]

Defaults:
  workspace: repository root
  base-url:  http://127.0.0.1:8000
  out-dir:   ${TMPDIR:-/tmp}/crucible-runs
USAGE
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKSPACE="$ROOT"
BASE_URL="http://127.0.0.1:8000"
OUT_DIR="${TMPDIR:-/tmp}/crucible-runs"
GOAL=""
AUTO_ACCEPT="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --goal)
      GOAL="${2:?missing value for --goal}"
      shift 2
      ;;
    --workspace)
      WORKSPACE="${2:?missing value for --workspace}"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2:?missing value for --base-url}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:?missing value for --out-dir}"
      shift 2
      ;;
    --accept)
      AUTO_ACCEPT="1"
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

if [[ -z "$GOAL" ]]; then
  echo "--goal is required" >&2
  usage
  exit 1
fi

for cmd in curl python3 jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

mkdir -p "$OUT_DIR"
RUN_TAG="$(date +%Y%m%d-%H%M%S)"
CREATE_FILE="$OUT_DIR/create-$RUN_TAG.json"
TASK_FILE="$OUT_DIR/task-$RUN_TAG.json"
RESULT_FILE="$OUT_DIR/result-$RUN_TAG.json"
FINAL_FILE="$OUT_DIR/final-$RUN_TAG.json"

PAYLOAD="$(
python3 - "$GOAL" "$WORKSPACE" <<'PY'
import json
import sys

goal = sys.argv[1]
workspace = sys.argv[2]
payload = {
    "goal": goal,
    "workspace_path": workspace,
    "mode": "project_edit",
    "budget": {
        "max_iterations": 2,
        "max_files_touched": 6,
        "max_tokens": 50000,
        "max_runtime_ms": 600000,
    },
}
print(json.dumps(payload))
PY
)"

curl -sS -X POST "$BASE_URL/v1/tasks" \
  -H "content-type: application/json" \
  -d "$PAYLOAD" >"$CREATE_FILE"

TASK_ID="$(jq -r '.task_id // empty' "$CREATE_FILE")"
if [[ -z "$TASK_ID" ]]; then
  echo "Failed to create task. Response:" >&2
  cat "$CREATE_FILE" >&2
  exit 1
fi

echo "task_id=$TASK_ID"
echo "create_json=$CREATE_FILE"

STATUS=""
for attempt in $(seq 1 240); do
  curl -sS "$BASE_URL/v1/tasks/$TASK_ID" >"$TASK_FILE"
  STATUS="$(jq -r '.status // empty' "$TASK_FILE")"
  echo "poll[$attempt] $TASK_ID => $STATUS"
  case "$STATUS" in
    READY_FOR_REVIEW|SUCCEEDED|FAILED|ABORTED)
      break
      ;;
    AWAITING_PLAN_APPROVAL)
      echo "  => auto-approving plan..."
      curl -sS -X POST "$BASE_URL/v1/tasks/$TASK_ID/plan/feedback" \
        -H "content-type: application/json" \
        -d '{"feedback": null}' >/dev/null
      ;;
  esac
  sleep 1
done

curl -sS "$BASE_URL/v1/tasks/$TASK_ID/result" >"$RESULT_FILE"

if [[ "$STATUS" == "READY_FOR_REVIEW" && "$AUTO_ACCEPT" == "1" ]]; then
  curl -sS -X POST "$BASE_URL/v1/tasks/$TASK_ID/accept" >"$FINAL_FILE"
else
  cp "$RESULT_FILE" "$FINAL_FILE"
fi

echo "task_json=$TASK_FILE"
echo "result_json=$RESULT_FILE"
echo "final_json=$FINAL_FILE"

jq '{task_id,status,modified_files_count:(.modified_files|length),diagnostics_count:(.diagnostics|length)}' "$FINAL_FILE"
