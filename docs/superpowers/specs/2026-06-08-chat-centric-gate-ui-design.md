# Chat-Centric, State-Driven Gate UI — Design

**Date:** 2026-06-08
**Status:** Approved (architecture) — pending spec review

## Problem

The VS Code extension's task-interaction layer is built around a single,
button-launched task (`this.session` + one `startStream`). Chat-driven tasks,
resume-spawned child task-ids, and webview/extension reloads are not first-class.
This produced a class of UI bugs (all observed live):

1. Gate Accept fails "No active session" — `clientForSession()` throws on null
   `this.session` (affects scope/validation/command decisions + accept/reject/feedback/resume).
2. Step-review card not rendered after reload — only created from the transient
   `step_review_requested` SSE event; no recovery if the stream wasn't live.
3. Resume → gates invisible — `startStream` stays subscribed to the *parent*
   task; the resume child's events broadcast on a channel with no UI subscriber.
4. Duplicate/stale plan cards — `planning_complete` *appends* a new `plan_card`
   each time; reload re-renders from history → duplicates; only the live one reacts.
5. Chat-driven tasks have no session — `this.session` is set only by `startTask()`;
   chat-created tasks never persist it → reload `sessionStore.load()` is null.
6. Gate cards split across two SSE consumers — command cards from the chat-message
   stream; step/scope from the review-panel `startStream`.
7. Actions target `this.session.taskId`, not the card's `taskId` — so on resume /
   multi-task, actions hit the wrong task. (This is why "feedback only works on the
   topmost card.")

**Systemic root cause:** the interaction model keys on an in-memory single task
session + transient SSE events, and is not rebuilt from durable state on
reload/resume.

## Principle

**The chat thread is the durable anchor; task ids churn under it (resume → new
child id).** Make the UI **chat-thread-centric and state-driven**: the UI tracks a
*thread*, the thread reports its *current task's* live state, and the UI renders
from that state. Resume can swap task ids freely — the thread view just follows.

## Architecture

### Backend — the thread becomes self-describing

1. **`ChatThread.active_task_id: str | None`** (`chat/models.py`) — the thread's
   current task. Set when a task is created or resumed *from* the thread; resume
   updates it to the child id. Persisted by `ChatThreadStore` (`chat/storage.py`).
   - Set in `engine.create_task_from_chat` (engine.py:1094) and the resume paths
     (`resume_from_execute` engine.py:712 / the `/resume` route when the parent has
     a `chat_channel_id`). Resolution fallback: latest task with
     `chat_channel_id == "chat:{thread_id}"` (covers pre-existing threads).

2. **`ThreadLiveState`** read model + endpoint **`GET /v1/chat/threads/{id}/live`**:
   ```
   ThreadLiveState {
     active_task_id: str | None
     status: TaskStatus | null
     pending_gate: { kind: "command"|"step"|"scope"|"validation", payload: dict } | null
     plan: { task_id: str, plan_markdown: str } | null
   }
   ```
   Resolver: load `active_task_id` → `TaskRecord`; map status → gate payload:
   - `AWAITING_COMMAND_DECISION` → `execution_state.pending_command_request`
   - `AWAITING_STEP_REVIEW` → `execution_state.pending_step_review`
   - `AWAITING_SCOPE_DECISION` → `execution_state.pending_scope_request`
   - `AWAITING_VALIDATION_DECISION` → `execution_state.pending_validation` (**new
     field**: validation gate payload is not currently persisted; add it in
     `_pause_for_validation_decision` (engine.py:654) for uniformity, mirroring the
     other `pending_*` fields).
   - `plan` populated when status is `AWAITING_PLAN_APPROVAL` (the actionable
     state); null otherwise. Past plans remain in the thread's message history
     (existing endpoint) — `live.plan` is only the *current, actionable* plan.
   No active task → all-null (UI shows just the conversation).

### Frontend — chat-centric, state-driven (`apps/vscode-extension`, `apps/editor-client`)

3. The controller tracks the **active thread id**, not a task session. The poller
   is re-pointed at **`GET /threads/{id}/live`**. Each poll re-renders the gate/plan
   card **from state** — exactly one live card per gate/plan, **replace not append**.
   SSE remains a latency hint, but **correctness comes from the poll**, so reloads
   and missed events self-heal.

4. **Actions resolve to `live_state.active_task_id` at click-time** — never
   `this.session.taskId`. command/step/scope/validation decisions + plan feedback +
   accept/reject all POST by the current task id derived from the thread's live state.

## Data flow

- **Gate appears:** orchestrator sets `pending_*` + `AWAITING_*` status on the task →
  thread's `/live` reflects it (active_task_id resolves to that task) → UI poll
  renders the gate card → user clicks → POST `/{decision}` by `live_state.active_task_id`.
- **Resume:** `/resume` creates child, sets `thread.active_task_id = child` → next
  `/live` poll returns the child's state → UI card now bound to the child. No UI
  reconnect needed.
- **Reload:** controller re-reads active thread id (persisted) → polls `/live` →
  re-renders current gate/plan. No dependency on prior in-memory session/SSE.

## Edge cases

- **Card dedup:** keyed by `(thread, gate-kind)`; a new `/live` with the same gate
  updates in place; a `/live` with `pending_gate == null` removes the card.
- **Gate race / already-decided:** if the user clicks a card whose gate the backend
  already resolved, the POST returns 409 "not awaiting…"; the UI treats 409 as
  benign (the next poll already shows the advanced state) — no error toast.
- **Resume mid-gate:** active_task_id flips to child; the parent's stale card is
  replaced by the child's live state on the next poll.
- **No active task / terminal:** `/live` returns nulls; UI shows conversation only.
- **Plan dedup (#4):** the plan card is rendered from `live.plan`, replacing any
  prior plan card for the thread — historical re-renders don't accumulate.

## Testing

**Backend (pytest):**
- `ThreadLiveState` resolver: each `AWAITING_*` status → correct `kind` + payload;
  no active task → nulls; plan surfaced when present.
- `active_task_id` set on `create_task_from_chat` and on resume (child id);
  fallback resolution by `chat_channel_id`.
- `pending_validation` persisted in `_pause_for_validation_decision`.
- `GET /threads/{id}/live` route returns the model; 404 unknown thread.

**Frontend (vitest):**
- Controller renders gate/plan card from a `/live` payload (one card per kind);
  second poll with same gate replaces; poll with `pending_gate=null` removes.
- Action posts by `live.active_task_id` (not `this.session.taskId`) — assert the
  client is called with the live task id after a simulated resume (id change).
- Reload simulation: fresh controller + persisted thread id → poll `/live` →
  card re-rendered with a working action (no "No active session").
- 409 on decision is swallowed (no error surfaced).

## Phasing

- **Phase 1 (backend):** `active_task_id`, `pending_validation`, `ThreadLiveState`
  + `/live` endpoint + set active_task_id on create/resume. Independently testable.
- **Phase 2 (frontend):** chat-centric controller (poll `/live`, render-from-state,
  action-by-live-task-id, dedup). Depends on Phase 1.

## Out of scope (YAGNI)

- Replacing SSE with polling entirely (SSE stays as a latency optimization).
- Multi-thread concurrent rendering (one active thread at a time, as today).
- Backend changes to the gate/decision *routes* themselves (they already accept
  POST-by-task_id; only the 409-tolerance is a UI concern).
