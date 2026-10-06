# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Service Layout

Polyglot monorepo — four packages, three runtimes:
- `apps/editor-client` (TypeScript): Zod-validated contracts + HTTP client for the backend
- `apps/vscode-extension` (TypeScript): VS Code extension — UI, polling, review panel
- `services/agentd-py` (Python): orchestration backend — task lifecycle, planning, patch, provider integrations
- `services/indexer-rs` (Rust): incremental indexing and symbol graph (tree-sitter parse + LSP-resolved Calls/Implements/Inherits edges + LSP diagnostics)

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
npm run -w @crucible/editor-client test
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension typecheck
```

### Python backend
```bash
cd services/agentd-py
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

python -m agentd.serve --port 8000 --reload   # start server (127.0.0.1 only; writes ~/.crucible/run/agentd-8000.token)

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
cargo run -- index --workspace /path/to/repo --snapshot-path /path/to/.crucible/index-snapshot.json --watch 0
cargo run -- query --snapshot-path /path/to/.crucible/index-snapshot.json --mode symbol_name --value build --depth 2 --limit 200
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
- **Agentic planning**: `CONTEXT_READY` means the `PlanningAgent` is actively exploring the real workspace (calling `search_code`, `read_file`, `list_directory`) before committing to a plan. This can take many tool calls and is the longest phase before `AWAITING_PLAN_APPROVAL`.
- **Plan approval gate**: the orchestrator pauses at `AWAITING_PLAN_APPROVAL`, emits `plan_markdown`, and waits for `POST /v1/tasks/{id}/plan/feedback`. `feedback=null` means approve; a string triggers plan re-exploration.
- **Agentic execution**: each plan step runs a `ToolLoop` (ReAct: Thought→Tool→Observe) instead of a single-shot patch call. The execution agent can `search_code`, `read_file`, `run_command`, and `search_semantic` before emitting patch ops. When the agent determines a step's approach is wrong, it emits `revision_needed` → delta replan fires.
- **Verify phase always runs**: after emitting a patch, the execution agent always enters a verify phase (Phase 2) to run linters and tests. There is no skip for steps without a `test_command` — the agent discovers what to run from `testing_strategy` and touched files. `verify_done(verified=true)` is gated by the state machine (see below).
- **Verify-phase state machine** (`tools/verify_phase_sm.py`): `VerifyPhaseStateMachine` owns all verify-phase state — replaces five scattered boolean flags previously in `loop.py`. 7 states (`EXPLORE`, `PATCH_FAILED_MUST_READ`, `PATCH_FAILED_CAN_RETRY`, `POSTPATCH_BLOCKING`, `POSTPATCH_CLEAN`, `TEST_FAILED`, `TEST_PASSED`), 6 events. Two enforcement layers: (1) per-turn schema filtering — `allowed_tools()` filters the inner `tool` enum, `allowed_action_types()` filters the outer `type` enum so the model literally cannot emit `verify_done` from `EXPLORE` or `emit_patch` from `POSTPATCH_CLEAN`; (2) state-handler check in the `verify_done` branch as a defense-in-depth. `emit_patch` dedup clears on every transition. `MAX_PATCH_RETRIES = 5` consecutive engine failures from `CAN_RETRY` raises `VerifyPhaseExhausted` → graceful `VerifyResult(verified=False)`. The model gets its current state explained each turn via `sm.state_description(iteration, error_summary, failure_summary)` injected into the user payload's `instruction` field.
- **Command-only steps** (`_is_command_only_step` in `tools/loop.py`): a step with empty targets (or folder-only targets like `tests/`) has nothing to patch — its goal is running commands ("run the full test suite"). It starts the SM in `POSTPATCH_CLEAN` (EXPLORE's only exits are patch events, so it would otherwise be trapped) with `command_only=True`: `verify_done` is gated behind `TEST_PASSED` (no "nothing to run" skip — the commands ARE the step), reads target the shadow from turn one, and the verify budget applies. On failure the model may still `emit_patch` — empty targets route novel writes through the scope-extension gate — or escalate via `revision_needed`. The chat classifier routes run/build/verify requests to `large_change` (NOT qa) so they become executable tasks. **Contract caveat:** `PlanStepSchema.targets` in editor-client must stay `.min(0)` — a `.min(1)` silently broke the ReviewCard for command-only plans (`getTaskResult` Zod-threw on every poll; the live slot never rendered).
- **testing_strategy vs test_command**: the planner sets `testing_strategy` on every code step as a natural-language hint (e.g. "run pytest on test_auth.py"). `test_command` is only set when the test file is itself a target of that step — this prevents running stale tests when the import hasn't been updated yet. The execution agent uses `testing_strategy` to discover the right command when `test_command` is absent.
- **read_file phased target**: in the explore phase `read_file`/`search_code` read the **real workspace** (pre-patch). Once the SM transitions out of `EXPLORE` (i.e. the first patch has been applied), `ToolRegistry.use_shadow_for_reads()` is called and subsequent reads return the **shadow workspace** — the model can see exactly what its patches produced before re-patching.
- **Pre-existing failure normalization**: `_normalize_error_message` in `engine.py` fingerprints pytest and cargo test output so that failures present before patching are filtered from the post-patch baseline comparison. Cargo failures are identified by extracting failed test names from the `failures:` block; shadow paths (`.crucible/state/shadows/task-xxx/`) are stripped before comparison.
- **Delta replan**: a `revision_needed` signal from the execution agent triggers `PlanningAgent.revise()`, which explores the workspace and emits targeted step revisions without restarting the task. Budget: `max_delta_replans` (default 3).
- **Milestone snapshot**: at every `AWAITING_PLAN_APPROVAL` transition the engine serializes the full task state into `plan_approval_snapshot`. Used verbatim to reconstruct the exact plan-review state for resume rollbacks.
- **Step execution** is bounded. Each step uses `completed_step_ids` to skip already-done work; failed steps checkpoint the shadow back before giving up.

### Tier B — lifecycle control & durable telemetry

These four features share one in-memory mechanism, the **`TaskControl`** channel (`orchestrator/task_control.py`: `abort` event + `abort_revert` flag + live `step_review_auto_accept`). `AgentOrchestrator._task_controls: dict[str, TaskControl]` holds one per running task; registered in `continue_task`/`resume_task` right before `_execute_plan`, released in `_execute_plan`'s `finally` (single owner). `_execute_plan` only **reads** the control (never creates one). Single-process asyncio ⇒ check+set with no `await` between is race-safe (same pattern as `_in_flight_*`).

- **Cooperative abort (F12)** — `POST /v1/tasks/{id}/abort {revert}` sets `control.abort_revert` then `control.abort.set()`; it does NOT touch the shadow/status (returns 409 if no control = task not running). The loop polls `control.abort` at the top of each step (`_execute_plan`) AND between ReAct iterations (`ToolLoop`, via the optional `abort` event — passed ONLY to the step-execution loop, never the inline-change loop) and raises `TaskAborted`. The `except TaskAborted` handler owns the unwind: rollback if `abort_revert`, clean shadow, transition `ABORTED` **in place on the caller's object**, write a ✗ stopped/reverted breadcrumb. Because `_partial_promote` runs *after* a step returns, a mid-step abort promotes nothing of the in-flight step. `/cancel` stays as-is for queued/terminal tasks (its route-side shadow free is unsafe for a running task — that's what `/abort` fixes).
- **True revert at reject/abort (F8)** — a pinned **pre-execution checkpoint** (`execution_state.pre_execution_checkpoint`) is captured at `_execute_plan` start under a `_baselines/<task_id>/` root that `prune_checkpoints` never scans (so it survives until terminal; cleared in the terminal `finally` via `_clear_pre_execution_checkpoint`). `_rollback_to_pre_execution(task)` = `_restore_shadow_checkpoint(baseline)` then `workspace_manager.promote(task)` — an exact rollback reusing existing machinery (promote copies modified files present in the shadow and **deletes** those absent, keyed on `task.modified_files`). `POST /reject` now performs this true revert → `ABORTED` (was "keep changes"); the ReviewCard's "Discard all changes" maps to it. Accept stays `→PROMOTING→promote→SUCCEEDED` (the final promote reconciles step-deleted files, so it is NOT dropped — deviation from the original spec).
- **Durable telemetry (F9)** — `FailureSummary`/`RunSummary` on `TaskRecord`. `_finalize_run_summary` runs at every terminal (`_execute_plan` finally) **and at `READY_FOR_REVIEW`** (so the ReviewCard shows "N of M" durably on reload, before accept/discard) plus the accept/reject/cancel routes. `failure_summary` is written richly at the FAILED except-site and via a finally fallback (always present on FAILED). Exposed via `resolve_live_state` (FAILED/ABORTED → `failure_summary`; `run_summary` whenever present) and `TaskResult`/`TaskView`. The extension's ephemeral `runDeviations`/`lastStepStarted`/`lastPatchError` are now a live-feel fallback the durable copy supersedes on reload.
- **Dynamic review preference (item 5)** — `POST /v1/tasks/{id}/review-pref {auto_accept}` mutates `control.step_review_auto_accept`; `_execute_plan` re-reads it (per step, falling back to the record value) instead of the frozen `TaskRecord.step_review_auto_accept`. Flipping to auto-accept while a step gate is **pending** resolves that gate as accept via `resolve_pending_step_review` (fires the same `_pending_step_decisions` future as `/step-decision`). The composer checkbox stays enabled during execution and posts both directions.
- **`_write_chat_completion` is ABORTED-aware** — returns silently for `ABORTED` so the `finally` never writes "Execution failed: <diag>" over the abort breadcrumb (the `e7b5f39`-class stale-completion bug).

### Task narrative (LLM-authored run summary)

Distinct from the deterministic `run_summary` (counts): `TaskNarrative` (`{outcome, headline, points}`) is an LLM-authored story of the run, for the Review/Error cards AND as next-chat-turn context. Spec/plan: `docs/superpowers/specs|plans/2026-06-13-task-narrative*`.
- **Append-only event log:** `execution_state.run_events: list[RunEvent]` (`kind: step_done|step_failed|replan`), appended in `_execute_plan` (`_append_run_event`) at the step-complete (after promote+mark), step-exhausted, repair-complete/fail, and replan (BEFORE `_apply_revision`) sites. **Never pruned** — a delta replan's reverted steps keep their `step_done` events, so the narrative tells the whole story; the log is decoupled from `completed_step_ids`/`x of n` (which a replan moves) and immune to plan growth/shrink.
- **Per-step note is FREE + RICH:** a `step_summary` field on the `verify_done` action (`AGENT_STEP_RESPONSE_SCHEMA`) → `VerifyResult.step_summary` → `StepRunResult.step_summary` → the `step_done` event's `note` (deterministic `edited <files>` fallback when the model omits it; capped 1500 chars). The per-step note is deliberately a **detailed account** (not a finished one-liner) — raw material the end summarizer distills, so neither layer is redundant.
- **Synthesis:** `ReasoningEngine.summarize_run(...)` (one structured call; `ReasoningEngineImpl` + `narrative_prompts.py`; `ScriptedReasoningEngine` takes a `run_narrative` kwarg). Called in `_finalize_task_narrative` at the `_execute_plan` finally — at **READY_FOR_REVIEW** (outcome `succeeded`, so the ReviewCard shows it pre-accept) and at FAILED/ABORTED. **Best-effort** (try/except): a synthesis failure or an engine lacking `summarize_run` never fails the task (narrative stays `None`). Discard-after-review does NOT regenerate (v1).
- **Exposure/consumption:** on `TaskRecord.task_narrative`, surfaced via `resolve_live_state` + `TaskResult`/`TaskView`; `_write_chat_narrative` persists it as a durable `agent/text` transcript message (rides `thread.messages → history` into explore/QA next turn); `_find_recent_task` adds it to the recent-task dict for the classifier (resumable tasks only). Frontend: `TaskNarrativeSchema` (editor-client), forwarded to `renderLiveReview`/`renderLiveError`, rendered as headline+points on ReviewCard/ErrorCard.

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
- `domain/models.py` — all Pydantic models: `TaskRecord`, `TaskBudget`, `TaskUsage`, `TaskExecutionState`, `AgentToolTrace`, `ToolCall`, `ToolResult`, `DeltaReplanRequest`, `PlanningResult`, `PlanRevisionResult`, `RevisedStep`, `TaskMilestoneSnapshot`, `ResumeTaskRequest`, etc.
- `domain/state_machine.py` — `transition()` validates all status changes; direct `store.create()` with a pre-set status bypasses it (used for child task creation)
- `orchestrator/engine.py` — `AgentOrchestrator`: `run_task()`, `continue_task()`, `resume_task()`, `_execute_plan()`; orchestrates PlanningAgent and ToolLoop
- `orchestrator/scripted_engine.py` — deterministic engine for testing (replays fixed responses for all three reasoning methods)
- `planning/agent.py` — `PlanningAgent`: thin coordinator; `generate_plan()` and `revise()` both delegate to `PlanningLoop`
- `planning/loop.py` — `PlanningLoop`: explore-then-commit ReAct loop; calls `create_planning_step()` per iteration; returns `PlanningResult` or `PlanRevisionResult`
- `planning/registry.py` — `PlanningToolRegistry`: read-only tools for the planning loop (`search_code`, `read_file`, `list_directory`, `search_semantic`, `query_graph`); also exposes `_render_query_result` used by both registries
- `retrieval/graph_walker.py` — `GraphWalker`: in-process BFS over `index-snapshot.json` backing the `query_graph` tool. File seed (`path`) → distinct neighbour files grouped by direction; symbol seed (`path:Symbol`) → symbol-level Calls/Imports/References/Inherits/Implements with line numbers. mtime-cached, thread-safe; `GraphWalkerSnapshotError` for unreadable snapshots
- `planning/prompts.py` — system prompt, user payload builder, and `PLANNING_STEP_RESPONSE_SCHEMA` (discriminated union: `tool_call | emit_plan | emit_revision`)
- `tools/loop.py` — `ToolLoop`: ReAct execution loop per plan step; calls `create_tool_step()` per iteration; returns `PatchResult` or `PlanHandoff`; `broadcast_key` param routes SSE events to a custom channel (used by inline change path to send to chat channel instead of task channel); `skip_verify=True` skips verify phase (used by inline changes)
- `tools/registry.py` — `ToolRegistry`: tools for the execution loop (`search_code`, `read_file`, `run_command`, `search_semantic`, `query_graph`); enforces path traversal protection and shell allowlist
- `tools/search.py` — `search_code` (ripgrep) and `search_semantic` (vector index query)
- `tools/files.py` — `read_file` with line range support and path traversal rejection
- `tools/shell.py` — `run_command` with configurable allowlist and timeout
- `reasoning/contracts.py` — `ReasoningEngine` Protocol: `create_plan()`, `create_patch()`, `create_tool_step()`, `create_planning_step()`, plus critique methods
- `reasoning/engine.py` — `ReasoningEngineImpl`: default implementation wiring prompt builders to providers
- `reasoning/tool_prompts.py` — system prompt, payload builder, and `AGENT_STEP_RESPONSE_SCHEMA` (discriminated union: `tool_call | emit_patch | revision_needed`) for the execution tool loop
- `reasoning/prompt_builder.py` — prompt builders for plan/patch calls
- `patch/engine.py` — `PatchEngine`: applies `patch_ops` (create_file, search_replace, replace_node, apply_diff) on the shadow workspace
- `providers/` — one file per model provider (anthropic, openai, gemini, groq, huggingface, watsonx, openrouter); all implement `ReasoningEngine` in `reasoning/contracts.py`
- `providers/openai_compatible_transport.py` — `OpenAICompatibleTransport`: the generic OpenAI chat-completions client (configurable base URL). Subclassed by `OpenRouterJsonTransport`, which adds site headers, `provider.require_parameters`, and the openrouter.ai model-capability registry via three hooks (`_default_headers`, `_build_extra_body`, `_reasoning_config`). Carries the **sticky JSON-mode downgrade**: strict `json_schema` is tried once, and on failure the instance falls back to `json_object` + schema-in-prompt for the rest of the process and clears `supports_{one,any}of_grammar`. The downgrade fires only on failures that genuinely prove the endpoint can't honor schemas — not on transients (429/5xx/timeouts/connection errors), not on truncated output (`finish_reason == "length"`), and not on empty responses, so a rate-limit blip never permanently degrades the process. Restart re-probes.
  - **LIMITATION (a) — the downgrade conflates "cannot honor THIS schema" with "cannot honor ANY schema."** `_downgrade_json_mode` flips `_json_mode` to `json_object` AND clears `supports_{one,any}of_grammar` in the same instant, and `_json_mode` is never retried. So an endpoint that serves `response_format: json_schema` perfectly well but rejects the `oneOf`/`anyOf` UNION construct (real vLLM/xgrammar and LM Studio behavior) returns a 400 → correctly judged probative → permanently downgraded to schema-in-prompt, even though the very next call would have sent a *flat* schema it would likely have accepted strictly. Not a regression (pre-branch OpenRouter re-attempted the union on every call), but the downgrade is **not** always correct — don't build on it as if it were. **Intended fix: a two-stage downgrade** — on the first probative failure clear only the grammar flags and retry strict ONCE with the narrowed (flat) schema; downgrade `_json_mode` only if flat strict also fails. Explicitly NOT the right fix: a constructor/env seam for `supports_anyof_grammar` — nobody will set an env var for an endpoint whose URL they just pasted in.
  - **LIMITATION (b) — the stored API key is slot-scoped, not endpoint-scoped.** The extension stores it under `crucible.providerKey.<backend>`, so changing only `CRUCIBLE_OPENAI_COMPAT_BASE_URL` retargets the endpoint while the previous host's bearer token stays on file and keeps being sent. Every other provider is safe by construction (key env var 1:1 with endpoint). Remedy: the settings panel's **"Clear key"** button (`settings/clearProviderKey` → `SettingsDeps.deleteSecret` → `RuntimeManager.deleteProviderKey`) — a blank API-key field means "keep the stored key", so removal has to be explicit. It also flags `restartRequired`, because the running managed backend still holds the old key in its spawn env.
