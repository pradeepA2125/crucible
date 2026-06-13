# Tier B — Task Lifecycle Control & Durable Telemetry (Design)

**Date:** 2026-06-13
**Branch:** `chat-ui-redesign` (worktree)
**Status:** Approved design — ready for implementation plan
**Supersedes the Tier B notes in:** `docs/superpowers/plans/2026-06-12-chat-ui-v2-handoff.md` (items 1, 2, 3, 5; item 4 deferred)

## Goal

Make the chat UI's lifecycle promises *true and durable*. Today three things are
hollow or ephemeral:

- **Stop** is only safe for chat turns; there is no cooperative abort for an executing task.
- **READY_FOR_REVIEW** is hollow — `_partial_promote` already wrote each completed step's
  files to the real workspace during execution, so the final promote re-copies and reject
  cannot revert (`engine.py:741` TODO).
- **ErrorCard / ReviewCard detail** lives only in extension memory and vanishes on reload —
  `TaskRecord` has no `failure_summary` / `run_summary`.

And one UX lie: the "Review each step" composer toggle is frozen into `TaskRecord.
step_review_auto_accept` at creation; flipping it mid-run does nothing.

## Keystone decision (everything inherits from this)

**Keep the partial-promote write model for live per-step feedback; add a single pinned
pre-execution checkpoint so reject and abort can perform a TRUE rollback.** Steps continue
landing in the real workspace per-step (the user sees edits in their editor as the run
progresses). We do NOT move to checkpoint-deferred writes. But we DO capture one pristine
checkpoint before step 1, which makes a real revert possible by reusing existing machinery.
Consequences, accepted:

- Final accept collapses to `SUCCEEDED` (drop the redundant final re-copy).
- **Reject performs a true rollback** to the pre-execution state (restore originals, delete
  task-created files) → `ABORTED`. Not "keep changes."
- Abort stops the run and offers a choice: **keep** what already partial-promoted, or
  **revert** to pre-execution. Aborting mid-step is safe because `_partial_promote` runs
  *after* a step returns — a mid-step abort promotes nothing of the in-flight step.

### Why a true revert is cheap here (reuses existing machinery)

`promote` (`workspace/shadow.py`) iterates `task.modified_files`: if a file exists in the
shadow it copies it over the real workspace; if it is *absent* from the shadow it **deletes**
it from the real workspace. So rolling the real workspace back is:

1. `_restore_shadow_checkpoint(shadow, pre_execution_checkpoint)` — shadow becomes pristine
   (task-created files disappear from it),
2. `promote(task)` — restores originals for modified files AND deletes the task-created ones.

This is an exact rollback with zero new copy/delete logic. The only new piece is pinning the
pre-execution checkpoint (the per-step checkpoint machinery — `_create_shadow_checkpoint`,
`execution_state.step_checkpoints`, `_restore_shadow_checkpoint` — already exists for
delta-replan; we add one baseline checkpoint exempt from pruning).

## Decisions summary

| # | Decision | Choice |
|---|----------|--------|
| 1 | Workspace-write model | Keep partial-promote (live feedback) + pinned pre-execution checkpoint for true revert |
| 2 | Abort granularity | Cooperative; checked between steps AND between ToolLoop iterations |
| 2b | Abort revert option | Stop offers **keep** or **revert to pre-execution** |
| 3 | Mid-task review preference | Full two-way dynamic (live-mutable per task) |
| 4 | Durable telemetry | Persist both `failure_summary` + `run_summary`; on FAILED write both |
| 5 | Final-review actions | **Finish** (keep → SUCCEEDED) vs **Discard all changes** (rollback → ABORTED) |
| 6 | Rollback mechanism | Reuse `_restore_shadow_checkpoint` + `promote` (handles copy AND delete via `modified_files`) |
| 7 | Structured plan steps at approval gate | **Deferred** (orthogonal, low value, extra LLM call) |

## Architecture

The four features share one new mechanism: a per-running-task **control channel** the
execution loop polls. Abort and the dynamic review preference are both live signals to a
running coroutine in a single-process asyncio engine, so they live in memory on the
orchestrator, not on the persisted record.

### Component 1 — `TaskControl` (the shared mechanism)

```python
@dataclass
class TaskControl:
    abort: asyncio.Event              # set by the abort route; polled by the loop
    abort_revert: bool                # if set when aborting, roll back to pre-execution checkpoint
    step_review_auto_accept: bool     # live-mutable; re-read before each step gate
```

- **Owns:** `AgentOrchestrator._task_controls: dict[str, TaskControl]`.
- **Lifecycle:** created when a task starts running (`run_task` / `resume_task`), removed at
  terminal state (in the same `finally` that already finalizes the run).
- **Concurrency:** single-process asyncio ⇒ check+set with no `await` between is race-safe
  (same pattern as `_in_flight_resume` / `_in_flight_feedback` in `build_router`).
