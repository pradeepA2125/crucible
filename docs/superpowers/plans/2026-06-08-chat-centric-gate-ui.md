# Chat-Centric Gate UI — Implementation Plan

> **For agentic workers:** Execute task-by-task with TDD. Steps use checkbox (`- [ ]`).

**Goal:** Make gate/plan interaction chat-thread-centric and state-driven so reloads and resume (task-id churn) never break the UI.

**Architecture:** The chat thread is the durable anchor. Backend exposes a per-thread `ThreadLiveState` (current task's status + the one active gate payload + current plan) via `GET /v1/chat/threads/{id}/live`. The VS Code controller tracks the *thread*, polls `/live`, renders gate/plan cards from state (replace-not-append, dedup), and binds every action to `live.active_task_id` at click-time.

**Tech Stack:** Python (FastAPI, Pydantic, pytest) backend; TypeScript (VS Code extension + editor-client, vitest) frontend.

**Spec:** `docs/superpowers/specs/2026-06-08-chat-centric-gate-ui-design.md`

---

## File Structure

**Backend (Phase 1):**
- `services/agentd-py/agentd/domain/models.py` — add `ChatThread.active_task_id`? No — ChatThread lives in `chat/models.py`. Add `TaskExecutionState.pending_validation`. Add `ThreadLiveState` + `PendingGate` models (new, in `chat/models.py`).
- `services/agentd-py/agentd/chat/models.py` — `ChatThread.active_task_id`; `ThreadLiveState`, `PendingGate`.
- `services/agentd-py/agentd/chat/storage.py` — persist `active_task_id`; `set_active_task(thread_id, task_id)`.
- `services/agentd-py/agentd/chat/live_state.py` — NEW: `resolve_thread_live_state(thread, task_store)` pure resolver.
- `services/agentd-py/agentd/orchestrator/engine.py` — set `pending_validation` in `_pause_for_validation_decision`; set thread `active_task_id` on `create_task_from_chat` + resume paths.
- `services/agentd-py/agentd/api/routes.py` — `GET /v1/chat/threads/{id}/live`.

**Frontend (Phase 2):**
- `apps/editor-client/src/contracts/task-contracts.ts` — `ThreadLiveStateSchema`, `PendingGateSchema`; `getThreadLiveState` on `BackendTaskClient`.
- `apps/editor-client/src/client/http-backend-client.ts` — implement `getThreadLiveState`.
- `apps/vscode-extension/src/controller.ts` — thread tracking, `/live` poll, render-from-state, action-by-live-task-id, dedup, 409-tolerance.
- `apps/vscode-extension/src/review-panel.ts` + webview HTML/CSS — beautiful gate/plan card rendering.

---

## PHASE 1 — Backend (thread self-describing)

### Task 1: `TaskExecutionState.pending_validation`

**Files:** Modify `agentd/domain/models.py`; Test `tests/test_planning_domain_models.py`

- [ ] **Step 1 — failing test**
```python
def test_execution_state_has_pending_validation_field():
    from agentd.domain.models import TaskExecutionState
    st = TaskExecutionState()
    assert st.pending_validation is None
    st.pending_validation = {"summary": "2 failed", "diagnostics": []}
    assert st.pending_validation["summary"] == "2 failed"
```
- [ ] **Step 2 — run, expect FAIL** (`AttributeError`/validation error)
- [ ] **Step 3 — implement:** in `TaskExecutionState` add:
```python
    pending_validation: dict[str, Any] | None = None  # validation gate payload (parity with pending_command_request)
```
- [ ] **Step 4 — run, PASS**
- [ ] **Step 5 — commit** `feat(models): persist validation gate payload on execution_state`

### Task 2: Thread `active_task_id` + store setter

**Files:** Modify `agentd/chat/models.py`, `agentd/chat/storage.py`; Test `tests/test_chat_storage.py` (or existing chat store test)

- [ ] **Step 1 — failing test**
```python
@pytest.mark.asyncio
async def test_set_active_task_persists(tmp_path):
    store = ChatThreadStore(str(tmp_path / "chat.sqlite3"))
    t = await store.create_thread(workspace="/w", title="x")
    await store.set_active_task(t.thread_id, "task-abc")
    again = await store.get_thread(t.thread_id)
    assert again.active_task_id == "task-abc"
```
(Match the actual `ChatThreadStore` API — confirm `create_thread`/`get_thread` names during execution; adapt the test to real signatures.)
- [ ] **Step 2 — run, expect FAIL**
- [ ] **Step 3 — implement:** `ChatThread.active_task_id: str | None = None`; persist in storage row/JSON; add `async def set_active_task(self, thread_id, task_id)`.
- [ ] **Step 4 — run, PASS**
- [ ] **Step 5 — commit** `feat(chat): track active_task_id per thread`

### Task 3: `ThreadLiveState` resolver (pure)

**Files:** Create `agentd/chat/live_state.py`, add models to `chat/models.py`; Test `tests/test_thread_live_state.py`

- [ ] **Step 1 — failing tests** (one per gate kind + no-task + plan):
```python
def _task(status, **es):
    from agentd.domain.models import TaskRecord, TaskStatus, TaskExecutionState
    return TaskRecord(task_id="t1", goal="g", workspace_path="/w",
                      status=TaskStatus(status),
                      execution_state=TaskExecutionState(**es),
                      plan_markdown="# Plan")

def test_command_gate():
    from agentd.chat.live_state import resolve_live_state
    t=_task("AWAITING_COMMAND_DECISION", pending_command_request={"command":"pytest"})
    ls=resolve_live_state("chat-1","t1",lambda _id:t)
    assert ls.pending_gate.kind=="command" and ls.pending_gate.payload["command"]=="pytest"

def test_step_gate():  # AWAITING_STEP_REVIEW -> pending_step_review -> kind "step"
def test_scope_gate(): # AWAITING_SCOPE_DECISION -> pending_scope_request -> kind "scope"
def test_validation_gate(): # AWAITING_VALIDATION_DECISION -> pending_validation -> kind "validation"
def test_plan_surfaced_only_on_awaiting_plan_approval():
def test_no_active_task_returns_nulls():
```
- [ ] **Step 2 — run, expect FAIL** (module missing)
- [ ] **Step 3 — implement** `PendingGate` + `ThreadLiveState` in `chat/models.py`; `resolve_live_state(thread_id, active_task_id, get_task)` in `live_state.py`:
```python
_GATE = {
    "AWAITING_COMMAND_DECISION": ("command", "pending_command_request"),
    "AWAITING_STEP_REVIEW": ("step", "pending_step_review"),
    "AWAITING_SCOPE_DECISION": ("scope", "pending_scope_request"),
    "AWAITING_VALIDATION_DECISION": ("validation", "pending_validation"),
}
def resolve_live_state(thread_id, active_task_id, get_task):
    if not active_task_id: return ThreadLiveState(active_task_id=None, status=None, pending_gate=None, plan=None)
    try: task = get_task(active_task_id)
    except KeyError: return ThreadLiveState(active_task_id=None, status=None, pending_gate=None, plan=None)
    status = str(task.status)
    gate = None
    if status in _GATE:
        kind, field = _GATE[status]
        raw = getattr(task.execution_state, field, None)
        payload = raw if isinstance(raw, dict) else (raw.model_dump() if raw is not None else {})
        gate = PendingGate(kind=kind, payload=payload)
    plan = None
    if status == "AWAITING_PLAN_APPROVAL" and task.plan_markdown:
        plan = {"task_id": task.task_id, "plan_markdown": task.plan_markdown}
    return ThreadLiveState(active_task_id=task.task_id, status=status, pending_gate=gate, plan=plan)
```
- [ ] **Step 4 — run, PASS**
- [ ] **Step 5 — commit** `feat(chat): ThreadLiveState resolver`

### Task 4: set `active_task_id` on task create/resume

**Files:** Modify `agentd/orchestrator/engine.py` (`create_task_from_chat`, resume paths) + `_pause_for_validation_decision`; Test `tests/test_orchestrator_chat_active_task.py`

- [ ] **Step 1 — failing test:** create_task_from_chat sets thread.active_task_id to the new task; resume_from_execute sets it to the child. (Use the chat store + engine wiring as in existing chat tests.)
- [ ] **Step 2 — run, expect FAIL**
- [ ] **Step 3 — implement:** after creating/resuming, call `chat_store.set_active_task(thread_id, new_task_id)`; in `_pause_for_validation_decision` set `task.execution_state.pending_validation = {"summary": ..., "diagnostics": [...]}` from the validation before the transition.
- [ ] **Step 4 — run, PASS**
- [ ] **Step 5 — commit** `feat(chat): point thread at current task on create/resume`

### Task 5: `GET /v1/chat/threads/{id}/live`

**Files:** Modify `agentd/api/routes.py`; Test `tests/test_chat_live_route.py`

- [ ] **Step 1 — failing test:** GET returns ThreadLiveState JSON for a thread whose active task is awaiting a gate; 404 unknown thread; nulls when no active task.
- [ ] **Step 2 — run, expect FAIL**
- [ ] **Step 3 — implement** route: load thread → `resolve_live_state(thread_id, thread.active_task_id, store.get)` → return `model_dump()`. Fallback: if `active_task_id` is None, resolve latest task with `chat_channel_id == f"chat:{thread_id}"`.
- [ ] **Step 4 — run, PASS**
- [ ] **Step 5 — commit** `feat(api): GET /chat/threads/{id}/live`

- [ ] **Phase 1 gate:** run full `pytest`; all green.

---

## PHASE 2 — Frontend (chat-centric controller + beautiful cards)

> Read first (during execution): `controller.ts`, `review-panel.ts` + webview HTML, `http-backend-client.ts`, `task-contracts.ts`, `SessionStore`. Write tasks against the real signatures found.

### Task 6: contracts + client `getThreadLiveState`
- [ ] Add `PendingGateSchema` (`kind` enum command|step|scope|validation, `payload` record) + `ThreadLiveStateSchema` to `task-contracts.ts`; add `getThreadLiveState(threadId): Promise<ThreadLiveState>` to `BackendTaskClient`.
- [ ] Implement in `http-backend-client.ts` (GET `/v1/chat/threads/${id}/live`, parse with schema).
- [ ] vitest: client parses a sample `/live` payload.
- [ ] commit.

### Task 7: controller renders gate/plan from `/live` (state-driven, dedup)
- [ ] Track `activeThreadId`; poll `getThreadLiveState(activeThreadId)` on the existing poll tick.
- [ ] Render exactly one card per `(thread, gate.kind)` / one plan card — **replace not append**; remove when `pending_gate`/`plan` is null.
- [ ] vitest (ControllerUI stub): poll with command gate → one card; second identical poll → still one (replaced); poll with null → removed.
- [ ] commit.

### Task 8: actions bind to `live.active_task_id` + 409-tolerance
- [ ] All decision/feedback/accept actions POST using the latest `live.active_task_id` (not `this.session.taskId`).
- [ ] Treat HTTP 409 from a decision as benign (no error toast; next poll reconciles).
- [ ] vitest: after a simulated resume (live.active_task_id changes), clicking a card calls the client with the NEW id; a 409 is swallowed.
- [ ] commit.

### Task 9: beautiful UI (webview cards)
- [ ] Restyle gate + plan cards in the webview: clear gate-kind header + icon, monospace command/diff where relevant, primary/secondary action buttons, pending/disabled states, subtle animation on appear/replace, dark/light theme via VS Code CSS variables (`var(--vscode-*)`).
- [ ] Manual visual check via the run skill / extension host; iterate until clean.
- [ ] commit.

- [ ] **Phase 2 gate:** `npm run -w @crucible/editor-client build`, `npm run -w @ai-editor/vscode-extension typecheck`, vitest green; manual UI smoke.

---

## Review passes (after implementation)
1. Spec-coverage pass — every spec requirement maps to a task (done below).
2. Re-read each changed file end-to-end for correctness + naming consistency.
3. Live end-to-end: resume a chat task, reload mid-gate, confirm card reappears and Accept works on the resumed (child) task.

## Spec coverage check
- Bugs #1/#5 (session) → Tasks 7–8 (no session dependency; actions by live id). ✓
- #2 (step render) → Tasks 3,5,7 (poll-driven). ✓
- #3 (resume) → Tasks 4,8 (active_task_id follows). ✓
- #4 (dup plan) → Task 7 (replace-not-append). ✓
- #6 (split surfaces) → Tasks 5,7 (one `/live` view). ✓
- #7 (wrong task) → Task 8 (bind to live id). ✓
