# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Service Layout

Polyglot monorepo — four packages, three runtimes:
- `apps/editor-client` (TypeScript): Zod-validated contracts + HTTP client for the backend
- `apps/vscode-extension` (TypeScript): VS Code extension — UI, polling, review panel
- `services/agentd-py` (Python): orchestration backend — task lifecycle, planning, patch, provider integrations
- `services/indexer-rs` (Rust): incremental indexing and symbol graph (tree-sitter + LSP diagnostics)

`apps/editor-client` is an npm workspace package consumed by the VS Code extension. Type changes there flow upstream to the extension via `BackendTaskClient` interface and Zod schemas.

## Commands

### TypeScript (root — runs across all workspaces)
```bash
npm install
npm run build        # build all TS packages
npm run test         # vitest across editor-client + vscode-extension
npm run typecheck    # tsc --noEmit across all workspaces
```

### TypeScript (workspace-scoped)
```bash
npm run -w @ai-editor/editor-client test
npm run -w @ai-editor/vscode-extension test
npm run -w @ai-editor/vscode-extension typecheck
```

### Python backend
```bash
cd services/agentd-py
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

uvicorn agentd.main:app --reload --port 8000   # start server

pytest                          # all tests
pytest tests/test_foo.py        # single file
pytest tests/test_foo.py::test_bar  # single test

ruff check .                    # lint
ruff format .                   # format
mypy agentd                     # type-check
```

### Rust indexer
```bash
cd services/indexer-rs
cargo build
cargo run -- index --workspace /path/to/repo --snapshot-path /path/to/.ai-editor/index-snapshot.json --watch 0
cargo run -- query --snapshot-path /path/to/.ai-editor/index-snapshot.json --mode symbol_name --value build --depth 2 --limit 200
```

### Stress / E2E scripts
```bash
cd scripts/stress
./bootstrap.sh          # one-time env setup
./start-backend.sh      # start agentd-py with the right provider
python e2e-stress-test.py
```

## Task Lifecycle (Spec-First Model)

```
QUEUED → CONTEXT_READY → AWAITING_PLAN_APPROVAL ─[user gate]─► PLANNED
       → EXECUTING → VALIDATING ⇄ REPAIRING → VALIDATED
       → READY_FOR_REVIEW → PROMOTING → SUCCEEDED
                                              (or FAILED / ABORTED at any point)
```

Key invariants:
- **Shadow workspace**: every task gets its own shadow copy of the real repo. All patch ops run on the shadow; the real workspace is only written on `PROMOTING` (accept).
- **Plan approval gate**: the orchestrator pauses at `AWAITING_PLAN_APPROVAL`, emits `plan_markdown`, and waits for `POST /v1/tasks/{id}/plan/feedback`. `feedback=null` means approve; a string triggers plan regeneration.
- **Milestone snapshot**: at every `AWAITING_PLAN_APPROVAL` transition the engine serializes the full task state into `plan_approval_snapshot`. This is the source of truth used to reconstruct the exact plan-review state during resume rollbacks — never reconstructed from assumptions.
- **Step execution** is bounded. Each step uses `completed_step_ids` to skip already-done work; failed steps checkpoint the shadow back before giving up.

## Resume / Rollback (child task pattern)

`POST /v1/tasks/{id}/resume` creates an **immutable child task** linked via `resume_of_task_id`. The parent is never mutated.

| Stage | Child starts as | What fires |
|-------|-----------------|------------|
| `plan` | `QUEUED` | `orchestrator.run_task()` (full re-plan) |
| `feedback` | `AWAITING_PLAN_APPROVAL` (snapshot state) | nothing async — user calls `/plan/feedback` on child |
| `execute` | `PLANNED` (current plan + completed_step_ids copied) | shadow cloned, `orchestrator.resume_task()` |

Concurrency guards: `_in_flight_feedback` and `_in_flight_resume` are closure-scoped sets in `build_router`. Check+add with no `await` in between is race-safe in asyncio.

## Architecture Details

### Python backend (`agentd/`)
- `api/routes.py` — all FastAPI routes; `build_router()` closes over store/orchestrator/workspace_manager
- `domain/models.py` — all Pydantic models: `TaskRecord`, `TaskView`, `TaskResult`, `TaskMilestoneSnapshot`, `ResumeTaskRequest`, etc.
- `domain/state_machine.py` — `transition()` validates all status changes; direct `store.create()` with a pre-set status bypasses it (used for child task creation)
- `orchestrator/engine.py` — `AgentOrchestrator`: `run_task()`, `continue_task()`, `resume_task()`, `_execute_plan()`
- `orchestrator/scripted_engine.py` — deterministic engine for testing (replays fixed patch/plan sequences)
- `patch/engine.py` — `PatchEngine`: applies `patch_ops` (create_file, search_replace, replace_node, apply_diff) on the shadow workspace
- `providers/` — one file per model provider (anthropic, openai, gemini, groq, huggingface, watsonx, openrouter); all implement `ReasoningEngine` contract in `providers/contracts.py`
- `reasoning/` — prompt builders and structured output parsers for plan + patch calls
- `retrieval/` — reads `index-snapshot.json` artifacts; injected into planning context as `retrieval_context`
- `storage/` — `InMemoryTaskStore` (tests) and SQLite store (production); both implement the `TaskStore` protocol
- `workspace/shadow.py` — `ShadowWorkspaceManager`: `prepare()`, `clone()`, `promote()`
- `validation/` — runs configurable validation commands (pytest, tsc, cargo test) on the shadow; returns `ValidationResult`