- **What it does:** one channel for two live signals. **Interface:** routes call
  `control.abort.set()` / assign `control.step_review_auto_accept`; the loop reads them.
  **Depends on:** nothing new; lives entirely in the engine. Seed value of
  `step_review_auto_accept` comes from `TaskRecord.step_review_auto_accept` (creation-time
  default), then diverges live.

### Component 2 — Cooperative abort (F12)

- **Route:** `POST /tasks/{id}/abort {revert: bool}` → set `control.abort_revert = revert`
  then `control.abort.set()` for a running task. It does NOT touch the shadow or status —
  the coroutine owns those. `/cancel` stays as-is for queued/terminal tasks.
- **Loop checks:**
  - `_execute_plan` checks `control.abort.is_set()` at the top of each step.
  - `ToolLoop` checks between ReAct iterations and raises `TaskAborted`.
  - Because `_partial_promote` runs *after* a step returns, raising mid-step leaves
    **nothing half-promoted**; already-completed (promoted) steps stay in the real workspace
    unless the user chose revert.
- **ABORTED-aware unwind:** catch `TaskAborted` → if `control.abort_revert`, run the
  **rollback** (Component 3b: restore pre-execution checkpoint + `promote`) → then
  `transition(task, ABORTED)` **once** → save → clean shadow → breadcrumb (✗ reverted vs
  ✗ stopped). Invariant (CLAUDE.md gate lessons): no later save re-writes a stale status
  over `ABORTED` (the stale-object clobber bug). The caller holds the task reference; we
  mutate it in place, never re-fetch a divergent copy.
- **Shadow cleanup ordering:** cleanup happens *after* the coroutine acknowledges the abort
  (inside the unwind), never from the route — fixes the current `/cancel` hazard where the
  route frees the shadow while the coroutine still runs against it.
- **No state-machine change:** `_TRANSITIONS` already permits `ABORTED` from every running
  state (EXECUTING, VALIDATING, REPAIRING, PLANNED, all `AWAITING_*`).

### Component 3 — Final-review collapse + true revert (F8)

- `PROMOTING` stops re-copying files (already partial-promoted). It becomes **finalize only**:
  clean shadow, finalize `run_summary`, → `SUCCEEDED`. No state-machine change (the
  `READY_FOR_REVIEW → PROMOTING → SUCCEEDED` edges already exist).
- **Finish** (accept at READY_FOR_REVIEW) → finalize → `SUCCEEDED`.
- **Discard all changes** (reject at READY_FOR_REVIEW) → run the **rollback** (3b) →
  `ABORTED`. The `/reject` route triggers the rollback before the ABORTED transition.
- ReviewCard offers exactly two terminal actions: **Finish** and **Discard all changes**
  (the old "keep changes but abort" is dropped — see Decision 5).
- Same finalize-only treatment applies to the normal validation-pass path and the
  validation-accept gate path (`engine.py:741`): both drop the redundant final re-copy.

### Component 3b — Pre-execution checkpoint & rollback (the true-revert engine)

- **Capture:** at the start of `_execute_plan` (before step 1), create one pristine checkpoint
  via the existing `_create_shadow_checkpoint` and pin its path on
  `execution_state.pre_execution_checkpoint`. It is **exempt from `prune_checkpoints`** until
  the task reaches a terminal state (the per-step checkpoints may still be pruned).
- **Rollback (shared by reject and abort-revert):**
  1. `_restore_shadow_checkpoint(shadow, pre_execution_checkpoint)` → shadow becomes pristine.
  2. `promote(task)` → for each `modified_files` entry: restore the original if present in the
     pristine shadow, **delete from the real workspace** if absent (the task-created files).