- `providers/reasoning_effort.py` — `ReasoningEffort` (StrEnum `off/low/medium/high/max`, cheapest-first `LADDER`) + `EffortSupport`: a frozen `supported`/`unsupported` pair per transport. A rung in NEITHER is UNKNOWN — unverified, which the UI renders differently from "unsupported" (an arbitrary pasted `openai_compatible` endpoint is entirely unknown); this is the same three-way split the context-window probe already draws between "couldn't tell" and "downgraded". `.resolve(level)` clamps an unsupported rung DOWN to the nearest non-unsupported one; upward is a last resort with exactly one instance in the whole feature — Groq 400s on `reasoning_effort: "none"`, so `off` resolves to `low`. A probative 400 marks only the offending RUNG unsupported for the process, never the whole capability — deliberately narrower than the sticky JSON-mode downgrade above, whose documented flaw (LIMITATION a) is collapsing "cannot honor THIS" into "cannot honor ANY"; transient failures (429/5xx/timeout/connection) mark nothing here either. Wire shape is per-transport: `openai_compatible` sends a top-level `reasoning_effort` (vLLM's front door, which auto-injects `enable_thinking`); `openrouter` nests `reasoning: {effort}`; `groq` sends `reasoning_effort` but can't express `off`; `ollama` sends a top-level `think` (bool or level string); `gemini` sends `thinking_config.thinking_level` and never alongside `thinking_budget` (sending both is a documented Gemini error); `turboquant` declares every rung unsupported and has no setter at all — `_build_body` applies the strict json_schema GBNF grammar only when `thinking_budget == 0` (a llama.cpp workaround), so any rung above Off would trade the strongest malformed-output defense in the stack for a latency dial. A startup hook applies the env-seeded rung at boot; skip it and the rung is reported by `GET /v1/config` but never actually sent to the transport, because `__init__` is synchronous while `apply_reasoning_effort` is async. Spec: `docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md`.
- `retrieval/` — reads `index-snapshot.json` artifacts; injected into planning context as `retrieval_context`
- `storage/` — `InMemoryTaskStore` (tests) and SQLite store (production); both implement the `TaskStore` protocol
- `workspace/shadow.py` — `ShadowWorkspaceManager`: `prepare()` (full copy), `prepare_lightweight()` (shallow copy for inline changes — no git init, just file tree), `clone()`, `promote()`
- `validation/` — runs configurable validation commands (pytest, tsc, cargo test) on the shadow; returns `ValidationResult`
- `orchestrator/broadcaster.py` — `EventBroadcaster` (renamed from `PatchEventBroadcaster`): keyed by `channel_id` (any string — task_id for task SSE, a UUID for chat SSE); single streaming mechanism for all SSE in the system
- `chat/agent.py` — `ChatAgent`: explore → classify → respond coroutine; `_draft_plan_markdown()` for small_change path
- `chat/storage.py` — `ChatThreadStore`: SQLite multi-thread storage; `resolve_diff_card(inline_task_id, resolution)` patches diff card resolved state; `append_plan_card(thread_id, task_id, md)` appends a plan version (dedups vs the task's latest) — see "Live cards" below
- `chat/classifier.py` — `IntentClassifier`: qa / small_change / large_change
- `chat/models.py` — `ChatMessage`, `ChatThread` dataclasses
- `chat/app_factory.py` — test-only `build_app()` — keeps provider transport init out of import path; uses `ScriptedReasoningEngine` and `_NullTransport`

### TypeScript packages
- `editor-client/src/contracts/task-contracts.ts` — canonical Zod schemas + `BackendTaskClient` interface + `StreamEvent` discriminated union (covers both task SSE and chat SSE events); source of truth for all API shapes
- `editor-client/src/client/http-backend-client.ts` — `HttpBackendClient`: snake_case↔camelCase mapping, all API calls
- `editor-client/src/domain/` — `types.ts`, `schemas.ts`, `task-state.ts`
- `vscode-extension/src/controller.ts` — `CrucibleController`: orchestrates all user actions; pure business logic, no VS Code API dependencies
- `vscode-extension/src/extension.ts` — VS Code activation, command registration, wires controller to UI
- `vscode-extension/src/review-panel.ts` — WebView panel for task review

**Build order**: `vscode-extension` types off `editor-client`'s compiled `dist/index.d.ts`, not source. After changing `editor-client`, run `npm run -w @crucible/editor-client build` before running `vscode-extension` typecheck — otherwise you'll get stale-type errors that don't exist in source.

### Chat interface
Routes registered when `chat_agent` is non-None in `build_router()`:
- `GET /v1/chat/threads?workspace=<path>` — list threads for a workspace. Summaries are enriched at query time (no schema change): `message_count`, `updated_at` (last message timestamp, falls back to `created_at`), and `status` — a history-chip value (`running | review | done | failed | null`) derived from the thread's `active_task_id` task via `thread_status_chip` in `chat/live_state.py`
- `POST /v1/chat/threads` — create thread (`{workspace, title}`)
- `GET /v1/chat/threads/{thread_id}` — get thread with messages
- `POST /v1/chat/threads/{thread_id}/message` — SSE stream. Body: `{content, step_review?}` — `step_review` (bool) is the composer's "Review each step" toggle, sent with every message; it's applied only when the turn creates a task (`large_change`): `step_review_auto_accept = not step_review`, frozen into the TaskRecord at creation (flipping the checkbox mid-task changes nothing). When omitted/non-bool the `CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT` env default applies
- `POST /v1/chat/threads/{thread_id}/review-pref {auto_accept}` — live "Review each edit" for an **in-flight controller turn** (see "Live review preference" below). 404 unknown thread, 409 when no turn is running

#### Reactive controller (`CRUCIBLE_CHAT_CONTROLLER=1`) — supersedes the legacy `ChatAgent` flow below

Merged on `main` via PR #2 (2026-06-21), flag-gated. `controller_factory.select_chat_handler` returns `ChatController` (`chat/controller.py`) instead of the legacy `ChatAgent` when the flag is truthy; both expose the same `handle_message(...)` surface + `_store`/`_broadcaster` attrs. **There is no explore→classify→route pre-classification.** Each turn runs one `ControllerLoop` (ReAct, mirrors `PlanningLoop`; `chat/controller_loop.py`): explore tools → **decide** one action — `answer` | `clarify` | `propose_mode` | `edit` | `submit_changes`. Phases are owned by `ControllerPhaseSM` (`chat/controller_phase.py`): **`ACTIVE` (default) | `PLAN` (opt-in, sticky)** — `ACTIVE` merges the old `DECIDE`+`EDIT` and needs no permission step to edit; `PLAN` is read-only exploration/discussion until the user picks `propose_mode`'s `"implement"` option (the old `EDIT`/`EXPLAIN` split and the `"edit"`/`"explain"` mode vocabulary are retired — 2026-07-22 merged-phase redesign, spec+plan `docs/superpowers/specs|plans/2026-07-21-merged-decide-edit-phase*`). Per-phase action-type filtering + a tight `oneOf` response schema on providers with `supports_oneof_grammar` (flat fallback otherwise). Edits run in a `TurnEditSession` (ACID shadow, **instant-promote on accept**, restore on reject), built lazily via a factory on first `edit` dispatch (so a plain `ACTIVE` turn that never edits never pays for one). Prompts in `chat/controller_prompts.py`.

- **The composer's sticky "Plan Mode" toggle** (persisted in the VS Code extension's `globalState`, survives across threads/reloads — NOT per-message local state like "Review each step") is the only way to enter `PLAN`; every plain message otherwise starts fresh in `ACTIVE`. `POST /message`'s `plan_mode` field selects `handle_message`'s starting phase (`"PLAN" if plan_mode else "ACTIVE"`). Toggling posts `setPlanMode` to the host, which persists it AND echoes a `planModeState` message back so the (fully-controlled) checkbox prop actually updates in the same session — the round-trip is required, not decorative, since the checkbox has no local state of its own.
- **`propose_mode` is `PLAN`-only** (plus a narrow `ACTIVE` carve-out below) — in `ACTIVE` the model just edits directly, no permission step. Inside `PLAN`, picking the model-authored `"implement"` option (via `POST /mode-decision` or the live ModeGate buttons) exits `PLAN` and re-enters the loop in `ACTIVE` for the rest of that turn, AND auto-flips the sticky toggle off (the ModeGate's own click handler posts `setPlanMode(false)` in the same action — single write path, no separate "auto-flip" code to drift out of sync with the manual toggle). The ModeGate's plan-sketch display is scrollable/expandable (this is what made deleting the old `EXPLAIN` mode safe — its only purpose was working around this card being too shallow to read); its free-text "Chat about this approach…" feedback field re-enters `PLAN` with that feedback (falls back to the host's persisted Plan-Mode value when the message omits `planMode`, since the card itself doesn't track the toggle). (Verified live this session: single-file Three.js game built end-to-end on TQP `qwen3.6:35b-a3b-q4_K_M` through ModeGate→EditGate→promote, pre-redesign under the old edit/explain vocabulary — data flow is otherwise unchanged by the phase-model rename.)
- **Live review preference (chat twin of the task-side item 5 above):** the "Review each edit" checkbox is mutable **mid-turn**, not frozen at turn start. `chat/turn_control.py::ChatTurnControl` (mirrors `orchestrator/task_control.py`) is held in `ChatController._turn_controls: dict[str, ChatTurnControl]`, registered right before `loop.run` and released in the same `finally` as `_active_loops` — so its absence is exactly what makes `set_review_pref` answer "no turn running" (→ 409). `ControllerLoop` re-reads `turn_control.auto_accept_edits` **at every edit dispatch**; the message's `step_review` is only the STARTING value (`run()` still accepts the plain `auto_accept_edits` bool and wraps it in a control when no caller supplies one — same fallback shape as `engine.py`'s `_ctrl.step_review_auto_accept if _ctrl is not None else …`). Three things this required, each a latent bug on its own: (1) `edit_decision_cb` is now passed **unconditionally**, not only in review mode — otherwise a mid-turn flip TO review has no gate to call and silently keeps promoting; (2) `EditRecordCb` gained a trailing **`was_gated`** arg supplied per edit by the loop, replacing the `is_review` frozen into `_edit_record_cb`'s `partial` at turn start — within one turn some edits gate and others don't, and the breadcrumb keys off what actually happened; (3) `POST /review-pref` flipping ON also fires the live `_pending_edit` future as accept, the same consistent-intent rule as `resolve_pending_step_review` (the reverse direction never retro-gates an edit that already promoted). Frontend: `setChatReviewPref` on the editor-client contract, and `controller.ts::setReviewPref` posts to BOTH surfaces (the chat thread and, when one is running, the task) — it previously early-returned on `!activeTaskId`, which with the task subsystem OFF meant the toggle reached nothing at all during a chat turn while the webview still optimistically flipped the box. `extension.ts` now also calls `RuntimeManager.setStepReview`, which had **no caller** — `getStepReview` hydrated the checkbox from `globalState` but nothing ever wrote it, so it reset to the default on every webview reload.
- **Class-A live gates:** a thread holds a LIST of pending gates (`ChatThread.pending_controller_gates`, spec §4.5 of the sub-agents design — sub-agents raise gates concurrently), each with a stable `gate_id` (uuid for controller gates; `task:{task_id}:{kind}` for task-derived gates; never a pydantic `default_factory` — the `ChatMessage.id` lesson) and an optional `agent: {id, label, name}`. Surfaced by `GET /v1/chat/threads/{id}/live` → `{turn_active, pending_gates:[{gate_id, kind: mode|edit|clarify|command|mcp_tool, payload, agent}], status, plan, failure_summary, run_summary, task_narrative}`. Decision futures are keyed by `gate_id`; `/edit-decision`, `/command-decision`, `/mcp-decision` take an optional `gate_id` — unknown → **404**, omitted with several of that kind pending → **409**, omitted with one → that gate (the frontend always sends it and treats 404/409 as benign). Mutations are `add_controller_gate`/`remove_controller_gate`/`clear_controller_gates(agent_id=…)` (sync read-modify-write, no `await`). Same render-from-`/live`, durable-breadcrumb model as the task gates. Turns are detached/durable (`/message`, `/mode-decision` & `/clarify-decision` 409-guard + subscribe-relay; `POST /stop` to halt; live-resume re-subscribes on reload).
- **Clarify gate (sibling of ModeGate):** the `clarify` action is NOT a chat bubble — it renders as a Class-A live gate (`PendingGate(kind="clarify")`). The action schema carries a model-authored `options` array (2-4 candidate answers); the UI (`ClarifyGate.tsx`) appends a free-text "Something else…" escape and disables the composer ("Answer on the card above"). `_finish` routes clarify → `_present_clarify_choice` (sets the gate, persists pills, NO question bubble — the question lives in the card). Resolved by `POST /v1/chat/threads/{id}/clarify-decision {answer}` (streamed, mirrors mode-decision) → `ChatController.resolve_clarify` writes ONE combined `❓ q → a` breadcrumb and re-enters the loop with the answer as the user reply. **Phase-resume:** a clarify raised mid-`PLAN` or mid-`ACTIVE` carries that same `resume_phase` in the gate payload (resolve_clarify reads it to resume into the SAME phase it was raised from) — this replaced the old `_edit_clarify_pending` side-map and, pre-redesign, the EDIT-only `resume_phase="EDIT"` special case. `PendingGate.kind` gained `"clarify"` in BOTH `chat/models.py` AND the editor-client Zod enum (the `.min(1)`-class footgun). Spec+plan: `docs/superpowers/specs|plans/2026-06-26-clarify-interactive-gate*`. (Verified live in-situ: an ambiguous "which tax module?" prompt on TQP produced the gate with options `[src/tax.py, src/taxutil.py]`.)
- **GOTCHA — the controller is workspace-frozen at startup.** `main.py` reads `CRUCIBLE_WORKSPACE_PATH` (default cwd) **once** and passes it into `select_chat_handler`; `ChatController._workspace_path` is used for the shadow root, all file ops, and retrieval **on every turn**. The thread's `workspace_path` column is stored and used for thread *listing* but is **ignored per-turn** — one backend process serves exactly one editing workspace. To test a specific workspace, point both the backend env AND the dev-host's opened folder at the same path. And **always quote `--workspace`** to `start-backend.sh`: an unquoted path with a space corrupts `$WORKSPACE` and every derived var (`DB_PATH`, `SHADOW_ROOT`, `ARTIFACTS_ROOT`, …) to the pre-space prefix.
- **Debug artifacts:** `<workspace>/.crucible/state/artifacts/chat/<thread_id>/<turn_id>/controller-turn-NN.json` (exact per-iteration LLM bytes) + `turn-trace.json` — the controller analog of the task path's `plan-turn`/`tool-trace`.
- **Todo ledger (multi-feature completion):** in `ACTIVE`/`PLAN` the controller can call the `write_todos` tool (a `TodoToolSource` over a per-request `TodoLedger`, `chat/todo_ledger.py` + `chat/todo_source.py`; 5 states pending/in_progress/done/blocked/cancelled, full-list-rewrite) to track a large/multi-part change. `submit_changes` is **hard-blocked** in `ControllerLoop` while any item is pending/in_progress (blocked/cancelled/done never deadlock it; the block is NOT counted as malformed — only `max_iters` bounds it); `answer` carries the same gate whenever an edit has landed THIS turn with items still open (so `ACTIVE`'s inherited `answer` terminal can't bypass the guarantee the way `submit_changes` alone used to enforce it — an unrelated stale ledger from an earlier turn never blocks ordinary Q&A that never touched it). The status is re-surfaced into the payload tail (`todo_status`) every iteration; persisted on `chat_threads.controller_todo_json` (request-scoped — survives `PLAN`→`ACTIVE` + clarify resume; cleared on terminal); exposed via `/live` (`ThreadLiveState.todos`, `ChatThread.controller_todos`) and rendered as a flat read-only `TodoCard` in the live slot (**`todos` MUST be in controller.ts `lastLiveSignature`** or updates are deduped away). Discretionary: model creates it only for multi-part work (steered via the propose_mode "enumerate every part" rule + a TODO LIST POLICY block; `done` requires evidence cited in `note`). `CRUCIBLE_CONTROLLER_MAX_ITERS` (default 500) is the loop cap — the real within-turn limit is the context window until the agent-memory module lands. **No completion backstop yet** (deferred, spec §7); op-deltas/nesting/action-form/manual-approval/event-log deferred (spec §9). Spec+plan: `docs/superpowers/specs|plans/2026-06-23-controller-todo-ledger*`.
- **Task subsystem flag (`CRUCIBLE_TASK_SUBSYSTEM`, default OFF):** gates the entire task-based path. OFF (default): `propose_mode`'s only reachable mode is `"implement"` (`PLAN`-only); `create_task`/`resume` teaching is omitted from the controller system prompt (`format_controller_system_prompt(task_subsystem_enabled=…)` swaps `_PROPOSE_MODE_MODES_{ENABLED,DISABLED}`), the allowed-modes set in `ControllerLoop` is `{"implement"}` (`_propose_mode_correction`), `ChatController.resolve_mode` rejects task modes, and the extension hides the task UI (`startTask`) via the `crucible.taskSubsystemEnabled` `when`-context key fed by `GET /v1/config`. ON: `propose_mode` becomes reachable from `ACTIVE` too (not just `PLAN`), restricted there to `{create_task, resume}` — never `"implement"`, since `ACTIVE` is already the implementing phase (`ControllerLoop._allowed_action_types()`/`_allowed_modes_for_current_phase()`) — so task creation stays reachable from every turn's default phase, not gated behind the user opting into Plan Mode. `/v1/tasks` routes stay registered but **dormant** when OFF (no hard-404 guard). OFF requires `CRUCIBLE_CHAT_CONTROLLER=1` (startup WARNING otherwise — `warn_if_incoherent_flags`). Inline `edit` is the PRIMARY path for changes of ANY size (large via the todo ledger), not a small-change-only path. Resolver: `chat/controller_factory.py::is_task_subsystem_enabled`. Existing `create_task` tests opt in via `monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM","1")` / `task_subsystem_enabled=True`. Deferred: turning the task path into a sub-agent execution path. Spec+plan: `docs/superpowers/specs|plans/2026-06-26-flag-gate-task-subsystem*`.

#### Sub-agents (P5) — all five phases complete on `feat/subagents`

The controller can dispatch parallel child agents that edit the **shared** real workspace. A write guard refuses edits that would overwrite another agent's newer write, and full reports go back to the dispatcher. **Default ON since Phase 5** (`CRUCIBLE_SUBAGENTS_ENABLED`; kill-switch `0/false/no/off`; resolver `chat/controller_factory.py::is_subagents_enabled`). Because the code default is on, it needs no wiring in `start-backend.sh`, `.env` or `buildBackendEnv` (those opt-in sites are only for default-off flags). It is **controller-only** and inert without `CRUCIBLE_CHAT_CONTROLLER`; an *explicit* ON without the controller logs a startup WARNING (`warn_if_incoherent_flags`). OFF means no write log, no read tracking and no `dispatch_agents` — the parent behaves byte-for-byte as before P5.
  - **Tests run with it OFF:** `services/agentd-py/tests/conftest.py` has an autouse fixture setting `CRUCIBLE_SUBAGENTS_ENABLED=0`, so `dispatch_agents` and the SUB-AGENTS block don't leak into unrelated prompts, tool lists and goldens. Sub-agent tests opt in with `monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")`. To test "off", set `"0"` explicitly: `delenv` now means the default, which is ON.
  - **A window reload kills in-flight turns.** On deactivation the extension's `RuntimeManager.dispose()` stops the managed backend, and the next startup reaps the orphaned agents as "failed — backend restarted". Only a *webview* reload (closing and reopening the chat tab) keeps a dispatch running and rehydrates the roster live. Spec rev 11: `docs/superpowers/specs/2026-09-29-subagents-design.md`. Plans: `docs/superpowers/plans/2026-09-30-subagents-1b-gates-approvals.md` and `…/2026-10-01-subagents-2-child-runtime.md`. Diagrams: `.archify/{dataflow-subagents-e2e,sequence-subagents-calls}-*/`.

- **Tagged prompt templates (1A, `agentd/prompting/tagged.py`):** controller prompt blocks carry `<<main>>`/`<<child>>`/`perm:`/`shell:`/`tool:`/`type:` tags. They are rendered against a flat, hashable `RenderContext` — `RenderContext.main()` for the parent, `RenderContext.for_agent(ctx, tools=…, shell_policy=…)` for a child. The main rendering is **byte-identical** to pre-P5, pinned by `tests/goldens/controller_prompt_main.json`, and `tests/test_prompt_leak_lint.py` keeps main-only text out of child prompts. `create_controller_step(..., allowed_types, render_ctx, persona)` is the engine seam. A child runs the `AGENT` phase with types `tool_call|edit|progress|report` (`plan` permission drops `edit`). Its prompt adds `_AGENT_ROLE_BLOCK` plus the definition's persona.
- **Stacked gates + truthful denials (1B):** see "Class-A live gates" above (`pending_controller_gates`, `gate_id`, 404/409) and "Truthful denials" under MCP (`ApprovalOutcome`/`denial_text`). A child's gate carries `agent: {id, label, name}`, and its edit gate payload adds the informational `shadow_key`. A `dontAsk` child never raises a command or MCP gate: anything not allowed by `allow_all` or a remembered rule is `ApprovalOutcome.deny("policy")`.
- **Dispatch (`agentd/subagents/`):** `dispatch_agents` (`SubAgentToolSource`) is offered when the flag is on. Built-ins (`definitions.py::BUILTIN_AGENTS`):
  - `explore` — `plan` permission, read-only tools.
  - `general-purpose` — `default` permission, all tools.

  **Agent definition files (Phase 3, `subagents/agent_files.py`):** Claude Code–compatible Markdown + YAML frontmatter; the body is the persona.
  - **Discovery:** `AgentCatalogLoader` searches `.crucible/agents/` → `.claude/agents/` → `~/.claude/agents/` → built-ins. A name wins at its highest-precedence source, a duplicate within one root keeps the first with a warning, and the catalog is sorted by name.
  - **Recursive `*.md`, with a per-file `(path, mtime)` cache.** A root-mtime cache like `SkillCatalogLoader`'s would miss nested edits, because a directory's mtime only moves when its direct entries change.
  - **No restart needed:** `ChatController._dispatch_source` re-reads the catalog per call, so a new file takes effect on the next turn.
  - **Honored fields:** `name` (`[A-Za-z0-9_.-]`, max 64), `description` (one collapsed line, max 1024), `tools`, `disallowedTools` (applied first), `model`, `permissionMode`, `maxTurns`, `skills`. Everything else is parsed and ignored.
  - **Tool mapping:** Read→`read_file`, Grep→`search_code`, Glob/LS→`list_directory`, Bash→`run_command`, Agent/Task→`dispatch_agents`, TodoWrite→`write_todos`, Skill→`read_skill`. WebFetch/WebSearch are dropped with a warning.
    - Edit/Write/MultiEdit/NotebookEdit map to the pseudo-tool **`edit`**, i.e. the edit *action*: a definition whose `tools` lacks it, or whose `disallowedTools` has it, loses the `edit` type (`definition_allows_edit` → `child_allowed_types(can_edit=)`).
    - `mcp__<server>` normalizes to the wildcard `mcp__<server>__*` (`tool_matches`).
  - **Value mapping:** `bypassPermissions` and unknown modes → `default`. The Claude Code aliases `sonnet|opus|haiku|fable` → `inherit`, with a warning.
  - **`skills:`** pre-seeds each listed skill's capped body into the child's `active_skills` (`_preseed_child_skills`). It needs `CRUCIBLE_SKILLS_ENABLED`; otherwise one warning and nothing loaded.
  - **`subagent-driven-development`** is force-loadable (`_non_executable_subskills()`) only while the sub-agents flag is on.

  Each dispatch runs `ChatController._dispatch` in this order:
  1. Builds an `AgentContext` per request: `effective_permission` (only read-only-ness propagates), `child_allowed_types`, persona, and `max_iters` from `maxTurns` or `CRUCIBLE_SUBAGENT_MAX_ITERS`.
  2. Calls `log.register_agent` (writes before this instant are not stale for the child).
  3. Calls `_on_dispatch_start`, which persists an `agent_dispatch` chat message (rendered as nothing by `MessageRow`) and `chat_agents` rows, and broadcasts `agent_started` — all before any child runs.
  4. Calls `SubAgentRuntime.dispatch`. That uses a process-wide `BoundedSemaphore` (`CRUCIBLE_SUBAGENT_MAX_CONCURRENT`, 8); a dispatching child releases its slot while it waits on its own children. It then runs `gather(return_exceptions=True)`.

  Nesting stops at `CRUCIBLE_SUBAGENT_MAX_DEPTH` (2). The tool result is `format_dispatch_result`: **every report in full, never truncated**, plus `ToolOutput.workspace_changes`.
- **A child (`ChatController._run_child`)** gets its own `ControllerLoop(agent=ctx, phase_sm=ControllerPhaseSM(start="AGENT"))` and its own context window. It has:
  - **Its own state:** an in-memory `TodoLedger` (never written to thread columns), an additive `SkillToolSource`, and memory recall without `remember` and without consolidation (`consolidate=False`, `memory_tool_source(allow_remember=False)`; recall caches keyed per `run_id = {thread}:{agent}`, freed by `release_run`).
  - **Tool limits:** `vcs_refusal` (`ToolRegistry(command_guard=)`) refuses git-mutating commands *before* the approval gate. `CHILD_EXCLUDED_TOOLS` drops the PTY session tools and `remember`. `AggregatingToolRegistry(allowed_tools=)` applies the permission filter.
  - **Review behaviour:** only an effective-`default` child shares the parent's live `ChatTurnControl` ("Review each edit"); other children auto-accept.
  - **Its own stream and record:** a `SequencedBroadcaster` on channel **`chat:{thread}:agent:{agent}`**, which stamps `seq` on every event, and an `AgentTranscript` → `chat_agents.transcript_json`.
  - **Debug artifacts** under `chat/<thread>/<turn>/agents/<agent>/`.

  Its only terminal is **`report`**. Open todos block it (a redirect, not malformed) until the final iteration, where the type set narrows to `["report"]` and an `Unfinished:` list is appended with status `partial`. Any other exit gets `loop.fallback_report(...)`. `_close_child` always clears the child's gates, releases its memory run and clears its channel's replay buffer.
- **Write guard (`subagents/write_log.py`, `chat/edit_session.py`):** one in-memory `WorkspaceWriteLog` per thread (`ChatController._write_log_for`). The parent is `MAIN_AGENT_ID = "main"`, so the main agent is guarded too.
  - **Tracking:** `BuiltinToolSource(read_observer=)` records reads; `accept` records promotes (`note_promote`), keyed by `canonical_path`.
  - **Check 1** runs in `TurnEditSession.apply`. **Check 2** runs in `accept` with **no await between the check and `promote_files`**. Either raises `StaleWriteError(path, writer_label=)` naming the agent that wrote the file after this agent last read it. A stale accept first restores the shadow from real.
  - **Re-seeding:** `_ensure_shadow` re-copies **every touched file from real on every apply**, not only on first touch, so a stale shadow copy can never promote over a newer write.
  - **No-op edits are rejected:** `apply` keeps only files whose bytes actually changed (`_same_bytes`). An all-unchanged batch raises `PatchFailureCode.NO_OP` (e.g. `replace == search`), giving the "your edit changes nothing" guidance with no gate, no promote and no breadcrumb. Found live: such an edit read `✓ Edit accepted`, appeared in `files_changed`, and its recorded promote made a sibling's edit stale.
  - **Shadows:** each child's shadow is `chatturn-{thread}-{agent}`; its edits land in the parent turn's rewind checkpoint.
  - **Refusal record:** the loop records the refusal through `edit_record_cb(diff, "stale", …)` as the breadcrumb `✗ Not applied: …`, appends `PATCH FAILED … STALE_READ` guidance and keeps going.
- **API + frontend (Part E):**
  - `GET /v1/chat/threads/{id}/agents[?turn_id=]` lists summaries.
  - `GET …/agents/{agent_id}` returns the record, transcript and `last_seq` (the highest persisted `seq`, so a viewer backfills and then subscribes without gaps).
  - `POST …/agents/{agent_id}/stop` stops one agent. `runtime.stop_agent` cancels its task; the child still writes a `stopped` fallback report. Stopping the whole turn mid-dispatch hands the parent a synthetic dispatch result built from `_inflight_dispatch`.
  - `/live` gains `agents` (`ChatController.live_agents`) and `/v1/config` gains `subagents_enabled`.
  - Thread-channel events: `agent_started`, `agent_status` and `agent_finished` (from `_on_agent_status`). Everything else stays on the child's channel.
  - editor-client: `AgentSummarySchema`/`AgentDetailSchema`, `listAgents`/`getAgent`/`stopAgent`, and the `agent_dispatch` message type.
- **UI (Phase 4, built to `.superpowers/brainstorm/11903-1790698215/content/subagent-ui-v3.html`; plan `docs/superpowers/plans/2026-10-01-subagents-4-ui.md`):**
  - **Roster card** (`webview-ui/src/components/agents/AgentRosterCard.tsx`) renders from the `agent_dispatch` message. That message is now also **broadcast** at dispatch start: on the thread channel for the main agent, and on the dispatcher's channel *before* it is persisted for a nested dispatch, so the persisted copy carries the event's `seq`. Without the broadcast, a live turn had no anchor until a reload. The webview dedups roster messages by `agent_ids` (`agents.ts::appendDurable`), because a reload replay can deliver the main one twice.
  - **Row data** comes from one `agents` map in webview state. It is merged from `/live` agents during a turn, and from `listAgents` on thread load and on the turn-end edge (`lastTurnActive` true→false in `pollThreadLiveState`). `agents` is in `lastLiveSignature` — the dedup invariant. `AgentRecord.summary()` carries `tool_count` (summed from the transcript's persisted pills) so finished rows keep "N tools".
  - **Open agent views** (▸ inline box, ⤢ floating window with sibling tabs, ■ Stop and Esc) share `AgentTranscript`. The webview posts the full open set (`setOpenAgents`). The host's vscode-free `AgentViewManager` (`src/agent-views.ts`) keeps one subscription per open agent: backfill with `getAgent` → follow `chat:{thread}:agent:{agent}` → skip events with `seq <= lastSeq` → re-backfill when the channel idles out → one final backfill when the agent is terminal (it carries the report).
  - `ChatEventSchema` has `seq`, because Zod strips unknown keys and the top-level `seq` never reached a consumer before. `streamChannel(channelId, signal?)` is cancellable.
  - Gate cards use `AgentChip`. With a pending command or MCP gate during a turn, the composer reads "Answer the card above…" and keeps Stop.
- **GOTCHA — slow startup work must not block `/health`.** The managed spawn stops a backend that isn't healthy within 60 s (`backend-process.ts`), and Starlette serves nothing until every startup handler returns. The newline-capability probe calls the provider (40–60 s on NIM), so every managed backend was killed. Provider-calling, best-effort startup work goes through `agentd/startup.py::in_background`.
- **Settings › Agents (v2 Phase 3, spec §10):** `agentd/api/agents_routes.py` serves `GET /v1/agents` (`{agents, skipped, available_tools}`; each agent carries effective vs declared permission, `trust`, `active`, `shadowed_by`, `warnings`, and for files the exact hashed `content` up to 64 KB), `PUT`/`DELETE /v1/agents/{name}` (writes `.crucible/agents/<name>.md` in Claude Code tool names via `subagents/agent_writer.py` — symlink-safe, confined to `.crucible/agents`, recorded as trusted; a name defined in a `.crucible/agents` subdirectory → 409; `trust` is a reserved name) and `POST`/`DELETE /v1/agents/trust {path, sha256}` (re-hashes the file; a mismatch → 409 "the file changed since you reviewed it"). The workspace is always the backend's own, never the request's. `AgentCatalogLoader.report()` keeps what the loader used to only log: per-definition `warnings` (unknown tools, dropped WebFetch/WebSearch, alias models, `maxTurns` clamped to 200, trust clamps), `skipped` files with a reason, and shadowed/inactive rows. The webview's `AgentsSection` loads this separately from the settings snapshot (`settings/listAgents`; errors stay in the section) and offers New / Edit / Duplicate-to-`.crucible` / Delete / Trust (the dialog shows the file text verbatim and sends its `sha256`).
- **Agent teams — foundations (v2 Phase 4, spec §7, §9):** `CRUCIBLE_TEAMS_ENABLED` (default **ON since Phase 6**; kill-switch `0/false/no/off`; inert without sub-agents, and an *explicit* ON without them logs a startup WARNING; `/v1/config` `teams_enabled`; the test conftest forces it off — opt in with `monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")`, and to test "off" set `"0"`, since `delenv` means the default, ON). Like sub-agents, a default-on flag needs no wiring in `start-backend.sh`, `.env` or `buildBackendEnv`. Limits: `CRUCIBLE_TEAM_MAX_MEMBERS` (6), `CRUCIBLE_TEAM_MAX_LIVE_PER_THREAD` (2), `CRUCIBLE_TEAM_REQUEST_BUDGET_PER_MEMBER` (80), `CRUCIBLE_TEAM_MAX_BUDGET` (1000 — enforced since 5B), `CRUCIBLE_TEAM_MAX_WAKES` (15). `agentd/teams/`: `store.py` (`teams`/`team_members`/`team_posts` on the chat DB connection, `ChatThreadStore.teams`; per-team post `seq`; `delivered_seq` never lowers), `validation.py` (labels, mentions, 8 000-char text, evidence), `service.py` (`TeamService`: board operations, `render_delta`, `status_text`, `brief`, `summary`), `tools.py` (members' `team_*`; the main agent's `create_team`/`post_board`/`team_status`/`adopt_proposal`/`resume_team`/`disband_team`; `adopt_proposal` works on a `DEADLOCKED` team, `resume_team` on a `PAUSED` one, in a user turn). **Coordinator — 5A deliberation (spec v2 §8.1–§8.4; plan `docs/superpowers/plans/2026-10-06-team-coordinator-5a-deliberation.md`):** `teams/state_machine.py` (pure `apply(state, event) → (state, actions)`; events `Kickoff`/`MemberReported`/`RoundEvaluated`/`MainAdopt`/`MainPost`/`Disband`) and `teams/coordinator.py` (`TeamCoordinator`, one per live team in `ChatController._coordinators`; executes actions through the `CoordinatorHost` methods `ChatController` implements). Deliberation runs in rounds: a proposal kickoff starts every member, a post kickoff its mentions (the rest join at round 2); a round's input is the board up to the round's **cutoff seq** (`delta_cutoff`), so a post made mid-round is recorded as `held` and reaches members next round — nothing is pushed mid-round (`_on_leftover` keeps a member's leftovers with `AgentSupervisor.keep`). Next-round activations are scheduled with `call_soon` (`start_member`): the last reporter is still inside its own task. A round ends when every quorum member has its final report: `failed_transient` re-queues after 30 s / 120 s; `failed` or a user ■ leaves the quorum (`member_lost`), and fewer than 2 ends the team `FAILED`; a deadline stop stays in. Per-member deadline `CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC` (900) counts **active** time only (`subagents/runtime.py::active_seconds` — minus gate waits and `METER.peek` limiter waits); at expiry `ControllerLoop.force_final()` narrows to `report`, then a 120 s grace before a `deadline` stop. Stances ride the report too (`stances`/`proposal` fields in the team-member schema only), validated in the loop through `report_check` (`teams/report_fields.py::ReportFields`): an invalid entry is refused (a byte-identical resubmission counts as malformed; exhaustion accepts with invalid entries dropped), a missing expected stance gets one redirect. `teams/adoption.py::evaluate_round` adopts at round end (open, posted before the round, every quorum member's latest stance `agree`; lowest seq wins); round limit → `DEADLOCKED`, where `post_board` runs one more round and `adopt_proposal` adopts. **Implementation (5B, plan `docs/superpowers/plans/2026-10-06-team-coordinator-5b-implementation.md`):** an adopted plan with assignments goes to `AWAITING_APPROVAL` when `approval_gate` is set — a `team_plan` Class-A gate (`PendingGate.team` set, `agent` null, so it never takes the composer or blocks notice turns: filter with `is_main()` / `!g.agent && !g.team`), resolved by `POST /v1/chat/threads/{id}/team-plan-decision {gate_id, decision: approve|feedback|reject, feedback?}` (404 unknown gate, 409 stale card, 422 bad feedback; feedback closes the proposal and runs one more round) — else straight to `IMPLEMENTING`, where each assignee starts with `Your assignment: … Files you own: … Shared files: …`. Check 1 (`TeamProtection` + `teams/scope.py`) refuses edits outside `IMPLEMENTING`, another member's files (`team_message <owner> instead`, a second try adds "already told"), and — with an approval gate — files outside the plan; helpers follow their member's team (`_team_membership_of`). Implementation reports are checked once in the loop (`teams/implementation.py`: `completed` with untouched files or an unanswered DM, `awaiting_peer` with nobody to wait on). `partial`/`failed` raise `member_blocked`; a report with every other member idle raises `stuck` (the reporter is excluded — it is still inside its own task); the third consecutive `stuck`, an exhausted budget (checked at every member/helper iteration top via `iteration_cb`, forcing every running loop to its final iteration; those `partial` reports are *interrupted* and owe their work) or three consecutive final `failed_transient` outcomes pause the team (`PAUSED`, timers cancelled; a transient burst's milestones become `notify` until the user speaks). `resume_team` runs only in a user turn (`turn_kind == "user"`), raises the budget (default per-member × members, capped at `CRUCIBLE_TEAM_MAX_BUDGET`) and restarts whoever owes work. **Review (5C, plan `docs/superpowers/plans/2026-10-07-team-coordinator-5c-review.md`):** the last assignment done opens a **closing proposal** (`system`-authored `proposal`, `payload.closing`, `files_changed`, `cycle`; `teams.closing_proposal_id`/`review_cycles`) that every quorum member verifies in a round (deadline as in deliberation, 25 iterations, no `edit`); a member reporting without a stance is redirected once, then abstains. `teams/review.py` is pure: `evaluate_review` (latest stance per reviewer) and `route_objection` — to the owner of the first cited assigned file, else the objector with a synthetic assignment (files must exist, not protected, the objector can edit, inside the approved plan when gated), else the main agent (`member_blocked`). Fixers run in `IMPLEMENTING` with the framed objection (`assignment.fix`), then the next closing proposal asks only objectors, fixers and owners of files the fix changed; the rest carry their `agree` (`payload.carried`). No objections → `DONE (reviewed)`; nothing routable or past `CRUCIBLE_TEAM_REVIEW_CYCLES` (2) extra closing proposals → `DONE (unresolved objections)`; the `done` milestone names the plan, the closing proposal, abstentions, unresolved objections and files. A plan with no assignments still ends `DONE (adopted)`. A rewind deletes the teams it rewinds past (`TeamStore.delete_teams`, with their notices). Team usage is summed in `_close_child` over members and helpers. Pills of `create_team`/`post_board`/`team_status`/`adopt_proposal`/`resume_team`/`disband_team` get "Open board" links (`TeamLinks`), the way back to an ended team's board. **5B smoke changes:** `awaiting_peer` must name `waiting_on` (redirected once otherwise) and the named member's report wakes the waiter with a system DM; quorum < 2 pauses (`quorum lost`) instead of failing, and `resume_team` puts every member back in the quorum; a team member's loop exhausted by unparseable output (`ControllerLoopExhausted`) reports `failed_transient` (re-queued); a main-agent `post_board` to a `FAILED` team revives it in the phase it failed from (`_revive_team`, post lands first); `reap_subagents` drops reaped teams' coordinators; the main agent's edits to files in a live team's plan are refused at Check 1 (`MainProtection(team_rule=_main_team_rule)`); the board header is phase-aware (live delivery while implementing; a round with no open proposal asks for a revised one); a valid but unavailable action type gets its own correction (`_unavailable_type_correction`) instead of "empty or no valid type". Milestones (`adopted`, `deadlock`, `member_lost`, `ended`) are wake notices with `source_kind "team"` (compact bodies, `teams/milestones.py`); trace `chat/<thread>/<created_turn>/teams/<team>/coordinator.jsonl`. Members lose the `edit` type outside `IMPLEMENTING` and are capped at 40 iterations in deliberation. Activity gains `round_started`/`round_ended`/`held`/`requeued`/`deadline`; `/live` teams gain `round_progress`; rewind 409s while a team has not ended. Board: round strips, verdicts, held footers, the adopted card (`RoundItems.tsx`); member chapters titled `Round n`. While a team is live, `TeamStrip` pins one line per team above the composer (round progress or phase, members, "Open board of <name>") so the board never needs a scroll back to the card. A report's `stances` entry that repeats the member's stance on that proposal this round is skipped (members often state it with `team_object` and again on the report). A message sent during a **notice turn** shows a "Queued — the agent picks this up at its next step" tag (`markQueued` → webview `queuedIds`, cleared when `/live` `turn_kind` stops being `notice`). Live-smoke gotchas: after a rebuild run **Developer: Reload Webviews** (the webview bundle is cached), and a host change needs **Reload Window** (which restarts the managed backend and reaps live teams); NIM can hold a stream open with no tokens until the 600 s stream deadline, delaying a notice turn by ~10 min. Members are depth-1 rows with `team_id` set and `dispatcher_id` null; they keep `awaiting_peer` (`ControllerLoop(report_statuses=TEAM_REPORT_STATUSES)` + `controller_response_schema(team_member=True)` — without both, the loop collapsed it to `completed`); their `team_*` tools survive any definition `tools:` filter (`MEMBER_TOOL_NAMES`); goal + roster ride the system prompt via `RenderContext.team_brief` and the `<<team>>` tag (`_TEAM_BLOCK`, after the persona; golden `tests/goldens/controller_prompt_teams.json`); phase/stances/unread ride the payload tail as `team_status` every iteration (`ControllerLoop.run(status_tail=)`). Routes `GET /chat/threads/{id}/teams`, `GET …/teams/{team_id}`, `POST …/teams/{team_id}/disband`; `/live` `teams` (slow fields only — in `lastLiveSignature`); team channel `chat:{thread}:team:{team}` (`team_post` with the post's `seq`, `team_phase`). Host `TeamViewManager` (`src/team-views.ts`); webview `TeamCard` + `TeamWindow` (Board tab + member tabs, Messages toggle, inline-confirm Disband). GOTCHAs: `/live` lists live teams only, so the controller reloads summaries when a team **leaves** `/live` (else an ended team's card keeps its last live phase); a follow loop that re-backfills without a timer delay starves the event loop (`TeamViewManager` awaits `delay()` after every stream end). **Activity journey (spec `docs/superpowers/specs/2026-10-05-team-activity-ui-design.md`):** a `team_activity` table with its own per-team `aseq` (independent of post `seq`, never rendered into member input) records `phase`/`woke`/`notified`/`took_up`/`picked_up`/`wrapped_up`/`capped`, written best-effort through `TeamService.record` from `_create_team`, `_wake_member`, `_on_leftover`, `_activate`, `_drain_member`, `_team_member_reported`, `disband_team` and `reap_subagents` — it replaces the `"<label> finished"` system posts. It streams as `team_activity` (with `aseq`) on the team channel; `getTeam` returns `activity` + `last_aseq`; `/live` teams add `members[].last`, `latest` and `counts`. Webview: pure builders `teamJourney.ts` (board items — a caused wake folds into its post's footer, a wake whose cause is hidden becomes a standalone beat) and `teamChapters.ts` (member chapters cut at transcript dividers numbered by `metadata.activation`, joined to activity), plus `teamIdentity.ts` (palette by roster order; the avatar initial is drawn by CSS so it never enters surrounding text).
- **Lifecycle:** children never survive a restart. `reap_subagents` (startup hook in `main.py`) fails orphaned rows, removes child gates with a breadcrumb, and deletes `chatturn-*-agent-*` shadows. Rewind (`forget_rewound_agents`) deletes the rewound turns' children, their memory segments/anchors (`MemoryStore.delete_segments`, `harness.forget_run`) and **resets** the thread's write log. The response gains `removed_agents`, and the preview counts child commands.
- **GOTCHAs found while implementing:**
  - `StaleWriteError` is a `PatchPreflightFailed` → `RuntimeError`, so the loop must catch it **narrowly and first**. Any broader `except` swallows it and the refusal silently becomes a generic failure.
  - What the engine receives is `RenderContext.for_agent(...)`, never the loop's `agent=` kwarg.
  - A fake engine whose `create_controller_step` lacks `render_ctx` is still called (the `accepts_kwarg` seam) and **silently behaves as the main agent**. `ScriptedReasoningEngine` keys its `agent_scripts` on `render_ctx.agent_label`.

#### Project instructions (AGENTS.md) + prompt files (P1, copilot-parity roadmap)

Two independent, flag-gated parity features. Spec/plan: `docs/superpowers/specs/2026-06-29-project-instructions-prompt-files-design.md` + `…/plans/2026-06-29-project-instructions-prompt-files.md`.

- **Project instructions (backend, controller-only):** `agentd/instructions/loader.py::ProjectInstructionsLoader` is an mtime-cached reader for `<workspace>/AGENTS.md` (mirrors the GraphWalker cache discipline — cheap NOOP until the file's mtime changes, so an edit **self-updates mid-session without a restart**; thread-safe; best-effort — any IO error degrades to `None` so instructions never break a turn; size-capped). `DefaultReasoningEngine` takes an optional `project_instructions_loader` and, in `create_controller_step`, resolves `loader.load()` and passes it to `format_controller_system_prompt(..., project_instructions=…)`, which appends a labeled `_INSTRUCTIONS_BLOCK_TEMPLATE` (uses `.replace`, **not** `.format` — AGENTS.md may contain literal `{ }`). `controller_factory.select_chat_handler` builds the loader from the **frozen** `workspace_path` when `is_project_instructions_enabled()`. **AGENTS.md is the ONLY source** (no `.github/copilot-instructions.md` fallback, no nested files); injection is **controller-only** — the planning/task prompt path is untouched. Env: `CRUCIBLE_PROJECT_INSTRUCTIONS` (default **ON**, kill-switch — off only for `0/false/no/off`) + `CRUCIBLE_INSTRUCTIONS_MAX_CHARS` (default `16000`; over-budget truncates with a marker + `logger.warning`).
- **Prompt files (frontend-only, expand-before-send):** `.crucible/prompts/<name>.md` snippets expanded inline in the composer via `/name [args]`. **No backend route, no editor-client contract change.** Pure helpers in `apps/vscode-extension/src/prompt-files.ts` (`substitutePrompt` — `$ARGUMENTS` = full arg string, `$1..$N` = whitespace-split positional, unfilled → empty; `parseSlashCommand`; `listPromptNames`; `loadPromptBody` — rejects path-traversal names). `controller.ts` (stays vscode-free; node `fs` ok) exposes `listPrompts()`/`expandPrompt(name,args)→{found,text}` over `<ws>/.crucible/prompts`. Host plumbing in `chat-panel.ts` routes `listPrompts`/`expandPrompt` webview messages → posts `promptList`/`promptExpanded` (handlers wired in `extension.ts`). `InputArea.tsx` intercepts an un-expanded `/name` on Enter: it posts `expandPrompt` instead of sending; the `promptExpanded` reply fills the draft so the user reviews/edits, then a second Enter (now non-slash) really sends (`found=false` is a soft no-op). The webview keeps a local mirror `webview-ui/src/slash.ts` of `parseSlashCommand` (it's a separate Vite bundle that doesn't import the extension's `src/`).

#### Agent Skills (P2, copilot-parity roadmap)

agentskills.io `SKILL.md` skills: an always-on catalog + model-driven progressive disclosure. Flag-gated, **default OFF** (`CRUCIBLE_SKILLS_ENABLED`), **controller-only**. Spec/plan: `docs/superpowers/specs/2026-06-30-agent-skills-design.md` + `…/plans/2026-06-30-agent-skills.md`. Grounded in a survey of Claude Code/Codex/opencode impls (all implement the same open standard).

- **Discovery + parse (`agentd/skills/loader.py::SkillCatalogLoader`):** mtime-cached scan of `<workspace>/.crucible/skills/*/SKILL.md` (single dir in v1; ecosystem dirs like `.claude/skills`/`~/.agents/skills` are a trivial later add — extend the dir list). Mirrors `instructions/loader.py` cache discipline (a skill add self-updates next turn, no restart; best-effort — a malformed skill is skipped with a `logger.warning`, never raises). Parses standard frontmatter: required `name` (≤64) + `description` (≤1024); `name`≠folder warns but keeps; `license`/`compatibility`/`metadata`/`allowed-tools` parsed-and-ignored (forward-compat). Needs PyYAML (now a hard dep). Returns `list[SkillManifest]{name,description,body_path,dir}` (body read lazily).
- **Catalog injection (`controller_prompts.py`):** `_SKILLS_BLOCK_HEADER` + a rendered `- name: description` list appended to the controller system prompt (cache-stable, mtime-driven — like `_MEMORY_BLOCK`/`_INSTRUCTIONS_BLOCK`). `DefaultReasoningEngine` takes `skill_catalog_loader`; `create_controller_step` resolves the catalog through a **char-budget guard** (`select_catalog_for_budget`, `CRUCIBLE_SKILLS_CATALOG_MAX_CHARS` default 16000 — order-truncation, query-independent ⇒ stays cache-stable) and passes it to `format_controller_system_prompt(..., skills_catalog=…)`. `controller_factory.select_chat_handler` builds the loader from the **frozen** `workspace_path` when `is_skills_enabled()`. The teaching block shows a worked `run_command` example for bundled scripts — **scripts run via the existing `run_command` shell-policy gate** (no dedicated script tool). **Scale path (built + tested, NOT wired live):** `rank_skills_by_relevance(manifests, query, embedder)` (reuses the memory `Embedder`) is the primitive for over-budget query-ranked catalogs; wiring it (→ dynamic tail) is deferred until catalog size demands it.
- **Activation (`agentd/skills/tool_source.py::SkillToolSource`):** one read-only tool **`read_skill(name)`** — resolves name→`SKILL.md`, caps the body (`CRUCIBLE_SKILLS_BODY_MAX_CHARS` default 20000), writes it into the **shared `active_skills` dict**, returns it. Registered in `ChatController._build_registry` (one `sources.append(...)` when `is_skills_enabled()`). The activated bodies ride the **dynamic payload tail** (`build_controller_step_payload` → `active_skills`, alongside `recalled_memories`) and are **re-injected every iteration** by `ControllerLoop` (compaction-resilient — the analog of opencode's `synthetic`/`noReply`). The `active_skills` dict is **created in `ChatController._run_loop`** (alongside `ledger`) and passed to BOTH `_build_registry` and `ControllerLoop` (same object). Turn-scoped in v1 (cross-turn persistence deferred).
- **`/skill` forced-load (deterministic explicit invocation):** `GET /v1/skills?workspace=` lists the catalog (gated-empty when off); `/v1/config` gained `skills_enabled`. The chat-message body accepts `forced_skills: list[str]` (route → `handle_message(forced_skills=…)` → `_run_loop` seeds `active_skills` from the catalog **before iteration 1**, so the body is active without the model choosing `read_skill`). Frontend: editor-client `sendChatMessage(..., {forcedSkills})` + `listSkills()` + `SkillSummary`/`skillsEnabled` Zod. The composer (`InputArea.tsx`) lazily fetches the catalog on the first `/` keystroke (`listSkills`→`skillList`); on an un-matched `/name` (prompt-file miss — **prompt file wins on name collision**, the host resolves prompts first), `resolveSkillCommand` (in `webview-ui/src/slash.ts`) sends the args as the message tagged with `forced_skills=[name]`. `crucible.skillsEnabled` `when`-context from `/v1/config` (mirrors `memoryEnabled`). The full `/`-autocomplete dropdown (prompts+skills with badges), originally deferred here to P4, shipped 2026-07-06 — see "Composer intelligence + memory inspector polish (P4-C tail)" below.

#### MCP client (P3, copilot-parity roadmap)

External MCP tool servers (stdio + HTTP/SSE) connected from `<workspace>/.crucible/mcp.json`,
tools callable as `mcp__<server>__<tool>` behind a live `"mcp_tool"` approval gate. Flag-gated,
**default OFF** (`CRUCIBLE_MCP_ENABLED`), **controller-only**. Spec/plan:
`docs/superpowers/specs/2026-07-02-mcp-client-github-integration-design.md` +
`…/plans/2026-07-02-mcp-client-github-integration.md`.

- **Config (`agentd/mcp/config.py::McpConfigLoader`):** mtime-cached `.crucible/mcp.json`
  (`{"mcpServers": {name: {command/args/env | type+url/headers, "enabled": true}}}`). An entry
  connects ONLY with explicit `"enabled": true` (allowlist beyond presence). `${VAR}` in
  env/headers resolves against the process environment at connect time; a missing var fails
  that server's connect with a message naming it. Malformed file/entry → skipped with a
  warning, never a crash. Server names: `[A-Za-z0-9][A-Za-z0-9_-]*`, no `__`.
- **Connections (`agentd/mcp/client.py::McpConnectionManager`):** official `mcp` SDK (v1 API,
  pinned `<2`). One background asyncio task per server owns the transport+session context
  managers (anyio cancel scopes are task-pinned) and parks on a stop event. **Connects at app
  startup via `app.add_event_handler("startup", manager.start)`** — NOT at factory time
  (module-level, no event loop). `reconcile(configs)` is the P4 settings-UI seam; per-server
  `McpServerStatus` is queryable. Failed server = zero tools + warning (degrade-not-raise).
- **Tools (`agentd/mcp/tool_source.py::McpToolSource`):** dynamic `definitions()` from
  `list_tools()`, namespaced `mcp__<server>__<tool>`; schemas ride `tools_json` via the
  existing `AggregatingToolRegistry` seam. Budget: `CRUCIBLE_MCP_TOOLS_MAX_CHARS` (default
  16000, order-truncation). Results: text blocks flattened; non-text counted-not-rendered;
  `isError` → `ToolOutput(is_error=True)` — the loop adapts, never crashes.
- **Gate:** every call raises `PendingGate(kind="mcp_tool", payload={server, tool, args})`
  (Class-A: renders from `/live`, survives reload; `mcp_approval_requested` SSE is only the
  instant-render poke). Resolved by `POST /v1/chat/threads/{id}/mcp-decision`
  `{approve, remember}`; remember persists the exact `(server, tool)` pair to
  `.crucible/approved-mcp-tools.json` (`McpRuleStore`) — auto-approves next time.
  `CRUCIBLE_MCP_DECISION_TIMEOUT_SEC` (default 0 = wait forever; timeout → reject).
  **`PendingGate.kind` gained `"mcp_tool"` in BOTH `chat/models.py` AND the editor-client
  Zod enum** (the `.min(1)`-class footgun) **AND webview `types.ts`**.
- **Truthful denials:** command and MCP approval callbacks return the server-internal `ApprovalOutcome{approved, denied_by: user|policy|timeout, decision}` (`domain/models.py`; never a request body, so a client cannot claim `policy`). `tools/approvals.py::denial_text` words each case — the user-rejection strings are unchanged; a timeout says "No decision arrived in time; … was not run."
- **Prompt:** `_MCP_BLOCK` teaching block auto-appends when any tool def name starts with
  `mcp__` (detected from `tool_definitions` — no loader param). Teaches: external/side-
  effecting + the approval pause is expected, not an error.
- **Env:** `CRUCIBLE_MCP_ENABLED` (off) · `CRUCIBLE_MCP_DECISION_TIMEOUT_SEC` (0) ·
  `CRUCIBLE_MCP_TOOLS_MAX_CHARS` (16000) · `CRUCIBLE_MCP_CONNECT_TIMEOUT_SEC` (30) ·
  `CRUCIBLE_MCP_CALL_TIMEOUT_SEC` (120). `/v1/config` exposes `mcp_enabled`.
- **GitHub:** proof-via-user-config (no bundled entry): an `mcp.json` entry for the official
  GitHub MCP server with a `${GITHUB_PAT}` header, verified live end-to-end.
- **Shipped web-search default (2026-07-02):** `resources/mcp-servers/ollama-web-search.py`
  (vendored first-party `ollama-python` example, PEP-723 deps, run via `uv run`) exposes
  `web_search`/`web_fetch` from Ollama's hosted API. Canonical `.crucible/mcp.json` entry —
  the P4 installer will write it as a default:
  `"web": {"command": "uv", "args": ["run", "<repo>/resources/mcp-servers/ollama-web-search.py"], "env": {"OLLAMA_API_KEY": "${OLLAMA_API_KEY}"}, "enabled": true}`.
  Key: free, https://ollama.com/settings/keys, exported in the backend env. Missing key →
  that server's connect fails naming the var; everything else unaffected. Provider swaps are
  config (community SearXNG/Tavily/Brave MCP servers), not code. Spec/plan:
  `docs/superpowers/specs|plans/2026-07-02-doc-write-tool-web-search-defaults*`.

#### Chat rewind

Rewind a thread to an earlier user message: restores the files the agent touched,
truncates the transcript + controller history, retires the memories those turns wrote.
Spec/plan: `docs/superpowers/specs|plans/2026-08-16-chat-rewind*`.

- **Capture is copy-on-first-write at the `TurnEditSession.apply()` seam**
  (`chat/rewind.py`), BEFORE `_ensure_shadow` — the real workspace is the clean
  before-state there (the `shadow == real` invariant in `edit_session.py`'s docstring).
  Chat edits are instant-promoted and the turn shadow is `rmtree`'d at `close()`, so
  this is the only moment pre-edit content exists. Reverse-applying the persisted
  `unified_diff` does NOT work as an alternative: `patch/diffing.py` caps diffs at 400
  lines / 24k chars, so it would restore large files wrong.
- **One checkpoint per turn**, keyed on the user message that started it
  (`ChatMessage.id`). Continuations (`resolve_mode` → implement, `resolve_clarify`) fold
  into the thread's HIGHEST seq — same anchor, no open/closed bookkeeping. No checkpoint
  → capture is a silent no-op (the `_promote_orphaned_edit` restart-recovery path).
- **`ChatMessage.id` is `str | None`, NOT a `default_factory`** — `model_validate` runs
  on raw dicts from `messages_json`, so a factory would mint a fresh id per read and
  every rewind anchor would 404 differently each poll. Legacy messages carry None and
  offer no affordance. Mirrored in editor-client Zod + webview `types.ts` (the
  `PendingGate.kind` footgun class). **`HttpBackendClient.getChatThread` maps message
  fields EXPLICITLY**, so `id` had to be added there too — the schema alone would have
  dropped it silently.
- **Restore folds the span first-seen-wins** — the OLDEST pre-state per path is the
  right one, which is what makes multi-turn rewind correct without full snapshots.
  Per-file failures are collected and reported, never aborted on.
- **The four `controller_*` blobs are snapshotted whole, not truncated.**
  `controller_history_json` has no alignment to transcript messages, so truncating it to
  match has no correct implementation; a verbatim earlier copy sidesteps it and picks up
  todos/active skill/pinned seed for free.
- **Memory:** `retire_since(source_ref, cutoff)` keys on `source_ref` = the controller's
  `run_id` = the `thread_id`, catching thread- AND workspace-scoped memories with no
  schema change (`RecallEngine` already filters `valid_to IS NULL`). The compaction
  anchor is snapshotted per checkpoint and restored — a **null snapshot deletes** the
  anchor, since leaving a newer one keeps summarizing turns that no longer exist. The
  route reads the anchor BEFORE `restore()`, which deletes the checkpoint row. Stale
  `compaction_segments` are accepted (downstream of the restored anchor). `remember()`
  now threads `run_id` so explicit memories are rewindable too. Best-effort throughout:
  memory never fails a rewind.
- **Refusals:** 409 while a turn is in flight; 409 when the span holds a non-terminal
  task (it holds its own shadow and promotes on completion, re-writing what was just
  reverted). Exec sessions are deliberately NOT blocking — background shells, left
  running and named in the confirm dialog. Commands already run are not undone, stated
  in the dialog.
- Env: `CRUCIBLE_REWIND_RETENTION_TURNS` (50) · `CRUCIBLE_REWIND_MAX_FILE_BYTES` (10000000).

#### write_doc — REMOVED (2026-07-16)

The one-shot `write_doc(path, content)` tool (single-call, full-file-content-only,
no append/partial-write) was removed entirely — every doc/plan/data write now goes
through the same `propose_mode → edit` flow as source code (`create_file` for a new
file, `search_replace`/`apply_diff` to grow or modify one incrementally). Root cause:
the single-call-with-full-content contract had no way to split a large multi-section
document (e.g. a multi-phase implementation plan) across multiple calls, which
reliably broke weaker/slower models (TurboQuant malformed-JSON truncation, Gemini
empty-args repetition attractor — both observed live on the same real plan-writing
turn). The original spec/plan docs (`docs/superpowers/specs|plans/2026-07-02-doc-write-tool-web-search-defaults*`)
are left as historical record; the tool, its `PendingGate(kind="doc_write")` gate,
`/doc-decision` route, `CRUCIBLE_DOC_WRITE_ENABLED`/`CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC`
env vars, and the `DocWriteGate` webview component no longer exist.

#### P4 — Install, managed runtime & settings UI (copilot-parity roadmap)

A new user goes from zero → working chat turn via a VSIX install + first-run wizard:
the extension provisions the whole runtime (uv-managed agentd, indexer, ripgrep,
LSPs), spawns/supervises the backend per workspace, and a settings webview
round-trips provider/MCP/skills/policy config. Spec/plan:
`docs/superpowers/specs|plans/2026-07-02-p4-install-runtime-settings*`.

- **Provider factory + validate + hot-swap (backend):** `agentd/providers/factory.py`
  (`build_transport(backend, credentials=None)`, `resolve_model`, `default_model`,
  `PROVIDER_KEY_ENV`) is the one place a `(backend, credentials)` pair becomes a
  transport — used at app startup, by validate, and by hot-swap; request-supplied
  credentials override process env and are held only in the transport object
  (never persisted/logged). `POST /v1/providers/validate` (`agentd/providers/validate.py`)
  pings a provider (`generate_text` with a 30s timeout) and always returns
  `200 {"ok": bool, "model"?, "error"?, "json_mode"?, "warning"?}` — never a 500, so the
  wizard/panel can render the provider's own error message verbatim. `json_mode` reports
  the transport's post-probe JSON mode — `"strict"` or `"json_object"` (downgraded), and is
  **omitted entirely** when the probe was inconclusive (a transient blip prevented the check)
  or when the backend isn't probed at all. `warning` is the non-fatal note the UI renders in
  amber next to a *successful* validate. So there are four shapes, and `json_mode` absent +
  `warning` present means "couldn't tell", NOT "downgraded". A **failed** validate must clear any
  earlier `warning` from the panel state — it describes a result no longer in effect
  (`settings-data.ts` posts a refreshed `settings/state` **before** the `settings/error`,
  since `SettingsApp` clears its error banner on every state message). `PUT /v1/config/provider`
  (`agentd/providers/runtime.py::ProviderRuntime.swap`) validates first, then mutates
  every live `DefaultReasoningEngine` (orchestrator + chat controller) in place —
  applies **from the next turn**, no restart. Known v1 limitation: the memory-harness
  summarizer keeps its construction-time transport until process restart. `GET /v1/config`
  reports `provider: {backend, model} | null`.
- **Startup lockfile:** `python -m agentd.serve --workspace-lock <ws>` (managed spawns only)
  writes `<workspace>/.crucible/state/agentd.lock` (JSON `{pid, port, started_at}`, atomic and
  symlink-safe via `runtime_lock.write_lock`). It is never deleted at shutdown: it is a hint.
  The extension reuses the backend it names only when `/health`'s proof shows that pid serving
  this workspace (see "Backend authentication"); otherwise it reaps the lock (killing only a
  verified process of ours) and spawns — one workspace, one backend.
- **MCP admin routes:** `agentd/mcp/admin.py` (`upsert_server`/`remove_server`/`read_raw_servers`,
  read-modify-write over `.crucible/mcp.json`, preserves unknown keys, `${VAR}` refs stay
  verbatim) + `McpConnectionManager.reconcile(configs, disabled=frozenset())` /
  `.reconnect(name, disabled=...)`. Routes (soft-gated: no manager → `GET` returns
  `{"enabled": false, "servers": []}`, writes 409): `GET /v1/mcp/servers`,
  `PUT /v1/mcp/servers/{name}` (upsert), `DELETE /v1/mcp/servers/{name}`,
  `POST /v1/mcp/servers/{name}/reconnect`. Enable/disable is **user-local**
  (extension `globalState`, passed as `disabled` on every reconcile-triggering call) —
  the shareable `mcp.json` file is never touched by a toggle.
- **`CRUCIBLE_SKILLS_DISABLED`** (comma-separated skill names): `SkillCatalogLoader.load_catalog()`
  filters these out after the mtime-cached scan (so every consumer — controller prompt,
  `read_skill`, `/v1/skills`, forced-load — sees the same filtered set). Read per call, not
  cached with the mtime signature.
- **Extension runtime core (vscode-free, `apps/vscode-extension/src/runtime/`):**
  `manifest.ts` (`RuntimeManifest`/`PlatformKey` types, `platformKey()`, `sha256Hex`/`verifyChecksum`) →
  `installer.ts` (`RuntimeInstaller.installAll()`: uv → agentd (venv + `uv pip install`) →
  indexer → ripgrep → rust-analyzer → gopls → jre → jdtls → lsps (pyright +
  typescript-language-server via npm), in dependency order; resume via `install-state.json`; a
  failed component marks dependents `failed` without aborting independent ones; missing Node.js
  degrades `lsps` to `skipped` with a "code-graph edges degraded" detail, never a hard failure).
  **gopls/jre/jdtls all added 2026-07-08, fully wired end-to-end (client install + release
  staging + live-verified, not just unit-tested):**
  - **`gopls`** is a plain binary component (identical shape to `rust-analyzer` — downloaded +
    checksummed straight into `bin/`). Release side: `scripts/release/build_gopls.py`
    cross-compiles it for all 4 platforms from a single Linux runner (Go's `GOOS`/`GOARCH`
    needs no target-OS host — verified locally) via a two-step `go get` (resolve, in a
    throwaway module) then `go build` (cross-compile) — `go install` itself **refuses**
    cross-compilation once `GOBIN` is set, and `go build` doesn't accept the `pkg@version`
    remote syntax at all, so neither alone works.
  - **`jre` and `jdtls` are directory-tree archives** (bin/lib/ and a jar tree, respectively) —
    the first components here that aren't a single self-contained binary. `installer.ts` gained
    a new `extract(archive, destDir, format)` dependency (real impl: `archive.ts`, using the
    `tar`/`extract-zip` npm packages) alongside the existing `download`/`exec` DI seam.
    `jdtls` ships as **one platform-independent archive** under the manifest's `"any"` key (same
    shape as agentd's wheel) — its OS/arch-specific bits are subdirectories inside, selected at
    spawn time, not separate builds. **jdtls requires Java 21+** (enforced by its own launcher;
    verified against a real installed jdtls) — the bundled JRE is Temurin 21, not whatever LTS
    is newest at the time.
  - **`apps/vscode-extension/src/runtime/jdtls.ts`** (vscode-free) is the reference-launcher
    port that turns an extracted JRE + jdtls tree into `CRUCIBLE_LSP_JAVA_CMD`: `configDirForPlatform`
    picks `config_mac_arm`/`config_mac`/`config_linux`/`config_win` (recent jdtls builds split
    mac/linux configs by arch — verified against a real downloaded archive; win32 doesn't split),
    falling back to the arch-generic dir for older jdtls builds that don't have the split;
    `findEquinoxLauncher` globs `plugins/org.eclipse.equinox.launcher_*.jar` (the underscore
    immediately after "launcher" excludes the platform-specific native *fragment* jars, which
    have a `.` there instead — e.g. `...launcher.cocoa.macosx.aarch64_*.jar`); `findJavaExecutable`
    recursively searches for `bin/java[.exe]` rather than assuming a fixed nested path (Adoptium
    archives nest everything under a version-specific top-level dir that changes every patch);
    `jdtlsDataDir` hashes the **full** workspace path (sha1), deliberately fixing a real bug in
    jdtls's own reference launcher, which hashes only `basename(cwd)` and so collides for two
    differently-located projects that happen to share a folder name.
  - `backend-process.ts` wires `CRUCIBLE_LSP_GO_CMD`/`CRUCIBLE_LSP_JAVA_CMD` to the managed
    paths when present, falling back to a bare `gopls`/`jdtls` PATH lookup otherwise (same
    graceful-degradation pattern as rust-analyzer).
  - Release staging: `scripts/release/fetch_jre.py` (per-platform Temurin download, real URLs/
    checksums resolved live against `api.adoptium.net` before pinning) and `fetch_jdtls.py`
    (single pinned Eclipse snapshot download) restage under `jre-<platform>.tar.gz`/`.zip` and
    `jdtls.tar.gz` — **no extraction happens release-side**, unlike every prior fetch script;
    the archives ship as-is and `installer.ts` extracts them client-side, since nothing needs
    the contents on the release runner. `make_manifest.py` gained matching build logic (jre:
    per-platform with format-dependent extension, doesn't fit the existing single-exe-suffix
    binary convention; jdtls: single `"any"`-keyed archive, same shape as agentd's wheel).
  - **Live-verified beyond unit tests**, end to end, before ever tagging a real release: a local
    `python -m http.server` served real locally-built/downloaded gopls/jre/jdtls artifacts; the
    actual `RuntimeInstaller.installAll()` (not a reimplementation) downloaded, checksum-verified,
    and extracted all three for real; `findJavaExecutable`/`findEquinoxLauncher`/
    `configDirForPlatform` all resolved correctly against the real extracted trees (including
    macOS's `Contents/Home/bin/java` nesting); the constructed `CRUCIBLE_LSP_JAVA_CMD` was
    actually spawned and the resulting jdtls process answered a live LSP `initialize` handshake
    in ~2s. →
  `backend-process.ts` (`BackendProcess.start()`: reuse a live locked backend via the lockfile,
  else reap-and-spawn `venvPython -m agentd.serve --port 0 --workspace-lock <ws>` with
  `buildBackendEnv(...)`, take the port from the child's `CRUCIBLE_SERVE {…}` stdout line,
  poll `/health` (proof-verified) up to 60s, pre-warm the index via
  `POST /v1/index/build`, then spawn the indexer watcher). `runtime/vscode-runtime.ts::RuntimeManager`
  is the thin vscode wiring on top: install root `~/.crucible/runtime`, provider
  backend/model in `globalState` + key in `SecretStorage`, per-workspace `BackendProcess`
  instances, status bar (`$(rocket) starting…` → `✓ :<port>` → `$(error) failed`), **crash
  backoff** (2s/4s/8s, max 3 attempts, counter resets after 5 min healthy), and an
  **upgrade prompt** when the bundled `resources/runtime-manifest.json`'s `releaseTag`
  differs from the installed one.
- **First-run setup wizard** (`src/setup-data.ts` + `webview-ui/src/setup/SetupApp.tsx` +
  `src/setup-panel.ts`, command `crucible.runSetup`): four-step flow
  welcome → install (per-component progress + Retry) → provider picker (9 providers,
  defaults mirroring `factory.py::_DEFAULT_MODEL`) → done. `saveAndStart` order is
  install → start backend → validate-via-route → report, because cloud provider
  validation is impossible before a backend exists to proxy the ping.
- **Settings panel** (`src/settings-data.ts` + `webview-ui/src/settings/SettingsApp.tsx` +
  `src/settings-panel.ts`, command `crucible.openSettingsPanel`, also the status-bar
  command): one `createSettingsHandler` rebuilds a full `SettingsState` snapshot after
  every action. **Provider** section hot-swaps (validate → store secret → `PUT
  /v1/config/provider`; failure aborts before calling setProvider) and persists the
  choice to `globalState` for the next managed restart (`RuntimeManager.saveProvider`,
  called from the panel's `setProvider` wrapper — separate from `storeProviderKey`,
  which the panel's `storeSecret` dep uses for the SecretStorage-only write).
  The Provider section also carries the **Context window** field (Part 2 of the
  exact-context-accounting spec): a DECLARED token count, pre-filled from a small
  substring-keyed starter table (`webview-ui/src/settings/contextWindows.ts`,
  default 128000) and never detected — NIM's `/v1/models` exposes no capability
  data and NIM accepts a 600k-token prompt with HTTP 200. It rides `setProvider`
  to `PUT /v1/config/provider` as `context_window`, which `ProviderRuntime` applies
  to every registered window sink — the task-loop harness, plus the chat
  controller's own when it has a distinct one — after validation
  succeeds; absent means "leave it alone", so a composer model-only swap never
  clears it. The opt-in **Test** button (`POST /v1/providers/context-test`,
  `agentd/providers/context_probe.py`) sends one prompt of the declared size with a
  passphrase at the FRONT and judges by recall, **not** by HTTP status — the
  measured NIM failure is HTTP 200 with `completion_tokens: 1` and empty content,
  so `ok` (the call completed) and `recalled` (the window is real) are separate
  fields all the way to the UI. `CRUCIBLE_CONTEXT_TEST_TIMEOUT_SEC` (default 240 — see
  "Key Configuration" for why it sits 60s under undici's default headersTimeout).
  **MCP servers** section: list/add/remove/reconnect/enable-toggle (toggle both updates
  the user-local disabled list AND calls `reconnectMcpServer` — no restart). **Skills**
  and **policy/memory** env-flag changes flag `restartRequired: true`, applied via the
  **Restart backend** button (`RuntimeManager.restart`). **Runtime** section shows
  `runtime.json` versions.
- **MCP tier-1 QuickPick commands** (`src/mcp-quickpick.ts::buildMcpEntry`, pure —
  stdio env vars become `${VAR}` refs; http/sse's first env var becomes an
  `Authorization: Bearer ${VAR}` header, the GitHub-server convention): `crucible.mcpAddServer`
  chains QuickPick(transport) → InputBox(command/URL) → InputBox(name) →
  InputBox(env vars) → `upsertMcpServer`; `crucible.mcpListServers` → QuickPick of
  servers (state dot + tool count) → Enable/Disable/Reconnect/Remove.
- **GOTCHA — npm package must stay unscoped.** `apps/vscode-extension/package.json`'s
  `name` was once scoped (`@`-prefixed) and had to move to the unscoped
  **`crucible-vscode-extension`**: `vsce` rejects `@`/`/` in the extension identity
  (only surfaced once Task 15 added real marketplace fields and ran `vsce ls --tree`).
  All `npm run -w ...` invocations use the unscoped name. `apps/vscode-extension/.vscodeignore` was added at the same time
  (previously absent — without it `vsce package` would bundle `src/`, `test/`,
  `webview-ui/src/`, etc.). `publisher`/`icon`/`repository`/`categories` are placeholder
  marketplace fields (real branding is a later phase).
- **Release pipeline:** `scripts/release/make_manifest.py::build_manifest` scans a dist
  dir for conventionally-named artifacts (`crucible-indexer-<platform>[.exe]`,
  `rg-<platform>[.exe]`, `uv-<platform>[.exe]`, `crucible_agentd-<ver>-py3-none-any.whl`),
  sha256-hashes each, and emits the `RuntimeManifest` JSON (platform-outer iteration order
  so a missing-artifact `FileNotFoundError` names a stable first offender).
  `scripts/release/fetch_tools.py::stage(archive_bytes, kind, platform)` is a pure
  tar.gz/zip member-extractor (searches by basename, not a hardcoded nested path) for
  restaging official uv/ripgrep release archives under our naming convention — network
  I/O is confined to `main()`. `.github/workflows/release.yml` (tag-triggered `v*`):
  test → per-OS indexer matrix build → fetch-tools → package (wheel + manifest.json +
  `vsce package`) → attach to the GitHub Release → `vsce publish` (needs a `VSCE_PAT`
  repo secret). Exit-smoke criteria (fresh `~/.crucible`-less machine, wizard → chat →
  settings hot-swap/MCP-add/policy-restart → lockfile reuse on reactivation) are
  documented in the plan but require a live VS Code session to actually run.

SSE event types from the chat message endpoint:

| Event | Description |
|-------|-------------|
| `chat_agent_thinking` | Thinking status text (explore phase, classifying, drafting…) |
| `explore_tool_call` | A tool call during explore phase |
| `intent_classified` | `intent`, `likely_targets`, `files_examined` |
| `tool_call` | Execution agent tool call during ToolLoop; payload includes `tool`, `thought`, `args` dict (use `args.path` for filename display) |
| `patch_applied` | A patch op was applied in the shadow workspace |
| `diff_ready` | Inline change ready; payload has `inline_task_id`, `diff_entries` |
| `thread_title_updated` | Thread auto-named from first user message; payload: `{thread_id, title}` |
| `chat_response` | QA answer chunk |
| `chat_breadcrumb` | Durable record of a resolved gate/plan action (`{text, task_id}`), pushed live so it lands in history without a reload. Persisted as an `agent/text` message with `metadata.breadcrumb=true`. Broadcast to BOTH the chat channel and the task channel. |
| `plan_card` | Read-only plan version (`{task_id, plan_markdown}`), pushed to the transcript live. Persisted via `append_plan_card`. |
| `chat_done` | Turn complete |

`ChatAgent` flow per message (legacy path, `CRUCIBLE_CHAT_CONTROLLER` off — see reactive controller above for the flag-on path):
1. **Explore phase** — up to 5 tool calls via `PlanningToolRegistry`; results go into `context`
2. **Classify** — `IntentClassifier` → `qa / small_change / large_change`
3. **Respond** — `qa` → `generate_text` answer; `small_change` → `run_inline_change`; `large_change` → `create_task_from_chat`

`ChatMessage.metadata` fields used at runtime:
- `thinking_log: list[str]` — all thinking/tool-call snippets from the agent turn; stored on both QA messages and diff cards; rendered as a collapsible "Show thinking" pane
- `diff_entries: list[DiffEntry]` — files changed in an inline change (on `diff_card` messages). Each entry carries `unified_diff`: capped diff text (400 lines AND 24k chars per file, whichever hits first, truncation marker appended — `_cap_unified_diff` in `engine.py`) rendered as tabbed `DiffPanes` in DiffCard/StepGate; the full diff stays available via the native editor diff against `temp_path`. Entries without `unified_diff` (pre-cap persisted messages) fall back to the `FileRow` list
- `resolved: "applied" | "discarded"` — patched in by `resolve_diff_card()` after promote/discard; controls rendered state of diff card buttons
- `taskId: str` — inline task id on diff card messages
- `step_id` / `step_title` — on step-review record `diff_card` messages (`_write_chat_step_diff_record`): the durable transcript copy of a resolved step gate, persisted with `resolved` pre-set (`applied`/`discarded`) so it renders inert, written BEFORE the decision breadcrumb (reads: diff → ✓ accepted). NOT broadcast live — the live StepGate card already showed the diff. Auto-accepted steps get a `✓ Step completed: <goal>` breadcrumb instead of a record
- `breadcrumb: true` — marks a gate/plan-action transcript breadcrumb (rendered as a normal agent text line)
- `mentioned_files: list[str]` — paths the composer's `@`-mention dropdown resolved for this user message (never content — see "Composer intelligence" below); `UserMessage.tsx` linkifies only these exact `@path` tokens
- `tool_events: list[dict]` — durable tool pills in the webview's `ToolEventView` shape (`{id, tool, args, thought?, source, output?, isError?, done}` — frontend-camelCase keys). Live pills stream over SSE and die on reload; this is the persisted record (`chat/tool_events.py::trace_to_tool_events`, outputs capped at 4000 chars). Writers: engine `_write_chat_tool_events` (one pills-only `agent/text` message per step attempt with `step_id`/`step_title`, and per planning round — initial, feedback regen, delta replan), `ChatAgent` (explore pills on QA/clarify messages, pills-only message before task cards), and inline-change diff cards (explore + execution, re-id'd). Deliberately NOT broadcast live — the raw `tool_call`/`tool_result` events already render the live pills and there's no shared id to dedup against the bubble.

`ChatThreadStore.resolve_diff_card(inline_task_id, resolution)` scans all threads for the matching `diff_card` message by `task_id` and patches `metadata.resolved` in-place — called from the promote and discard API routes.

#### Composer intelligence + memory inspector polish (P4-C tail)

The last of the copilot-parity roadmap's P4-C scope. Spec/plan:
`docs/superpowers/specs/2026-07-06-composer-intelligence-design.md` +
`…/plans/2026-07-06-p4c-composer-memory-polish.md` (memory polish spec:
`…/specs/2026-07-06-memory-inspector-polish-design.md`). Merged to `main` 2026-07-06,
live-smoke verified (real backend + VS Code dev host, CDP-driven).

- **Shared trigger detection (`webview-ui/src/composerTrigger.ts` + `components/TriggerDropdown.tsx`):** `detectTrigger(text, cursor)` finds an in-progress `/` (only as the message's leading token, mirroring `parseSlashCommand`'s grammar) or `@` (anywhere, no whitespace between the `@` and the cursor) trigger token. `TriggerDropdown` is a presentational list positioned `absolute bottom-full` above the composer (mirrors `ModelMenu`'s popover) — arrow keys navigate, Enter/Tab selects (inserts, does not send), Escape dismisses. Purely additive: a `/name args` typed and sent without ever opening the dropdown still resolves via the pre-existing `parseSlashCommand`/`expandPrompt` flow untouched.
- **Unified `/`-dropdown:** `slash.ts::buildSlashDropdownItems(query, promptNames, skills)` merges the already-fetched prompt/skill catalogs (no new backend calls — both were already lazily fetched on the first `/` keystroke) into one filtered, badged (`Prompt`/`Skill`) list; prompt-file-wins-on-collision (mirrors `resolveSkillCommand`). Prompt rows show no sublabel (prompts have no description concept).
- **`@`-file mentions, end to end:**
  - **Discovery:** `extension.ts` exposes a `listWorkspaceFiles`/`workspaceFileList` round-trip over `vscode.workspace.findFiles` (standard ignore-dirs, capped 5000), lazily fetched on the first `@`. `openFile`/`vscode.window.showTextDocument` closure for click-to-open. Both are plain `extension.ts` closures (not `controller.ts`, which stays vscode-API-free) — the same pattern as the pre-existing `onOpenSettings`. The hand-maintained `vscode-shim.d.ts` (this repo doesn't pull in `@types/vscode`) gained `findFiles`/`asRelativePath`/`showTextDocument`.
  - **Insertion + tracking:** selecting a file inserts `@path ` into the draft; `InputArea.tsx` tracks the exact set of paths inserted via the dropdown for the current draft (`trackedMentionsRef`) — only those resolve to a real mention on send, never a blind `@`-regex scan (so an email address or handle typed by hand never triggers). On send, `mentionedPaths` (paths still present in the final text) rides the `sendMessage` webview message.
  - **Read + cap (`apps/vscode-extension/src/mentioned-files.ts::readMentionedFiles`):** `controller.ts`'s `sendChatMessage` reads each mentioned path off the real workspace and caps content at a **fixed 20,000-char constant** (`MENTION_FILE_MAX_CHARS`, deliberately not an env var — a UI-side convenience limit, not a backend policy knob); a missing/unreadable file becomes `"(file not found or unreadable)"` rather than blocking send.
  - **Contract:** editor-client `sendChatMessage(..., {mentionedFiles: {path, content}[]})` → HTTP body `mentioned_files`. `routes.py`'s `post_chat_message` parses it and passes it to `ChatController.handle_message(..., mentioned_files=…)` — **controller-only**, matching the existing convention for MCP/skills (the legacy `ChatAgent` path is untouched).
  - **Turn-scoped folding (`ChatController.handle_message`):** the referenced content is appended as a `"...\n\n---\nReferenced files:\n### path\n\`\`\`\n<content>\n\`\`\`"` block to a local `turn_message` that feeds **only** `_run_loop`'s `goal` (→ `plan_context["goal"]`) for that one call — the persisted/display `ChatMessage.content` stays the original short text, tagged with `metadata.mentioned_files` (paths only). **Verified live via direct SQLite inspection of `chat.sqlite3`:** neither `messages_json` nor `controller_history_json`/`controller_seed_json` (the cache-prefix replay history) ever contain the file content — it exists only in that turn's one-shot debug artifact (`controller-turn-NN.json`'s `goal` field). A later turn that needs the file again costs the model one extra `read_file` call; the mentioned path stays visible in the persisted display text as a breadcrumb either way. This was an explicit design tradeoff (bounded context growth over never-re-reading), not an oversight.
  - **Rendering:** `MessageRow` passes `msg.metadata?.mentioned_files` into `UserMessage`, which linkifies only those exact `@path` tokens (never an unrestricted regex over the bubble text) — click posts `{type:"openFile", path}`. **GOTCHA (found + fixed via live smoke):** the *optimistic* echo (`controller.ts`'s `appendChatMessage`, called before the network round-trip completes) originally hardcoded `metadata: {}`, so a mention only rendered clickable after a reload (when the persisted message with real metadata was fetched) — fixed to mirror the backend's shape so the link renders instantly on send.
- **Memory inspector polish:** `memory/{MemoryApp,BrowserTab,RecallTraceTab}.tsx` (a separate Vite bundle that already imported the shared `index.css` but predated the chat/settings design-token pass) migrated from hardcoded slate hex onto the same `--color-*` tokens; kind-accent colors (semantic/procedural/episodic) now reuse existing tint tokens (filled pill: tint bg + `var(--color-panel)` text) instead of one-off hex. A new header icon button in `ThreadView.tsx` (`db` icon, alongside the existing ☰) posts `openMemoryPanel`, routed through `chat-panel.ts`/`extension.ts` to the pre-existing `crucible.openMemoryPanel` command — no new capability-flag plumbing needed, since that command already degrades gracefully when memory is disabled. Deliberately **not** merged into the Settings pane/bundle and **not** an inline floating overlay (both considered, both rejected/deferred) — it stays a separate panel, just discoverable from chat now.

#### Live cards, gates & breadcrumbs (Class-A model)
The chat UI separates **interactive** affordances from the **durable transcript** — conflating them caused a cluster of bugs (cards vanishing with no record, reappearing on reload, 409 on re-accept, a crash).
- **Interactive gate/plan cards** render *only* in the pinned `/live` slot (`renderLiveGate` / `renderLivePlan` in `chat.js`), driven by `GET /v1/chat/threads/{id}/live`. `resolve_live_state` (`chat/live_state.py`) derives the one active gate from the task **status** via `_GATE_FIELD` (`AWAITING_{COMMAND,STEP,SCOPE,VALIDATION}_DECISION` → `pending_*` field) and the plan only at `AWAITING_PLAN_APPROVAL`. The slot auto-clears when status advances. The 1s `liveStateTimer` poll is the durable render path (survives reload + resume task-id churn); SSE events only *poke* a re-fetch. Step-review cards in chat have **no** SSE poke — they render purely from the poll.
- **Durable records** are persisted chat messages, also broadcast live so the transcript fills in real time (`/live` carries gate+plan only, never the message list): a `✓/✗/↻` **breadcrumb** (`write_chat_breadcrumb` → `chat_breadcrumb` event + `agent/text` message) for every resolved gate/plan action, and a read-only **`plan_card`** version.
- **`plan_card` is a version history.** `ChatThreadStore.append_plan_card(thread_id, task_id, md)` appends a new version (feedback regenerates → old plan stays, new appends after the `↻ feedback` breadcrumb) but skips a write identical to the task's current latest (collapses the double-writer / re-presentation). `_write_chat_plan_card` is the **single** writer (the chat agent no longer writes one) and broadcasts to **both** the chat channel (presentation turn listens here) and the task channel (execution stream picks up a feedback-regenerated version on approval). Frontend dedups by task+content signature (`planSig` in `chat.js`), so versions coexist while a re-delivery (live + reload, or both channels) never duplicates. Interactive Implement/Feedback buttons live ONLY in the `/live` slot — persisted `plan_card` messages are **read-only**.

**Gate invariants (each caused a real bug):**
- **Gates clear in place.** `_pause_for_step_review`, `_pause_for_validation_decision`, (and the scope/command callbacks) must reset `pending_*` + transition the **caller's** task object — NOT re-fetch a fresh record and reset that. The validation gate variant: `run_task`'s `finally` writes the chat completion line from its own local, which `return await _pause_for_validation_decision(...)` never rebinds — a re-fetched copy left it stale at `AWAITING_VALIDATION_DECISION` and the transcript got "Execution failed: <diagnostics>" after an accept. `transition()` mutates in place and the caller (`_execute_plan`) holds the reference; re-fetching a divergent object leaves the caller stale at `AWAITING_STEP_REVIEW`, which re-saves the stale gate (card reappears on reload, 409 on re-accept) and crashes the next transition (`Invalid transition: AWAITING_STEP_REVIEW -> VALIDATING`). Safe because the decision routes (`/step-decision`, `/scope-decision`, …) only `future.set_result(...)` — they never mutate/persist the task, so nothing changes it during the `await`.
- **Gate-raise `except ValueError` swallows ONLY true re-entrancy** (`task.status == target`); re-raise otherwise. An invalid-source transition (e.g. raising a scope gate while parked in `AWAITING_STEP_REVIEW`) silently swallowed strands `pending_*` behind a stale status, so `/live` renders the wrong gate (or none) and the task blocks until its decision timeout (scope default 600s).

**`/live` poll dedup-signature invariant (controller.ts `pollThreadLiveState`):** the 1s poll is the durable backstop for any *missed SSE terminal* (`chat_done`, gate-clear). It dedups on a `lastLiveSignature` (JSON of `{taskId, status, turnActive, gates, plan, runSummary, narrative, failure}`) to avoid webview churn, then `return`s early when unchanged. **INVARIANT: every durable signal consumed after the dedup gate MUST be in the signature** — else its transition is swallowed and the webview never learns. This bit three times: `runSummary`/`narrative`/`failure` (Review/Error card never updated) and **`turnActive`** (a controller chat turn has no task, so `status`/`gate`/`plan` stay null the whole turn → the `true→false` end transition was deduped away → `sendLiveStatus(...,false)` never sent → composer wedged on "Agent is working…" forever). The one **documented exception** is the READY_FOR_REVIEW `getTaskResult` fields (modifiedFiles/shadowPath/plan), safe only because they're immutable at that terminal status. Reconciliation has a second half in the webview reducer (`useAppState.ts` `liveStatus` case): on `turnActive=false` **with `status===null`** (controller turn ended) it seals any lingering streaming bubble (no data loss) AND re-enables input *even with no bubble* (an error-before-broadcast turn has none); gated to `status===null` so it never touches input during task execution.

### Memory harness (Phase 1 compaction + Phase 2 recall/consolidation)

Self-contained module (`agentd/memory/`): `harness.py` (the only unit the loops see), `compactor.py` (token-trigger eviction + summarize), `store.py` (SQLite — `compaction_segments`, `anchored_summaries`, + P2 `memories`/sqlite-vec/FTS5), `consolidator.py` + `recall.py` + `embedder.py` + `tool_source.py` (P2), `models.py`, `config.py`. ON by default since 2026-07-08 (`CRUCIBLE_MEMORY_ENABLED`; kill-switch via `0/false/no/off`); P2 also needs a workspace scope (wired via `build_memory_harness(..., workspace_path=…)` in `main.py` + `controller_factory.py`).

- **Wiring:** both ReAct loops call `await self._memory_harness.prepare_turn(history, run_id)` at the top of each iteration and `history[:] = _prep.history` (in-place, same list object). Sites: `chat/controller_loop.py` (run_id = thread_id) and `tools/loop.py` (run_id = `{task_id}:{step.id}` — per-step, so one step's anchor never leaks into another). `NO_OP_HARNESS` is a byte-identical passthrough when disabled. **prepare_turn is best-effort** — any compactor exception is swallowed (memory must never break a loop iteration).
- **Budget model (the fracs are setpoints, NOT a partition):** `maybe_compact` fires when `history_tokens ≥ window_tokens × trigger_frac` (default 0.65); it evicts the oldest **whole turns** (lossless at turn boundaries via `_select_hot`) down to a hot floor of `window × hot_token_frac` (0.4), persists them as `compaction_segments` (BEFORE summarizing → lossless), and folds them into the `anchored_summaries` anchor. Steady state is a **sawtooth between 0.4 and 0.65**; the 0.35 above the trigger (up to the full window) is deliberate overshoot headroom, because compaction is only checked at iteration start and one fat turn can blow past the trigger mid-turn. `CRUCIBLE_MEMORY_HOT_TURNS` caps **message count**, not logical turns (misnomer; the token floor usually binds).
- **Anchor = running summary-of-summary:** each round feeds the prior anchor + newly-evicted text to `make_engine_summarizer` (`harness.py`). `upsert_anchor` bumps `version` per round (v1→v2→…). The injected head message is `[MEMORY] Summary of earlier conversation that was compacted:\n<anchor>`.
- **Summarizer hardening (weak-model failure modes, all fixed via TDD):** the summary call uses a **single-key** `{"transcript": <flat text>}` payload (a multi-key JSON dict shape gets echoed back verbatim by weak models), the prompt (`_SUMMARY_SYSTEM`) is a 9-section Claude-Code-style "note to your future self" requiring output wrapped in one `<summary>...</summary>` block, and the result is **extracted + validated**: `_extract_summary` pulls the block, `_is_echo` rejects empty/JSON-object output, and the summarizer **retries once then raises `SummarizerEchoError`** → the compactor degrades (keeps the prior anchor, marks `degraded`, logs) rather than persisting garbage. Carry-forward is **goal-relevant, not strictly lossless** — a fully-superseded file may be recency-triaged out (accepted by design; a durable file-ledger outside the LLM summary is the deferred fix).
- **Observability:** `compactor.maybe_compact` logs `[memory] compacted run=… anchor=vN evicted=K anchor_chars=…` on success (it previously logged only on failure). Both loops broadcast a `memory_compacted` SSE event (`{evicted, anchor_version}`) when `_prep.compacted` — typed in editor-client's `StreamEvent`, rendered by `controller.ts` as a live `🗜️ Compacted N earlier messages into memory (vK)` chat line.

#### Phase 2 — cross-session recall + write path

Spec/plans: `docs/superpowers/specs/2026-06-28-memory-harness-phase2-recall-design.md`, `docs/superpowers/plans/2026-06-28-memory-harness-phase2{a,b,c}-*.md`. Live-validated (see `docs/superpowers/2026-06-29-memory-phase2-live-smoke-plan.md`).

- **Data model (`store.py`, same `memory.sqlite3`):** `memories` (id, scope_kind/scope_id, kind, content, entities JSON, importance, bitemporal `valid_from`=event/`created_at`=ingestion, `valid_to`/`superseded_by` lifecycle, `source_kind`, `source_ref`, A+link `source_seq_lo/hi`) + a co-located **sqlite-vec** `vec_memories` (`float[384]`, bge-small) + an **FTS5** `memories_fts` mirror. **`global` scope is reserved but never written in P2** (workspace + thread only; workspace is the consolidation default).
- **sqlite-vec is a hard dependency** (moved out of the `[memory]` extra 2026-07-03 — `agentd/memory/store.py` imports it unconditionally at module level, and that module is on the always-imported chain via `orchestrator/engine.py`, so a bare `pip install crucible-agentd` with no extras previously crashed the whole backend at startup regardless of `CRUCIBLE_MEMORY_ENABLED`; found via the P4 managed-runtime installer, which installs the plain wheel). The runtime *extension load* (`sqlite_vec.load(conn)`) is still guarded in try/except → `_vec_enabled`; a missing/incompatible native extension degrades to FTS5-only and never crashes the store (Phase-1 compaction depends on it too).
- **Write path (`consolidator.py`): LLM proposes, Python disposes.** Background `Consolidator` runs one `generate_json` distill (Mem0-style few-shot prompt → `CandidateMemory{kind,content,entities,importance,contradicts?}`) then a deterministic, **await-free** post-process: embed (off the loop via `to_thread`), dedupe (cosine ≥ `MEMORY_DEDUP_THRESHOLD` 0.92 vs same kind+scope), supersede (LLM `contradicts` hint; **episodic never supersedes**), insert. Existing-context fed to the LLM is **capped** (top-20 by importance+recency) so the prompt can't grow unbounded. Deliberate path = the `remember()` tool. **Triggers** (all fire-and-forget, best-effort, refs held so tasks aren't GC'd): compaction events (distill the evicted slice via the A+link seq span), task terminal (deferred — dormant subsystem), and **edit-promoting controller turns** (`submit_changes`; QnA/answer/clarify excluded).
- **Read path (`recall.py`): `RecallEngine`** fuses semantic (sqlite-vec ANN, over-fetched `k*4` then live+scope filtered) + lexical (FTS5 BM25) + structural (entity overlap) + importance + exponential recency, each **min-max normalized**, with a relevance floor (`min_score`). Filters `valid_to IS NULL` + scope before scoring. `recall_grounded` optionally grounds the top 1-2 in the code graph (`query_graph`, best-effort). The harness fills `prepare_turn`'s recall slot **every turn, cached per query**; the loop drops `recalled_memories` into the **dynamic tail** of the payload (KV-safe, finding #13), omitted when empty.
- **GOTCHA — two recall bugs found live (both invisible to unit tests):** (1) a raw user query with paths/dots/colons/`AND`/`OR` is **not** a valid FTS5 MATCH expression and raised a syntax error that nuked the whole recall — `_fts_match_query` now tokenizes + quotes each term. (2) The recall **query source is `plan_context["goal"]`** (the current user message), NOT `history` — the message isn't in `history` on turn 1, so `prepare_turn(history, run_id, query=…)` takes it explicitly (falls back to a history scan). Recall failures log with `exc_info` now.
- **Tools + prompt teaching:** `MemoryHarness.memory_tool_source()` returns a `MemoryToolSource` (`remember` + `recall`, the latter only when a recall engine is wired); the controller registers it. `format_controller_system_prompt(..., memory_enabled=…)` (env-resolved via `is_memory_enabled()`) appends a MEMORY block teaching `recalled_memories`/`recall`/`remember` — gated like `task_subsystem_enabled`.
- **Embedder:** one shared `Embedder` (bge-small, unit-normalized, lazy load, degrade-not-raise) for the consolidator + recall; warmed in a daemon thread at build so the first turn doesn't eat the ~130MB load.

#### Phase 3 — reranker (3-A backend) + inspector panel (3-B frontend)

Spec: `docs/superpowers/specs/2026-06-29-memory-phase3-reranker-inspector-design.md`. Plans: `docs/superpowers/plans/2026-06-29-memory-phase3a-reranker-trace-backend.md` (backend) + `…-phase3b-inspector-panel-frontend.md` (frontend). 3.2 (global-prefs UI) deferred.

- **Reranker (`memory/reranker.py`):** local `sentence-transformers` CrossEncoder (`BAAI/bge-reranker-base`), **independent of `MEMORY_ENABLED`** (own flag `CRUCIBLE_MEMORY_RERANKER`, default ON since 2026-07-08, kill-switch via `0/false/no/off`) and **degrade-not-raise** (model/lib absent → fused order, `available=False`). Slots into `RecallEngine` at the post-floor seam, **count-gated** (`CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES`, default 8) — reorders floor-passing candidates, never resurrects below-floor ones. `recall()` signature is **UNCHANGED**; `recall_with_trace()` does the work and `recall()` = `(await recall_with_trace(...))[0]`. Only the harness `_fill_recall` switched (every `_SpyRecall` test fake gained `recall_with_trace` in lockstep — same breakage class as P2's `prepare_turn(query=…)`).
- **Recall trace + persistence:** `RecallTrace`/`RecallTraceEntry` (`models.py`) capture per-candidate normalized signals (semantic/lexical/structural/importance/recency) + `fused_score` + `rerank_score` + `final_rank` + `injected`; entries cover **all** scored candidates (incl. below-floor `injected=false`), so a 0-candidate trace exposes the empty-query/FTS5 failure class directly. `TurnPreparation.recall_trace` is filled by `_fill_recall`; the **controller loop** persists it to `<workspace>/.crucible/state/artifacts/chat/<thread>/<turn>/memory-recall-NN.json` (best-effort).
- **Store browse helpers (`store.py`, read-only):** `list_memories(scope_kind, scope_id, kind=None, include_retired=False)`; `get_supersede_chain(memory_id)` (oldest→newest via `superseded_by`).
- **Three read-only GET routes (`api/routes.py`, gated by `is_memory_enabled()`):** `GET /v1/memory/inspect?thread_id=` → latest `RecallTrace` JSON or soft-empty `{entries:[]}`; `GET /v1/memory?scope_kind=&scope_id=&kind=&include_retired=` → `list[Memory]`; `GET /v1/memory/{id}/chain` → supersede chain. `/v1/config` gained `memory_enabled`. **GOTCHA (fixed 3-A):** the inspect route's artifact glob — `chat_turn_artifacts_root(thread_id, "", ws)` already returns `…/chat/<thread>` (`Path / ""` is a no-op join), so taking `.parent` over-strips to `…/chat` and the `base/*/memory-recall-*.json` glob probes one dir too shallow → always empty. Don't take `.parent`. (The soft-empty unit test masked it; `test_inspect_serves_persisted_trace` is the regression guard.)
- **Inspector panel (3-B, frontend):** a dedicated **`MemoryPanel`** is a **second Vite entry** in the React `webview-ui` (`memory.html` → `src/memory/{main,MemoryApp,RecallTraceTab,BrowserTab,types,vscodeApi}.tsx`), NOT the stale HTML-string "review-panel.ts" the spec named (chat fully replaced that with the `webview-ui` React app). `webview-ui` keeps **local mirror types** (it doesn't import editor-client). The host is split for testability: `src/memory-data.ts` (vscode-free — `handleMemoryMessage` + `MemoryDataSource`/`MemoryBrowseFilter`, unit-tested in the node-env vitest) and `src/memory-panel.ts` (the `vscode` `MemoryPanel` class, mirrors `chat-panel.ts` asset-rewrite+CSP). `controller.ts` stays **vscode-free**, exposing `memoryDataSource()`/`memoryThreadId()`/`memoryWorkspacePath()` (client built from the backend URL, session-independent like `attachToTask`); **`extension.ts` owns panel construction** (needs `context.extensionUri`). Command `crucible.openMemoryPanel` is gated by the `crucible.memoryEnabled` `when`-context fed from `/v1/config` (mirrors `taskSubsystemEnabled`). editor-client adds Zod `RecallTrace`/`RecallTraceEntry`/`MemoryView` + `getMemoryInspect`/`listMemories`/`getSupersedeChain` (snake→camel; routes return snake_case, signals keys pass through unmapped). Read-only; no live polling (Refresh button re-fetches).
- **Phase-3 config env vars:** `CRUCIBLE_MEMORY_RERANKER` (default on since 2026-07-08), `CRUCIBLE_MEMORY_RERANKER_MODEL` (default `BAAI/bge-reranker-base`), `CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES` (default 8).

### Backend authentication

Spec: `docs/superpowers/specs/2026-10-04-backend-auth-design.md`. Plan: `docs/superpowers/plans/2026-10-05-backend-auth.md`.

- **Start:** `python -m agentd.serve --port N [--reload] [--workspace-lock <ws>]` (`agentd/serve.py`, stdlib + uvicorn imports only) binds `127.0.0.1` first, writes a fresh 43-char token to `~/.crucible/run/agentd-<port>.token` (0600, atomic, under an `flock` shared with a 7-day tidy), writes the lock, prints `CRUCIBLE_SERVE {"pid","port"}`, then serves the pre-bound socket. Token and lock are never deleted at shutdown; a live backend bumps its token file's mtime daily.
- **Middleware** (`agentd/auth.py`, pure ASGI, outermost): loopback peer (403) → `Host` exactly `127.0.0.1:<port>` (421) → no `Origin` (403) → `Authorization: Bearer` (401). The only exemption is exactly `GET /health`. 503 until the startup hook (index 0) has loaded the token.
- **`/health?nonce=<hex>`** returns `pid`, `proof = HMAC(token, "crucible-health-v1\0"+nonce)` and `bound = HMAC(token, "crucible-health-bound-v1\0"+nonce+"\0"+pid+"\0"+workspace)` — clients verify the server holds their token without sending it, and reuse checks it is the right pid and workspace.
- **Clients verify before sending:** the extension's `BackendGate` (`src/backend-auth/backend-gate.ts`) probes before the first request to a URL and after any connection error, and never forwards (or sends a token) unless the probe is `authed`; `localhost` is rewritten to `127.0.0.1`; no redirects are followed. editor-client takes `authToken`/`onAuthStatus` and throws `BackendAuthError` on 401/403/421. The Rust indexer verifies once per token, POSTs with `.no_proxy()`, and prints `auth=1` for `--version`. Dev scripts use `scripts/_backend_auth.{py,sh}`; a grep test (`tests/test_scripts_use_auth_helper.py`) enforces it.
- **Managed runtime:** pre-spawn checks (`import agentd.serve`, indexer `--version` with `auth=1`, 5 s timeouts) raise `RuntimeUpdateRequiredError` → one modal (an editable dev install is pointed at `scripts/dev/install-local.sh`, which also builds the indexer).
- **GET routes stay read-only** — `tests/test_get_routes_read_only.py` pins the reviewed list; a new GET route fails it until reviewed.

### ChatGPT plan usage ("Continue with ChatGPT")

Spec: `docs/superpowers/specs/2026-10-06-chatgpt-plan-usage-design.md` (built from every page under https://developers.openai.com/siwc). Eligible ChatGPT Plus/Pro users sign in and Crucible's model calls go to `POST https://api.openai.com/v1/responses` with the account's OAuth token — usage comes out of their ChatGPT plan (Plus shares one five-hour limit across every app). Backend id **`chatgpt`**.

- **The backend owns tokens** (`agentd/chatgpt_auth/`): `store.py` (one 0600 record per registration under `~/.crucible/auth/chatgpt/registrations/`, `host.json` = the machine's `ext_agent_host_id`; override the root with `CRUCIBLE_CHATGPT_AUTH_DIR`), `oauth.py` (`AttemptManager`: fresh state/nonce/PKCE per attempt, one-shot loopback listener on `127.0.0.1` — port 1455 else any — at `/auth/callback`, separate from the API socket; first sign-in uses `client_id=dynamic_agent_client` + `agent_name_hint=Crucible` and keeps the issued `oaiapp_…` id; a returning account must come back as the same subject and client), `oidc.py` (discovery, code exchange, refresh, revocation, ID-token verification against JWKS), `session.py` (`ChatGPTPlanBearer`: refresh 5 min before expiry under the registration's **cross-process flock**, re-reading the record first because refresh tokens rotate and every workspace's backend shares them; unusable refresh codes → `needs_sign_in`, network/5xx keep the credentials; `sign_out` revokes then clears tokens but keeps the registration), `service.py` (process singleton; one bearer per registration so in-process refreshes serialize too). The extension only opens the browser and sends the registration **id** (never a token) as `CRUCIBLE_CHATGPT_REGISTRATION` — spawn env, or `credentials` on validate/hot-swap.
- **Routes** (`api/chatgpt_auth_routes.py`): `POST /v1/auth/chatgpt/attempts` → `{attempt_id, authorize_url}` (the URL can carry an `id_token_hint`: browser only, never logged); `GET …/attempts/{id}`; `POST …/attempts/{id}/cancel`; `GET …/registrations` (summaries, no tokens); `POST …/registrations/{id}/sign-out` → `{remote_revoked}`; `POST …/registrations/{id}/models` (POST because it may refresh). `/v1/config` provider gains `uses_chatgpt_plan`.
- **Transport** (`providers/openai_transport.py`, also the API-key `openai` backend): every call streams with `store:false` and an `input` array; `plan_route=True` sends only `PLAN_ROUTE_FIELDS` (no `temperature`/`max_output_tokens`/…); success = `response.completed`, a stream without it is transient, `response.failed`/`incomplete` are errors; a 401 gets one refresh-and-retry. Schemas go through **`providers/openai_strict_schema.py`**: OpenAI strict mode rejects a root union, free-form objects (`tool_call.args`) and optional fields, so the codec wraps the root in `{"action": …}`, sends free-form objects as JSON strings, makes optional fields required+nullable and `oneOf`→`anyOf` — and decodes the reply back, so the engine sees today's shapes. `test_every_schema_we_send_encodes_to_openai_strict` lists every schema passed to `generate_json`; **a new schema must be added there**.
- **Access stops are not outages** (`providers/plan_access.py`): `ProviderAccessStopped` (usage limit 429, not eligible, session invalid, plan disabled, unsupported capability, misconfigured) is excluded from `is_provider_unavailable` (a plan usage limit is a 429 too). `ControllerLoop` re-raises it on the first call (no correction, no retry); the main turn ends with a user-facing message and `/live` `provider_access` drives the card; a team member's stop **pauses the team** (`ProviderStopped` event, reason `provider <kind>`) and reports `failed_transient`, so nothing re-queues and `resume_team` restarts it.
- **UI follows OpenAI's UI/UX guidelines**: approved "Continue with ChatGPT" button (`components/shared/ChatGPTBrand.tsx` — don't restyle it); Settings › Provider's ChatGPT plan card (accounts, sign in again, re-enable via `prompt=consent`, sign-out with unconfirmed-revocation notice, model picker from the account's catalog); one-time "You're using your ChatGPT plan" modal per registration; "Using ChatGPT plan · Manage usage" beside the composer's model chip; usage-limit card with Manage usage as the primary action; the setup wizard offers the plan first (it starts the backend model-less — `getProviderSettings` allows an empty model for `chatgpt` only — then signs in). Host logic: `src/chatgpt-settings.ts` (vscode-free, shared by Settings, its chat overlay and Setup), `src/chatgpt-deps.ts`. `providerAccess` is in `lastLiveSignature` (the dedup invariant).
- **Live-verified 2026-10-06 (Phase 0, a ChatGPT *Go* account — the docs say Plus/Pro, Go worked):** strict `json_schema` works via the codec (6/6 controller round trips; a raw root `anyOf`, `oneOf` or free-form object is a 400 `invalid_json_schema`); reasoning effort `none…max` works (`minimal` is rejected); `prompt_cache_key` and `max_output_tokens` are accepted; `temperature`, string `input`, `store:true` and `stream:false` are refused with a bare `{"detail": …}` 400. The catalog entry carries `context_window` (272000), which Settings sends as the compaction window. Findings: spec §11.
- **Plan route = native function calling (`providers/openai_native.py`).** These Codex-tuned models answer in output items: `commentary`-phase messages (prose preambles), `function_call` items (actions — after which the model stops and waits), and a `final_answer` message. Asking for an action as schema-constrained *text* forced every message, preambles included, to be an action, so one response held several made-up steps (a `write_todos` ritual repeated 10×, a nonexistent `wait_agents`, a fabricated summary of sub-agent reports — all live, 2026-10-07). On the plan route `generate_json` therefore sends each action type as a function tool in a `crucible` namespace (a flat schema → one forced function) with `tool_choice: required` + `parallel_tool_calls: false` (exactly one call), and replays `conversation_history` as native items: actions → `function_call`, results → `function_call_output`, everything else → messages. The conversion is lossless (`from_native_input` round-trips every history shape and real payloads in `tests/fixtures/native_payloads/`); a result whose label isn't its call's own tool keeps it as a `[from …]` header, a call without a recorded result gets `NO_RESULT` (the API needs one output per call). Measured: no cache cost (hits cover the instructions+tools prefix in every format) and no reasoning-item replay needed to stop the loop. Preamble text goes to the thinking pane; the exact request is dumped to `_provider_debug/chatgpt/native-<schema>.json`. The API-key route keeps structured text (`native_tools=False`), where `_stream_once` takes the first complete JSON message and stops.
- **Plan-route 400s with a bare `{"detail": …}` body** (e.g. "The 'x' model is not supported when using Codex with a ChatGPT account.") map to `PlanUnsupportedCapability`: input-determined, so no retry and no correction loop.
- **Open items:** Remote-SSH/VM hosts can't sign in (the loopback reaches the browser's machine). Usage-limit and revoked-session paths are covered by tests only (not reproducible on demand).

### Retrieval pipeline
- `indexer-rs` writes `index-snapshot.json` with `nodes`/`edges`/`diagnostics`/`stats`
- `agentd-py` reads the snapshot per task via `retrieval/` module; if missing, auto-triggers one index run
- Retrieval context flows into `PlanningAgent.generate_plan()` as `initial_context` and into step execution via `patch_request_context`
- `graph_neighbor_files` (in the planner payload via `RetrievalContext.as_prompt_payload()`): files reached from the goal's matched/semantic seeds by one structural hop, surfaced as an initial reading list
- Stale/missing snapshots emit warning diagnostics but never block orchestration
- Both `PlanningToolRegistry` and `ToolRegistry` also give the agent live access to the workspace during their loops (not just the snapshot)
- **Gotcha — ignored ANCESTOR dirs silently disable indexing for a whole workspace.** `is_ignored_path` (`indexer-rs/src/service.rs`) matches its `IGNORED_DIRS` (`.git`, `node_modules`, `.venv`, `target`, `dist`, `build`, `vendor`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.crucible/state`, `.crucible`, `.tmp`) against components of the **full absolute path**, not the path relative to the workspace root. So if `--workspace` points *under* a dir with one of those names — e.g. a stress/smoke workspace at `.../.tmp/smoke-xxx` — **every file is filtered**: the watcher starts, LSP warms up, but the snapshot stays at 0 nodes and file-change events are ignored. The watcher is fine; the workspace location is the problem. Put real workspaces outside ignored-named ancestors (e.g. `workspaces/…`, like `crucible-stress` → 4328 nodes), not under `.tmp/`. (The within-workspace ignore of these dir names IS intentional — only the ancestor match is the footgun.)
- **Supported languages (`indexer-rs/src/parser.rs`, `is_supported_source_path` in `service.rs`):** TypeScript/TSX, JavaScript/JSX (`.js`/`.jsx`/`.mjs`/`.cjs` — ride the TypeScript/TSX tree-sitter grammars directly, since both are strict supersets of JS; no separate grammar or extractor), Python (bespoke `rustpython_parser` walker, not tree-sitter), Rust, Go, Java. Go and Java added 2026-07-08 — parsing is unit-tested in `tests/parser_tests.rs`, and live LSP-resolved `Calls`/`Inherits` fan-out via gopls/jdtls has been smoke-tested end-to-end (see below) — the one thing NOT separately exercised is `Implements` fan-out specifically (interface→concrete-impl dispatch discovery), only `Calls`/`Inherits`. **Go-specific:** owner ids for structs/interfaces are scoped by parent **directory**, not file (`go_owner_id`) — Go's package-per-directory model means a struct's methods routinely live in a different file than its `type` declaration, and a file-scoped id (the convention every other language here uses) would split one struct into two unrelated symbol nodes; live-verified with a 2-file smoke workspace (type in one file, method in another → single shared `App` node). Struct field embedding (Go's composition-based "inheritance") is NOT modeled as an `Inherits` edge in v1 — only explicit `extends`/`implements`-style heritage (TS/Java) and nominal base classes (Python/Rust) are.
- **Retrieval pipeline mirrors the extension list.** `agentd-py`'s `retrieval/chunker.py::_LANGUAGE_MAP` and `retrieval/artifact_client.py::_is_supported_source_path` are separate, manually-mirrored copies of the same extension set (Python has no import of the Rust side) — both had gone stale (missing Go/Java, and JS/JSX had never been added either) and were fixed alongside the Go/Java indexer work. `_is_supported_source_path` only affects the `repository_structure` file-count summary shown to the planner (cosmetic-but-visible); `_LANGUAGE_MAP` affects the `Language: <x>` tag on every semantic-search chunk (`chunk.language`, rendered straight into the LLM-visible chunk text) — missing entries silently degrade to `"unknown"` rather than breaking, so this class of staleness is easy to miss. Live-verified (not just unit-tested): `RetrievalArtifactClient.load_context()` against the Go smoke snapshot correctly surfaced `App`/`Build`/`Run`/`Repo` in `planner_evidence` with accurate line numbers/snippets — the **graph-based** half of retrieval works for Go end-to-end. The **semantic/embedding** half (chunker → sentence-transformers → sqlite-vec ANN) was NOT separately live-tested for Go/Java (would need the embedder wired up); only inferred safe because chunk boundaries are snapshot-node-driven, not language-gated.

#### Symbol-graph edge resolution (LSP)
- The Rust parser emits `Calls`/`Inherits` edges to `external:<kind>:<name>` placeholders, then a resolver stage (`indexer-rs/src/resolver.rs`) queries the LSP (`textDocument/definition` + `implementation`) and rewrites them to workspace symbol nodes. `Implements` edges fan out from concrete impls to a Protocol/ABC/interface declaration. Python call bodies are walked for call sites; Python class bases, TS/JS `extends`/`implements`, Go's `method_declaration` receivers, and Java `extends`/`implements` are resolved to workspace classes via `definition`.
- **LSP must be ON for resolution.** `start-backend.sh` launches the self-updating watcher with `CRUCIBLE_LSP_ENABLED=true` (+ `CRUCIBLE_LSP_{PY,TS,RS,GO,JAVA}_CMD`, `CRUCIBLE_LSP_STARTUP_TIMEOUT_MS`, `CRUCIBLE_LSP_REQUEST_TIMEOUT_MS`). The watcher pays a one-time rust-analyzer warmup per launch, then resolves incrementally per changed file. The synchronous auto-index fallback (`retrieval/artifact_client._render_index_command`) forces LSP **off** (fast, tree-sitter-only) to avoid stalling a task; the watcher re-resolves and overwrites within ~a minute. `CRUCIBLE_LSP_GO_CMD` defaults to `gopls`; `CRUCIBLE_LSP_JAVA_CMD` defaults to `jdtls` (Eclipse JDT LS — heavier to spawn than the other servers here; needs a JDK on PATH and is slower to warm up). JS/JSX files are opened in the **same** `typescript-language-server` session as TS/TSX (one shared `LspLanguage::TypeScript` session, not a second process) so cross ts↔js `definition`/`implementation` resolution stays intact.
- **Resolution caveats:** pyright (open-source) does NOT implement `textDocument/implementation` (that's Pylance), so Python `Implements` fan-out is empty — use `Inherits` for nominal Python subclass discovery instead. Only NOMINAL subclassing is tracked; a class that conforms to a Protocol structurally without declaring it as a base is not in the graph. gopls and jdtls both advertise `textDocument/implementation` support; `Calls`/`Inherits` resolution is live-verified for both (see below), `Implements` fan-out specifically has not been separately exercised.
- **GOTCHA (found + fixed live, 2026-07-08) — unacked server-to-client LSP requests silently stalled gopls.** `window/workDoneProgress/create` is a **request** the server sends the client (carries both `id` and `method` — the LSP spec allows several of these: `client/registerCapability`, etc.), not a notification. `LspSession`'s message loops (`request_with_timeout`, `drain_notifications`, `wait_until_indexed`) matched incoming messages on `id` alone (`response_id`), so this class of message looked like "a response to someone else's request" and was silently dropped — never acknowledged. gopls (the first server here to actually send one, once `workDoneProgress: true` was advertised) blocked its own request pipeline waiting for that ack: `textDocument/definition` timed out at the full `CRUCIBLE_LSP_REQUEST_TIMEOUT_MS` even though gopls itself would have answered in ~0.1s. Root-caused with a minimal standalone Python LSP client (bypassing our adapter) that reproduced the hang, then confirmed the fix by acking the request in that same script before touching Rust. Fix: `is_server_request()` (payload has both `id` and `method`) checked before the response-id match in all three loops; `ack_server_request()` replies with a generic `{"result": null}` — every server request observed so far only needs acknowledgment, not a real answer, so this acks generically rather than maintaining a per-method allowlist (future servers that send other request types are covered automatically).
- **GOTCHA (found + fixed live, 2026-07-08) — two hardcoded `[TypeScript, Python, Rust]` language arrays wasted a full server spawn+indexing-wait per bootstrap for languages the workspace doesn't even use.** `wait_for_indexing` and `flush_notifications` both iterated a fixed 3-language list (silently missing Go/Java too), unconditionally calling `ensure_session`/`apply_operation` — so e.g. a Go-only workspace paid to spawn AND wait on pyright+tsserver+rust-analyzer for nothing, and (worse) never waited on gopls's own indexing at all, so the resolver queried a cold gopls and got no resolution back. Fixed by deriving the wait/flush set from `self.sessions` entries that `open_workspace_files` already started (i.e. languages with a real file in the workspace) instead of a hardcoded list — scales to any number of registered languages for free, and fixes the pre-existing waste for the original three languages too, not just Go/Java.
- **GOTCHA (found + fixed live, 2026-07-08) — `extract_call_target`'s `last_identifier` picked the alphabetically-last token, not the textually-last one.** It was implemented as `extract_identifiers(text).sort().dedup().last()` — for a bare call this is a no-op (one token), but for a member-access call like `a.Build()` where the receiver name sorts alphabetically *after* the method name (`'B' < 'a'` in ASCII), it silently returned `"a"` instead of `"Build"`. Harmless when the LSP resolver succeeds (resolution is **position**-based, not name-based — `go_callable_position`/`typescript_callable_position` already pointed at the right spot), but wrong whenever resolution fails or is off: the leftover `external:call:<name>` placeholder node showed the receiver, not the callee. Found via a live gopls smoke test (`a.Build()` produced an orphaned `external:call:a` node instead of `external:call:Build`); this bug predates Go/Java and affects TS/Rust too (no existing test happened to use a receiver name that sorts after its method name) — fixed by splitting the tokenizer into a position-preserving primitive (`tokenize_identifier_candidates`) that `first_identifier`/`last_identifier` use directly, while `extract_identifiers` (used by `extract_variable_declarators`, which wants the distinct name set, not positional order) keeps its sort+dedup. Regression test: `typescript_parser_targets_the_method_not_the_receiver_when_receiver_sorts_last`.

#### `query_graph` tool (symbol-graph navigation)
- Registered in `PlanningToolRegistry` and `ToolRegistry` **only when an `index-snapshot.json` exists**; backed by `retrieval/graph_walker.py`. Reachable in all three context-gathering loops: planning, execution (must be in `verify_phase_sm._ALLOWED_TOOLS` for the current state — it is, for every state), and chat explore (must be in `chat/agent.py::_EXPLORE_SCHEMA` tool enum — it is).
- Two modes: file seed `node="<path>"` → distinct neighbour files grouped "depends on / connects out" vs "used by / connected in"; symbol seed `node="<path>:Symbol"` → symbol-level edges with line numbers. `edge_kinds` filters Calls/Imports/References/Inherits/Implements; `depth` (max 3), `limit` (max 60).
- Teaching blocks: `planning/prompts.py` (planning loop), `reasoning/tool_prompts.py` (execution loop), `chat/agent.py::_EXPLORE_PROMPT` (chat). The `tool` field in both response schemas is a free string — tool availability is the `registry.definitions() ∩ sm.allowed_tools()` intersection (execution) or the schema enum (chat), NOT a schema tool-enum.

### Testing patterns
- Python tests in `services/agentd-py/tests/` use `ScriptedPlanningEngine` / stub `Reasoner` classes + `InMemoryTaskStore` + `ShadowWorkspaceManager(tmp_path)`
- `ScriptedPlanningEngine` implements all three reasoning methods: `create_tool_step()`, `create_planning_step()`, and legacy `create_patch()`
- Scripted `create_tool_step()` responders must detect verify phase and return `verify_done` when entered. Pattern:
  ```python
  in_verify = any(
      isinstance(msg.get("content"), str) and "Patch applied successfully" in msg["content"]
      for msg in history
  )
  if in_verify:
      return {"type": "verify_done", "thought": "scripted", "verified": True, "test_output": ""}
  ```
- `pytest-asyncio` with `@pytest.mark.asyncio` for all async tests
- Integration-style tests (no mocks of the file system or HTTP) — real `tmp_path` shadows, real `PatchEngine`
- TypeScript tests use vitest; VS Code extension tests use a stub `ControllerUI` implementation
- **`InMemoryTaskStore.get()` returns the SAME object reference** (no copy), which MASKS stale-reference / object-divergence bugs. For a test that depends on production semantics — store returns a fresh copy, e.g. verifying a coroutine advanced the *caller's* task object — use `SQLiteTaskStore(tmp_path / "x.sqlite3")` instead.
- **Never `asyncio.get_event_loop().run_until_complete(coro)` in tests.** On Python 3.13 it raises `RuntimeError: There is no current event loop` once a prior `@pytest.mark.asyncio` test closes the loop, so the test passes in isolation but fails order-dependently in the full suite. Use `asyncio.run(coro)` or `@pytest.mark.asyncio async def`.
- **`pytest | tail` (or any pipe) masks pytest's exit code** with the pipe's last command's (always 0). Read the actual `FAILED`/summary lines, never trust the reported exit code of a piped run.
- **Never pass `-q` to pytest here** — `pyproject.toml` already sets `addopts = "-q"`, so a CLI `-q` stacks to `-qq`, which suppresses the final `N passed in Xs` summary line entirely (you get dots with no count and no failure summary). Run plain `pytest [paths]` for the summary; when you must capture output, redirect to a file and check `$?` directly: `pytest --color=no > /tmp/out.txt 2>&1; echo exit=$?; tail -5 /tmp/out.txt`.
- A shifting failure set across full-suite runs = order/state pollution or environment dependence (e.g. `test_graph_walker_reachability` is `@requires_live_snapshot` and reflects `index-snapshot.json` freshness). Reproduce a suspect failure **in isolation** before attributing it to your change.

## Key Configuration

### Python backend env vars

**Core**
- `CRUCIBLE_REASONING_BACKEND` — LLM provider: `openai`, `anthropic`, `gemini`, `groq`, `ollama`, `watsonx`, `openrouter`, `openai_compatible` (default: `openai`)
- `CRUCIBLE_DB_PATH` — SQLite database path (default: `.crucible/state/agentd.sqlite3`)
- `CRUCIBLE_SHADOW_ROOT` — shadow workspace root (default: `.crucible/state/shadows`)
- `CRUCIBLE_LOG_FILE` — path for the agentd file log (default: `.crucible/state/agentd.log` relative to uvicorn CWD); tailable with `tail -f services/agentd-py/.crucible/state/agentd.log`
- `CRUCIBLE_CHAT_DB_PATH` — SQLite path for chat threads (default: `.crucible/state/chat.sqlite3`)
- `CRUCIBLE_PROJECT_INSTRUCTIONS` — inject `<workspace>/AGENTS.md` into the controller system prompt (default **ON**; kill-switch — `0/false/no/off`). See "Project instructions (AGENTS.md) + prompt files".
- `CRUCIBLE_INSTRUCTIONS_MAX_CHARS` — size cap for the injected AGENTS.md (default `16000`; over-budget truncates with a marker).
- `CRUCIBLE_SKILLS_ENABLED` — discover + offer `.crucible/skills/*/SKILL.md` to the controller (catalog + `read_skill` + `/skill` forced-load). Default **OFF**; opt in with `1/true/yes/on`. See "Agent Skills (P2)".
- `CRUCIBLE_SKILLS_CATALOG_MAX_CHARS` (default `16000`; order-truncates the catalog) + `CRUCIBLE_SKILLS_BODY_MAX_CHARS` (default `20000`; caps a loaded skill body).
- `CRUCIBLE_MCP_ENABLED` — connect external MCP servers from `.crucible/mcp.json` and offer their tools to the controller. Default **OFF**; opt in with `1/true/yes/on`. See "MCP client (P3)".
- `CRUCIBLE_MCP_DECISION_TIMEOUT_SEC` — seconds to wait for the user's mcp_tool gate decision; `0` (default) = wait forever; timeout → reject.
- `CRUCIBLE_MCP_TOOLS_MAX_CHARS` — char budget for MCP tool definitions in tools_json (default `16000`; order-truncation).
- `CRUCIBLE_MCP_CONNECT_TIMEOUT_SEC` — per-server connect wait at startup before continuing without it (default `30`).
- `CRUCIBLE_MCP_CALL_TIMEOUT_SEC` — per-call timeout for an MCP tool invocation (default `120`).
- `CRUCIBLE_LISTEN_PORT` / `CRUCIBLE_SERVE_PID` — set by `python -m agentd.serve` for the app process only (stripped from every backend subprocess by `agentd/child_env.py::child_env()`); the app refuses to start without them, so bare `uvicorn agentd.main:app` no longer works. (`CRUCIBLE_PORT` is retired.)
- `CRUCIBLE_AUTH_DISABLED=1` — skips only the bearer-token check (peer, Host and Origin checks stay). **Any local user, and any web page doing blind GETs, can then drive the backend**; a warning repeats every 60th request. Never inherited by a managed spawn.
- `CRUCIBLE_REWIND_RETENTION_TURNS` — rewind checkpoints retained per chat thread (default `50`; oldest pruned, their file snapshots deleted with them).
- `CRUCIBLE_REWIND_MAX_FILE_BYTES` — files larger than this are not snapshotted for rewind (default `10000000`); they are recorded `oversize` and reported as not-restored rather than restored wrong.
- `CRUCIBLE_SUBAGENTS_ENABLED` — the controller's `dispatch_agents` + shared-workspace write guard (default **ON**; kill-switch `0/false/no/off`; controller-only). Limits: `CRUCIBLE_SUBAGENT_MAX_DEPTH` (2), `CRUCIBLE_SUBAGENT_MAX_CONCURRENT` (8, process-wide), `CRUCIBLE_SUBAGENT_MAX_ITERS` (100, when a definition sets no `maxTurns`). See "Sub-agents (P5)".
- `CRUCIBLE_TEAMS_ENABLED` — agent teams: the main agent's `create_team` and the team tools (default **ON**; kill-switch `0/false/no/off`; inert without sub-agents). Limits and round timeouts: see "Agent teams" under Sub-agents.
- `CRUCIBLE_SKILLS_DISABLED` — comma-separated skill names to exclude from the catalog (user-local disable, set by the extension's settings panel; not cached with the catalog's mtime signature).
- `CRUCIBLE_REASONING_EFFORT` — `off|low|medium|high|max`, unset by default. Unset sends NO effort field to the transport at all, which is what leaves each provider's own legacy dial (`CRUCIBLE_GEMINI_THINKING_LEVEL`, `CRUCIBLE_GROQ_REASONING_EFFORT`, `CRUCIBLE_OLLAMA_THINK`) exactly as it is today — the backward-compatibility guarantee, achieved with no extra code. Normally set from a composer chip beside the model picker, which persists the EFFECTIVE (post-clamp) rung to `globalState` and injects it on the next managed spawn; `PUT /v1/config/provider {reasoning_effort}` hot-applies it to every live transport with no restart, and `GET /v1/config` reports the current rung plus the capability map. See `providers/reasoning_effort.py` above and `docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md`.
- **`start-backend.sh` defaults ON (2026-07-02):** `CRUCIBLE_CHAT_CONTROLLER`, `CRUCIBLE_SKILLS_ENABLED`, `CRUCIBLE_MCP_ENABLED` (+ `CRUCIBLE_SEMANTIC_RETRIEVAL=true`) — the engine defaults above stay OFF, but the script opts in (`${VAR:-1}`, override via env to opt out; same pattern as the scope-policy note). The repo-root `.env` sets the same flags for manual runs.
- Provider API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, etc.
- `CRUCIBLE_OPENAI_COMPAT_BASE_URL` / `_MODEL` / `_API_KEY` — the generic `openai_compatible` backend: any OpenAI `/chat/completions` endpoint (NVIDIA NIM, vLLM, LM Studio, Together, DeepInfra). Base URL and model are required (no defaults); the key is optional for self-hosted endpoints. Also `_MAX_TOKENS` (4096), `_JSON_MAX_TOKENS` (16384), `_TIMEOUT_SEC` (120 — bounds opening the stream and each HTTP read), `_STREAM_TIMEOUT_SEC` (600 — a TOTAL deadline on consuming one response; an endpoint that keeps trickling chunks never trips a per-read timeout, measured on NIM as 9+ min hung turns, so expiry raises `TransientTransportError` and the call is retried), `_MAX_RETRIES` (4). Frontend counterpart: the `ProviderInfo.keyOptional` flag (mirrored in `setup-data.ts` PROVIDERS + `webview-ui/src/settings/types.ts`) marks a **non-local** provider whose API key is genuinely optional, so the wizard/settings don't treat a blank key as a blocking omission — `local` alone can't express it (an `openai_compatible` endpoint may be remote and keyless).

**Model selection** (per provider)
- `CRUCIBLE_GEMINI_MODEL`, `CRUCIBLE_OPENAI_MODEL`, `CRUCIBLE_ANTHROPIC_MODEL`, `CRUCIBLE_GROQ_MODEL`, `CRUCIBLE_OLLAMA_MODEL`
- `CRUCIBLE_GEMINI_THINKING_LEVEL` — enables extended thinking for Gemini 2.5+ models (`none` | `low` | `medium` | `high`)

**Memory harness (see "Memory harness" under Architecture)**
- `CRUCIBLE_MEMORY_ENABLED` — master switch, default **ON** since 2026-07-08 (truthy = `1/true/yes/on`; set to `0/false/no/off` to disable). When off, `prepare_turn` is a byte-identical passthrough. (Phase-2 recall/consolidation additionally needs a workspace scope, which the factories pass.) The managed runtime installer (`apps/vscode-extension/src/runtime/installer.ts`) installs the `crucible-agentd[memory]` extra (pulls in `sentence-transformers`/PyTorch, ~500MB-1GB+) specifically so this default works out of the box instead of silently degrading the embedder.
- `CRUCIBLE_MEMORY_DB_PATH` — SQLite path (segments + anchors + memories) (default `.crucible/state/memory.sqlite3`)
- `CRUCIBLE_MEMORY_WINDOW_TOKENS` — effective context window the fracs are taken against (default `128000`). Normally set for you: the settings panel's Provider section has a **Context window** field whose value the extension persists to `globalState` and injects here on the next managed spawn, and `PUT /v1/config/provider {context_window}` hot-applies it to every live compactor without a restart. A hand-set env var still works and is what a non-managed backend uses.
- `CRUCIBLE_MEMORY_COMPACT_TRIGGER_FRAC` — fire compaction at this × window (default `0.65`)
- `CRUCIBLE_MEMORY_HOT_TOKEN_FRAC` — evict down to this × window of newest whole turns (default `0.4`)
- `CRUCIBLE_MEMORY_HOT_TURNS` — cap on **messages** (not logical turns) kept hot (default `10`)
- `CRUCIBLE_MEMORY_DEDUP_THRESHOLD` — cosine ≥ this dedupes a candidate vs same kind+scope (default `0.92`) · `CRUCIBLE_MEMORY_RECALL_TOKEN_BUDGET` — cap on injected recall (default `1500`) · `CRUCIBLE_MEMORY_WEIGHTS` — `w_sem,w_lex,w_struct` (default `0.5,0.3,0.2`) · `CRUCIBLE_MEMORY_GRAPH_GROUNDING` (default on) · `CRUCIBLE_EMBEDDING_MODEL` (reused — default `BAAI/bge-small-en-v1.5`). Needs `pip install -e '.[memory]'` (sentence-transformers + numpy; sqlite-vec is a base dependency, always installed).

**Tool loop**
- `CRUCIBLE_TOOL_LOOP_ENABLED` — set to `0` or `false` to fall back to single-shot `create_patch()` (default: `true`)
- `CRUCIBLE_TOOL_RESULT_MAX_CHARS` — max chars of tool output injected into loop context (default: `4000`)
- `CRUCIBLE_RIPGREP_CMD` — path to ripgrep binary used by `search_code` (default: `rg`)
- `CRUCIBLE_SHELL_POLICY` — `ask` (default, every `run_command` surfaces an Accept-once / Accept-and-remember-this-workspace / Reject card) or `allow_all` (skip the gate; run any command). Per-task override via the `shell_policy` field on the task submission. Replaces the old `CRUCIBLE_SHELL_ALLOWLIST` (removed).
- `CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC` — seconds to wait for the user's command decision; `0` (default) = wait forever. On timeout the command is rejected (returned as a tool-result error so the agent adapts).
- `CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT` — workspace default for whether step diffs are auto-accepted (`true`, default) or surfaced for review (`false`). The submission payload's `step_review_auto_accept` field, when provided, overrides this; when omitted, the env value wins.

**Scope extension** (controls how out-of-scope file writes are handled)
- `CRUCIBLE_SCOPE_POLICY` — `strict` (auto-reject) | `ask` (pause + VS Code modal, **default via start-backend.sh**) | `auto` (silently approve + audit log)
- `CRUCIBLE_SCOPE_TRIGGER` — `any` (every out-of-scope file trips the gate, **default via start-backend.sh**) | `nearby` (only files in the same directory as a target or conventional names like `__init__.py`)
- `CRUCIBLE_SCOPE_REMEMBER` — `task` (approved files remembered for the rest of the task, **default**) | `none` (ask every time)
- `CRUCIBLE_SCOPE_TIMEOUT_SEC` — seconds before an `ask`-mode prompt auto-rejects (0 = wait forever, default)

**Important**: `start-backend.sh` always sets `ask` + `any` so every out-of-scope write shows a VS Code approval modal. The Python engine defaults (`strict` + `nearby`) are only relevant when the backend is started outside the script.

**Retrieval**
- `CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH` — path to index-snapshot.json (default: `<workspace>/.crucible/index-snapshot.json`)
- `CRUCIBLE_RETRIEVAL_MAX_AGE_SEC` — max snapshot age before auto-reindex (default: `900`)
- `CRUCIBLE_INDEXER_INDEX_CMD` — command template for auto-indexing (`{workspace}`, `{snapshot_path}`)

**Validation**
- `CRUCIBLE_VALIDATION_COMMANDS_JSON` — JSON array of validation commands to run after execution; overrides auto-detection

**Provider validate / context-window test**
- `CRUCIBLE_CONTEXT_TEST_TIMEOUT_SEC` — seconds `run_context_test` (the Settings panel's opt-in Test button, `agentd/providers/context_probe.py`) waits for the provider's answer before reporting a timeout (default `240`). Deliberately under undici's default `headersTimeout` (300000ms) — the settings webview's request travels through Node's `fetch`, and a backend timeout landing at or after 300s would race that client-side abort and could surface an opaque `UND_ERR_HEADERS_TIMEOUT` instead of this module's own "Provider did not respond within Ns" message.

### VS Code extension settings (package.json contributes.configuration)
- `crucible.backendBaseUrl` — default `http://127.0.0.1:8000`. Leave default for the managed backend; set explicitly to attach to a dev backend (e.g. `start-backend.sh`) instead — an explicit value always wins over managed spawn.
- `crucible.defaultMode` — `inline | file_edit | project_edit | autonomous`
- `crucible.pollIntervalMs` — default `2000`
- `crucible.managedRuntime.enabled` — default `true`. Kill-switch back to the pure dev flow (disable to skip install/spawn entirely and require an explicit `backendBaseUrl`).
- `crucible.policy.shell` — `ask | allow_all`, default `ask`. Becomes `CRUCIBLE_SHELL_POLICY` in the managed spawn's env only when explicitly set (otherwise `buildBackendEnv`'s default of `ask` stands).
- `crucible.policy.scope` — `strict | ask | auto`, default `ask`. Becomes `CRUCIBLE_SCOPE_POLICY`, same explicit-only override rule.
- `crucible.memory.enabled` / `crucible.memory.reranker` — booleans, default `true` since 2026-07-08. Become `CRUCIBLE_MEMORY_ENABLED` / `CRUCIBLE_MEMORY_RERANKER`; a change flags `restartRequired` in the settings panel.

---

## Debugging Methodology

### Trace the full call path before asserting how code works

Do not describe (or design against) a code path from a single function. **Trace it end to end first** — caller → callee → the actual strings/values sent to the boundary. A recurring failure mode: reading one builder (e.g. `build_planning_step_payload`) and assuming the rest, when the system prompt is built by a *separate* function (`format_planning_system_prompt`) and the two are passed independently to `generate_json(system_instructions=…, user_payload=…)`.

Worked example — where does the planner's `initial_context` (retrieval) actually live?
- `orchestrator/engine.py`: `retrieval_context.as_prompt_payload()` → `initial_context=` in `plan_context` (pinned to round-1 on feedback via `task.planning_initial_context`).
- `reasoning/engine.py::create_planning_step`: calls `format_planning_system_prompt(...)` (system string = prompt text + `tools_json` + `max_calls`, **no retrieval**) AND `build_planning_step_payload(...)` (user payload — retrieval lands here, early, with `instruction`/`budget_status` LAST for KV-cache stability).
- Both strings go to `generate_json` as separate args.

The lesson: the system prompt and the user payload are built by different functions and carry different things; verify which builder owns a field before reasoning about prompt structure, caching, or staleness. Use `_debug_dump` artifacts (`plan-turn-NN`) to see the exact bytes sent.

### Starting the backend for local testing

Always use `start-backend.sh` rather than running uvicorn directly — it sets all env vars correctly:

```bash
# From repo root — pick a workspace and provider
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
bash scripts/stress/start-backend.sh \
  --backend gemini \
  --workspace "$PWD/workspaces/crucible-stress" \
  --validation-profile none   # use 'full' when testing validation

# Verify it's up
curl -s http://127.0.0.1:8000/health   # needs no token
```

Log file lands in `.tmp/stress-<timestamp>/logs/agentd.log`. Tail it while running tasks:

```bash
tail -f .tmp/stress-*/logs/agentd.log | grep -v "GET /v1/tasks"   # filter out poll noise
```

### Opening the VS Code extension development host

```bash
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/crucible-stress"
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

Task ID is persisted to `/tmp/crucible-verify-state/current_task_id.txt` between stages.

### Inspecting a task mid-flight

Every request except `GET /health` needs the backend's token (see "Backend authentication"):

```bash
AUTH="Authorization: Bearer $(cat ~/.crucible/run/agentd-8000.token)"
TASK_ID=task-xxxx
curl -s -H "$AUTH" http://127.0.0.1:8000/v1/tasks/$TASK_ID | python3 -m json.tool
curl -s -H "$AUTH" http://127.0.0.1:8000/v1/tasks/$TASK_ID/result | python3 -m json.tool
```

### Watching the SSE stream directly

```bash
curl -sN --no-buffer -H "$AUTH" "http://127.0.0.1:8000/v1/tasks/$TASK_ID/stream-patch" \
  -H "Accept: text/event-stream"
```

Connect **before** approving the plan — the stream stays open through the entire execution. Event types:

| Event | When |
|-------|------|
| `planning_tool_call` | PlanningAgent calls a tool during exploration |
| `planning_tool_result` | Tool result returned to PlanningAgent |
| `planning_complete` | PlanningAgent emitted plan; includes `files_examined` and `confidence` |
| `tool_call` | Execution agent calls a tool within a step's ReAct loop |
| `tool_result` | Tool result returned to execution agent |
| `revision_needed` | Execution agent signalled plan is wrong; delta replan will fire |
| `operation_success` | A patch op applied successfully |
| `operation_error` | A patch op failed |
| `done` | Task reached a terminal state |

### Diagnosing a stuck task

| Symptom | Likely cause | Check |
|---------|-------------|-------|
| Stuck at `CONTEXT_READY` | PlanningAgent exploring (many tool calls) OR Gemini rate limit | Log: `[PLAN] PlanningAgent exploring` vs `Gemini transient error` |
| Stuck at `AWAITING_PLAN_APPROVAL` | Normal — waiting for user to approve/reject | Expected state; poll task status |
| Stuck at `PLANNED` after approval | Backend restarted mid-flight; orphan task | Start a new task or use `POST /resume` with `stage: execute` |
| Step loops in verify phase with non-zero exits | Pre-existing test failures keep SM in `TEST_FAILED` | Agent should run scoped test (e.g. `pytest tests/test_foo.py::test_bar`), not full suite — `_static_baseline` filters pre-existing errors from the postpatch comparison |
| `DUPLICATE PATCH BLOCKED` in tool history | SM dedup caught an exact-repeat `emit_patch` within a single state stay | Expected — model should read the file first; cache clears on the next transition |
| `VerifyPhaseExhausted` in logs | 5 consecutive engine failures from `PATCH_FAILED_CAN_RETRY` | Step attempt converted to `VerifyResult(verified=False)`; orchestrator retries step or marks failed per its retry policy |
| No `planning_tool_call` events but task advancing | Tool loop disabled (`CRUCIBLE_TOOL_LOOP_ENABLED=0`) | Single-shot patch mode active |
| SSE stream closes immediately | Replay buffer has stale `done`, or task is terminal | Check task status; start a new task |
| `revision_needed` event but task fails shortly after | Delta replan budget hit (`max_delta_replans`) | Check `delta_replans_used` in task execution_state |

### Asyncio race conditions to watch for

The orchestration engine is single-process asyncio. Key race windows:

- **`_running_tasks` vs SSE connect**: `run_task`/`continue_task` add to `_running_tasks` at their first line, but they run inside `asyncio.create_task()` — they don't start until the current coroutine yields. The route handler pre-adds `task_id` to `_running_tasks` before `create_task()` for the feedback route to close this window.
- **Replay buffer pollution**: `run_task` broadcasts `done` when pausing at `AWAITING_PLAN_APPROVAL`. This goes into the replay buffer and would cause any new SSE subscriber to close immediately. Fix: `clear_replay()` instead of `broadcast(done)` at the pause point.
- **`webview.html` coalescing**: VS Code coalesces rapid sequential writes to `webview.html` into one render. Use `postMessage` for incremental updates (patch events); only replace the full HTML on genuine state changes (status, result, files).

### Reading artifacts to understand what happened

Every task writes debug artifacts to `<workspace>/.crucible/state/artifacts/<task_id>/`. This is the primary source of ground truth when a task behaves unexpectedly — check here before guessing.

```
<task_id>/
  plan-evidence.json              # retrieval context fed to the planner
  planning-trace.json             # PlanningAgent tool call trace (initial plan)
  planning-trace-feedback.json    # PlanningAgent tool call trace (after user feedback)
  json-plan-draft.json            # raw output of markdown→JSON plan conversion
  plan.json                       # approved executable plan (PlanDocument)
  delta-replan-revision.json      # delta replan result when revision_needed fires
  full-validation.json            # validation output after all steps complete

  step-<id>/
    tool-trace.json               # execution agent tool call trace for this step
    attempt-<n>/
      patch-context.json          # full context sent for patch generation (tool-loop-disabled mode)
      patch.json                  # parsed patch candidate(s)
      preflight-<cN>.json         # preflight check result per candidate
      ranking.json                # candidate scoring and selection
```

**What to look at for each failure mode:**

| Problem | Artifact to read |
|---------|-----------------|
| Plan explores wrong files / misses key symbols | `planning-trace.json` — which tool calls did the planning agent make? What did it read? |
| Plan markdown looks correct but JSON plan is wrong | `json-plan-draft.json` — did the markdown→JSON conversion parse the steps correctly? |
| Step fails repeatedly with no progress | `step-<id>/tool-trace.json` — is the agent reading the right files before emitting? |
| Patch applies but logic is wrong | `step-<id>/tool-trace.json` — did the agent call `run_command` to verify before emitting? |
| `revision_needed` fires unexpectedly | `delta-replan-revision.json` — what evidence did the agent cite? What steps were revised? |
| Step fails preflight (file not found, policy violation) | `step-<id>/attempt-<n>/preflight-<cN>.json` |
| Wrong patch generated (bad search string) | `step-<id>/attempt-<n>/patch.json` + check `tool-trace.json` — did the agent read the file first? |
| Validation fails after patching | `full-validation.json` |

**Quick command to inspect the latest task's artifacts:**

```bash
TASK_ID=$(cat /tmp/crucible-verify-state/current_task_id.txt)
ARTIFACTS="<workspace>/.crucible/state/artifacts/$TASK_ID"

ls $ARTIFACTS
ls $ARTIFACTS/step-*/

cat $ARTIFACTS/planning-trace.json | python3 -m json.tool | less
cat $ARTIFACTS/step-s1/tool-trace.json | python3 -m json.tool
cat $ARTIFACTS/delta-replan-revision.json | python3 -m json.tool
```

The API also exposes artifacts:
```bash
curl -s -H "$AUTH" http://127.0.0.1:8000/v1/tasks/$TASK_ID/artifacts | python3 -m json.tool
```

### Provider-specific notes

- **Gemini**: use `gemini-flash-latest` (stable alias). Preview models have lower quota and hit 429s. Set `CRUCIBLE_GEMINI_TIMEOUT_SEC=600` — the default 120s is too short for the PlanningAgent loop (many tool calls).
- **Model env var**: `CRUCIBLE_GEMINI_MODEL` in `.env` must be on its own line — concatenating it with another var (no newline) silently breaks the export.
- Transient 429/503 errors retry automatically (up to 4 attempts, exponential backoff). A task stuck at `CONTEXT_READY` with log lines `Gemini transient error (attempt N/4)` is retrying — wait it out.
- **Anthropic**: constrained JSON decoding is not available (no `response_json_schema`). The schema is stringified into the system prompt instead. Expect slightly lower schema compliance rate; the discriminated union constraints help enforce it at the prompt level.
- **GOTCHA — `openai_transport.py` cannot be pointed at a custom endpoint.** It uses the **Responses API** (`client.responses`), which OpenAI-compatible servers do not serve (NVIDIA NIM returns 404 for `/v1/responses`, 401 for `/v1/chat/completions`). Use the `openai_compatible` backend for any non-OpenAI endpoint.
- **NVIDIA NIM (`https://integrate.api.nvidia.com/v1`)**: verified 2026-07-31 to support strict `response_format: json_schema` including `oneOf` discriminated unions, and to accept `max_completion_tokens`. `nvext.guided_json` is not needed. Free-tier **capacity is per-model, and is the real constraint — not capability**: `z-ai/glm-5.2` **does** support strict `json_schema` including `oneOf` unions (verified — 223.6s TTFB, discriminator honored), but is queue-bound on NVIDIA's free tier (~225s to first byte, and one probe died with an HTTP 504 after 302s). It is capable but not usable interactively there, since one Crucible turn makes many calls. `nvidia/nemotron-3-ultra-550b-a55b` answers in ~0.5s and is the model to actually use. Use a paid endpoint (Z.ai's own API is OpenAI-compatible — a base-URL change) if you want GLM-5.2's capability at usable latency.
- **Ollama** (qwen3-family models): qwen3 emits implicit thinking tokens even with no explicit `think` flag. When `format=<schema>` (structured output) is active, thinking can exhaust `num_predict` before the JSON is emitted, leaving `message.content` empty — and since Ollama has no separate reasoning-token budget (thinking + output share one `num_predict` pool, unlike e.g. OpenAI's `reasoning_effort`), this can happen at any budget size, not just a small one (confirmed live 2026-07-13: Ollama Cloud's `nemotron-3-super:cloud` exhausted a 16384-token budget — half of a 32768 `num_ctx` — purely on `<think>`). The `ChatAgent` explore phase deliberately omits conversation history from structured-output payloads to reduce context size and lower the probability of this.
  - `OllamaJsonTransport`'s JSON-call `num_predict` is `num_ctx × json_predict_frac` (defaults `32768 × 0.5`), not a flat constant — it scales with `num_ctx` instead of a second hardcoded number needing to stay in sync, and always leaves headroom below `num_ctx` for the prompt (the old flat-32768-for-both never actually gave the model the full 32768 for output). `num_ctx`/`json_predict_frac` are configurable via `CRUCIBLE_OLLAMA_NUM_CTX` (default `32768`) / `CRUCIBLE_OLLAMA_JSON_PREDICT_FRAC` (default `0.5`) so a bigger-context cloud model can be given more room without a code change. `generate_text` keeps a small fixed `num_predict=2048` to cap runaway answers, but now tracks the configured `num_ctx` too.
  - A per-instance `think` dial (bool or, for models like GPT-OSS that require it, a level string `"low"/"medium"/"high"/"max"`) is sent as a **top-level** request field (not inside `options`) via `CRUCIBLE_OLLAMA_THINK` — unset (default) omits the field entirely, preserving prior behavior. This was previously ripped out as a blanket `think=False` because qwen3 ignores the flag and emits implicit thinking regardless; it's back as opt-in-per-deployment (not a default) so an operator can test whether a *specific* model honors it (e.g. Nemotron) without risking a silent no-op regression for models that don't.
  - Two layers handle a provider call raising. (1) `controller_loop.py::_iterate` wraps the `create_controller_step` call in a `try/except Exception` that routes the failure through the SAME `consecutive_malformed`/`_MAX_MALFORMED` correct-and-continue mechanism already used for a parsed-but-semantically-invalid response (wrong type for phase, empty required field) — no separate retry primitive, just one more branch feeding the one existing counter, appending an inline correction carrying the exception text to history before the next attempt, since a parse/output failure is usually input-determined and retrying with unchanged history just reproduces it. (`PARSEFAIL_CORRECTION` in react_common.py is defined but STILL unused — the inline text is richer, since it interpolates the real error). `PlanningLoop` has an analogous but separately-implemented inner retry (`_MAX_STEP_RETRIES=2`) around `create_planning_step`; left as-is (not worth the regression risk to unify further). Most controller failures (a model returning empty content, unparseable JSON) self-correct within `_MAX_MALFORMED` and the turn never even notices. (2) Only once that's exhausted does `ControllerLoopExhausted` (message includes the original exception text) propagate up to `ChatController._run_loop`, which catches it in a generic `except Exception` alongside the existing `CancelledError` handler and ends the turn as a normal visible `"answer"` outcome (`⚠️ The turn failed and had to stop: <exc>`) instead of letting it die uncaught out of the SSE route — the pre-fix bug left zero failure signal anywhere in the UI (composer re-enabled via unrelated cleanup, but no error card/breadcrumb/toast). Live-verified 2026-07-13: an EDIT-phase call that legitimately overflowed its 16384-token output budget (a real large file, not thinking-exhaustion) hit layer 2 and rendered the graceful message in the actual VS Code webview.
- **Ollama — `start-backend.sh`**: run the script from the repo root that owns the `services/agentd-py` you want to test. If using `--agentd-dir` (worktree override), ensure `CRUCIBLE_WORKSPACE_PATH` is exported inside the env block — missing it causes `ChatAgent` to default to `cwd` (the agentd-py dir) instead of the workspace root.