### TypeScript packages
- `editor-client/src/contracts/task-contracts.ts` — canonical Zod schemas + `BackendTaskClient` interface; source of truth for all API shapes
- `editor-client/src/client/http-backend-client.ts` — `HttpBackendClient`: snake_case↔camelCase mapping, all API calls
- `editor-client/src/domain/` — `types.ts`, `schemas.ts`, `task-state.ts`
- `vscode-extension/src/controller.ts` — `AiEditorController`: orchestrates all user actions; pure business logic, no VS Code API dependencies
- `vscode-extension/src/extension.ts` — VS Code activation, command registration, wires controller to UI
- `vscode-extension/src/review-panel.ts` — WebView panel for task review

### Retrieval pipeline
- `indexer-rs` writes `index-snapshot.json` with `nodes`/`edges`/`diagnostics`/`stats`
- `agentd-py` reads the snapshot per task via `retrieval/` module; if missing, auto-triggers one index run
- Retrieval context flows into `create_markdown_plan`, `create_plan`, `create_patch` as `retrieval_context` dict
- Stale/missing snapshots emit warning diagnostics but never block orchestration

### Testing patterns
- Python tests in `services/agentd-py/tests/` use stub `Reasoner` classes + `InMemoryTaskStore` + `ShadowWorkspaceManager(tmp_path)`
- `pytest-asyncio` with `@pytest.mark.asyncio` for all async tests
- Integration-style tests (no mocks of the file system or HTTP) — real `tmp_path` shadows, real `PatchEngine`
- TypeScript tests use vitest; VS Code extension tests use a stub `ControllerUI` implementation

## Key Configuration

### Python backend env vars
- `AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH` — path to index-snapshot.json (default: `<workspace>/.ai-editor/index-snapshot.json`)
- `AI_EDITOR_RETRIEVAL_MAX_AGE_SEC` — max snapshot age before auto-reindex (default: `900`)
- `AI_EDITOR_INDEXER_INDEX_CMD` — command template for auto-indexing (`{workspace}`, `{snapshot_path}`)
- Provider API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, etc.

### VS Code extension settings (package.json contributes.configuration)
- `aiEditor.backendBaseUrl` — default `http://localhost:8000`
- `aiEditor.defaultMode` — `inline | file_edit | project_edit | autonomous`
- `aiEditor.pollIntervalMs` — default `2000`

---

## Debugging Methodology

### Starting the backend for local testing

Always use `start-backend.sh` rather than running uvicorn directly — it sets all env vars correctly:

```bash
# From repo root — pick a workspace and provider
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
bash scripts/stress/start-backend.sh \
  --backend gemini \
  --workspace "$PWD/workspaces/shadow-forge-stress" \
  --validation-profile none   # use 'full' when testing validation

# Verify it's up
curl -s http://localhost:8000/health
```

Log file lands in `.tmp/stress-<timestamp>/logs/agentd.log`. Tail it while running tasks:

```bash
tail -f .tmp/stress-*/logs/agentd.log | grep -v "GET /v1/tasks"   # filter out poll noise
```

### Opening the VS Code extension development host

```bash
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/shadow-forge-stress"
```

After any TypeScript change: `npm run build` then reload the extension host window (`Cmd+Shift+P` → Developer: Reload Window).

### Scripted end-to-end testing (acts as a human)

`scripts/verify/` has a three-stage flow that drives a task from submission to acceptance:

```bash
cd services/agentd-py && source .venv/bin/activate && cd -

# Stage 1 — submit task, wait for plan
python scripts/verify/01_create_task.py '<goal>' '<workspace_path>'

# Stage 2 — optional: provide feedback to regenerate plan
python scripts/verify/02_feedback.py '<feedback text>'

# Stage 3 — approve plan, wait for READY_FOR_REVIEW, accept patch
python scripts/verify/03_finalize.py
```

Task ID is persisted to `/tmp/ai-editor-verify-state/current_task_id.txt` between stages.

### Inspecting a task mid-flight

```bash
TASK_ID=task-xxxx
curl -s http://localhost:8000/v1/tasks/$TASK_ID | python3 -m json.tool
curl -s http://localhost:8000/v1/tasks/$TASK_ID/result | python3 -m json.tool
```

### Watching the SSE patch stream directly

```bash
curl -sN --no-buffer "http://localhost:8000/v1/tasks/$TASK_ID/stream-patch" \
  -H "Accept: text/event-stream"
```

Expected events: `operation_success`, `operation_error`, `done`. Connect this **before** approving the plan — the stream stays open through the entire execution.