- This reuses existing machinery entirely; no new copy/delete logic.
- **Resume nuance:** for a child created via `resume (execute)`, "pre-execution" is the state
  at *resume* start (the parent's promoted changes are already in the real workspace). Rollback
  therefore undoes only this run's steps, not the parent's — the intended semantic. The child
  captures its own pre-execution checkpoint at its `_execute_plan` start.

### Component 4 — Durable telemetry (F9 + F8 → item 3)

```python
class FailureSummary(BaseModel):
    step_id: str | None
    step_index: int | None     # "step 3 of 4"
    error_class: str           # e.g. "VerifyPhaseExhausted"
    message: str               # capped

class RunSummary(BaseModel):
    steps_completed: int
    steps_total: int
    deviations: list[str]      # scope extensions, delta replans, discarded steps, validation-accepted

# TaskRecord additions:
failure_summary: FailureSummary | None = None
run_summary: RunSummary | None = None
```

- **`run_summary` is finalized on EVERY terminal transition** (SUCCEEDED / FAILED / ABORTED),
  accumulated server-side during the run from `execution_state` (which already tracks
  `delta_replans_used`, scope approvals, discarded steps, validation-accepted).
- **`failure_summary` is additionally written on FAILED**, capturing the failing step +
  error class. **On a failure both are populated** so the ErrorCard shows the failure detail
  *and* the run-so-far context ("got through 2 of 4, one scope extension, then step 3 —
  VerifyPhaseExhausted").
- **Exposure:** `resolve_live_state` adds `failure_summary` when FAILED/ABORTED and
  `run_summary` whenever present; both also surface on `TaskResult` / `TaskView`.
- **Frontend:** ErrorCard ← `failure_summary` (+ `run_summary`); ReviewCard ← `run_summary`.
  The extension's ephemeral `runDeviations` / `lastStepStarted` / `lastPatchError` become a
  live supplement the durable copy supersedes on reload (keep for live-feel; no longer the
  source of truth).

### Component 5 — Dynamic review preference (item 5)

- **Route:** `POST /tasks/{id}/review-pref {auto_accept: bool}` → sets
  `control.step_review_auto_accept`.
- **Engine:** re-reads `control.step_review_auto_accept` before each step's gate decision,
  instead of the frozen `TaskRecord` value.
- **Edge case (pinned):** if a step gate is **currently pending** and the user flips to
  auto-accept, the `/review-pref` route resolves that pending gate as **accept** too
  (consistent intent) — it checks for a live `pending_step_review` and fires its decision
  future. Flipping the other way (→ review) only affects future steps.
- **Frontend:** the composer checkbox stays enabled during execution and posts to
  `/review-pref` both directions; the StepGate card gains an "Accept & auto-accept the rest"
  affordance that posts the same.

## Frontend touchpoints (extension + webview)

- Work-bar **Stop** shown during task execution → offers **Stop & keep** vs **Stop & revert**
  → `POST /abort {revert}` (F12). (Chat-turn Stop unchanged.)
- Dynamic **review checkbox** + StepGate "auto-accept the rest" → `POST /review-pref`.
- **ErrorCard** renders durable `failure_summary` (+ `run_summary`); **ReviewCard** renders
  durable `run_summary` and offers **Finish** vs **Discard all changes** (true revert).

## Implementation slices (one spec, sequenced)

The pre-execution checkpoint + rollback helper lands first (slices 2 and the abort in slice 3
reuse it); the control channel is introduced with abort (slice 3) and reused by the dynamic
pref (slice 5).

1. **Pre-execution checkpoint + rollback helper** (backend, Component 3b) — pin the baseline
   checkpoint, exempt it from pruning, and a `_rollback_to_pre_execution(task)` that restores
   + promotes. The shared revert engine for slices 2 and the abort in slice 3.
2. **Final-review collapse + true revert** (backend) — PROMOTING finalize-only; Finish →
   SUCCEEDED (no re-copy); Discard → rollback → ABORTED.
3. **Control channel + cooperative abort** (backend) + Stop (keep/revert) button (frontend) —
   abort reuses the slice-1 rollback when `revert`.
4. **Durable telemetry** (backend model + `/live` + `TaskResult`) + ErrorCard/ReviewCard
   durable render (frontend).
5. **Dynamic review preference** (backend re-read + route) + dynamic checkbox (frontend).

## Testing posture

- Python: integration-style with real `tmp_path` shadows and the scripted engine. Use
  `SQLiteTaskStore` (not `InMemoryTaskStore`) for any test that depends on store-returns-a-
  copy semantics / object-divergence (per CLAUDE.md) — notably the ABORTED-aware no-clobber
  test and the gate-resolve-on-pref-flip test.
- Key cases: abort between steps; abort mid-ToolLoop-iteration leaves nothing half-promoted;
  ABORTED-aware save does not clobber; shadow cleanup ordering; accept→SUCCEEDED performs no
  re-copy; **Discard→rollback restores modified files to originals AND deletes task-created
  files in the real workspace, then ABORTED**; **abort-revert performs the same rollback,
  abort-keep does not**; pre-execution checkpoint survives `prune_checkpoints`;
  `run_summary` finalized on all three terminal states; `failure_summary` + `run_summary`
  both present on FAILED; pref flip resolves a pending step gate.
- TypeScript: vitest for ErrorCard/ReviewCard durable render and the dynamic checkbox /
  Stop-during-execution controller paths.

## Invariants carried over (CLAUDE.md gate lessons)

- Gates and aborts clear/transition the **caller's** task object in place; never re-fetch a
  divergent copy and mutate that (stale-object clobber → card reappears on reload, 409 on
  re-action, invalid-transition crash).
- Decision routes only `future.set_result(...)`; they never mutate/persist the task, so the
  `await` is safe.

## Out of scope (explicitly deferred)

- **Checkpoint-deferred writes** — the keystone keeps partial-promote (live per-step edits);
  we do NOT defer all writes to the end. (True *revert* IS now in scope, via the pinned
  pre-execution checkpoint — see Component 3b.)
- **Structured plan steps at the approval gate** (item 4) — orthogonal (plan-conversion
  timing), low value, costs a pre-approval LLM call.
- **Mid-iteration abort of non-task ToolLoops** (inline change / chat) — abort applies to
  task execution; chat-turn Stop already works via SSE disconnect.