### Diagnosing a stuck task

| Symptom | Likely cause | Check |
|---------|-------------|-------|
| Stuck at `CONTEXT_READY` | Gemini rate limit / timeout | Log: `Gemini transient error` |
| Stuck at `PLANNED` after approval | Backend restarted mid-flight; no coroutine driving it | Task is an orphan — start a new task |
| Stuck at `PLANNED` without restart | `continue_task` still generating JSON plan (Gemini slow) | Log: `[PLAN] Plan Approved` present? |
| SSE stream closes immediately | Replay buffer has stale `done` from prior run, or status is terminal | Check task status; start new task |
| No events in activity log | Stream connects after execution already finished | Replay buffer replays on reconnect — events should still appear |

### Asyncio race conditions to watch for

The orchestration engine is single-process asyncio. Key race windows:

- **`_running_tasks` vs SSE connect**: `run_task`/`continue_task` add to `_running_tasks` at their first line, but they run inside `asyncio.create_task()` — they don't start until the current coroutine yields. The route handler pre-adds `task_id` to `_running_tasks` before `create_task()` for the feedback route to close this window.
- **Replay buffer pollution**: `run_task` broadcasts `done` when pausing at `AWAITING_PLAN_APPROVAL`. This goes into the replay buffer and would cause any new SSE subscriber to close immediately. Fix: `clear_replay()` instead of `broadcast(done)` at the pause point.
- **`webview.html` coalescing**: VS Code coalesces rapid sequential writes to `webview.html` into one render. Use `postMessage` for incremental updates (patch events); only replace the full HTML on genuine state changes (status, result, files).

### Reading artifacts to understand what happened

Every task writes debug artifacts to `<workspace>/.agentd/artifacts/<task_id>/`. This is the primary source of ground truth when a task behaves unexpectedly — check here before guessing.

```
<task_id>/
  plan-evidence.json          # retrieval context fed to the planner (files, symbols, diagnostics)
  markdown-plan-draft.json    # raw LLM output for the markdown plan
  markdown-plan-critique.json # critique pass result (issues found, severity)
  markdown-plan-final.json    # final markdown plan after critique
  plan.json                   # approved JSON execution plan (steps, ops, targets)
  json-plan-draft.json        # raw LLM output for JSON plan generation
  json-plan-critique.json     # JSON plan critique result
  json-plan-final.json        # final JSON plan
  full-validation.json        # validation output after execution

  step-<id>/
    debug-patch-raw.json      # raw LLM patch output before parsing
    attempt-<n>/
      patch-context.json      # full context sent to LLM for patch generation
      patch.json              # parsed patch candidate(s)
      preflight-<cN>.json     # preflight check result per candidate (file existence, policy)
      ranking.json            # candidate scoring and selection rationale
```

**What to look at for each failure mode:**

| Problem | Artifact to read |
|---------|-----------------|
| Plan looks wrong / misses files | `plan-evidence.json` — did retrieval find the right files/symbols? |
| Plan keeps getting rejected by critique | `markdown-plan-critique.json` — what issues did the critique find? |
| JSON plan fails schema validation repeatedly | `json-plan-draft.json` — is the LLM output malformed? |
| Step fails preflight (file not found, policy violation) | `step-<id>/attempt-<n>/preflight-<cN>.json` |
| Wrong patch generated (bad search string, wrong location) | `step-<id>/attempt-<n>/patch.json` + `patch-context.json` |
| Patch candidate ranked poorly / wrong one selected | `step-<id>/attempt-<n>/ranking.json` |
| Validation fails after patching | `full-validation.json` or `step-<id>/attempt-<n>/` validation file |

**Quick command to inspect the latest task's artifacts:**

```bash
TASK_ID=$(cat /tmp/ai-editor-verify-state/current_task_id.txt)
ARTIFACTS="<workspace>/.agentd/artifacts/$TASK_ID"

# See what was generated
ls $ARTIFACTS
ls $ARTIFACTS/step-*/attempt-*/

# Read a specific artifact
cat $ARTIFACTS/plan-evidence.json | python3 -m json.tool | less
cat $ARTIFACTS/step-s1/attempt-1/ranking.json | python3 -m json.tool
```

The API also exposes artifacts:
```bash
curl -s http://localhost:8000/v1/tasks/$TASK_ID/artifacts | python3 -m json.tool
```

### Provider-specific notes

- **Gemini**: use `gemini-flash-latest` (stable alias). Preview models (`gemini-3-flash-preview`, `gemini-2.0-flash-exp`) have lower quota and hit 429s on large planning prompts. Set `AI_EDITOR_GEMINI_TIMEOUT_SEC=600` in `.env` — the default 120s is too short for complex tasks.
- **Model env var**: `AI_EDITOR_GEMINI_MODEL` in `.env` must be on its own line — concatenating it with another var (no newline) silently breaks the export.
- Transient 429/503 errors retry automatically (up to 4 attempts, exponential backoff). A task stuck at `CONTEXT_READY` or `PLANNED` with log lines `Gemini transient error (attempt N/4)` is retrying — wait it out.
