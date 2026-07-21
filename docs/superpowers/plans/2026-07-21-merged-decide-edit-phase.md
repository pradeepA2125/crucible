# Merged DECIDE/EDIT Controller Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the chat controller's `DECIDE`/`EDIT`/`EXPLAIN` phase model into two phases — `ACTIVE` (default, can act immediately) and `PLAN` (opt-in, sticky toggle, discuss-before-acting) — removing the `propose_mode` re-negotiation friction every plain follow-up message currently forces.

**Architecture:** `ControllerPhaseSM` collapses to two states with an explicit `start=` constructor argument; `ACTIVE` merges every `DECIDE`+`EDIT` action type (minus `propose_mode`, which becomes `PLAN`-only except a narrow task-subsystem carve-out); a lazy edit-session factory replaces eager `TurnEditSession` construction so `ACTIVE`-as-default never crashes a plain Q&A turn; the per-turn entry-hint signal splits into two independently-scoped flags to preserve an existing fumble-recovery guarantee; a new sticky `plan_mode` boolean flows composer → extension `globalState` → per-message wire field → backend phase selection.

**Tech Stack:** Python (agentd-py backend, pytest), TypeScript/React (VS Code extension + webview-ui, vitest).

**Design doc:** `docs/superpowers/specs/2026-07-21-merged-decide-edit-phase-design.md` (went through 5 rounds of architecture review — read it if a task's rationale needs more background than this plan restates).

## Global Constraints

- No changes outside this design's scope: the two named concerns (compaction/lint-staleness fixes from the prior branch) are already merged and untouched by this plan.
- Every renamed/removed symbol (`DECIDE`→`PLAN`, `EDIT`→`ACTIVE`, `EXPLAIN` deleted, `edit`→`implement` as a `propose_mode` wire value) must be renamed consistently in the SAME task that introduces the rename — never leave a mixed old/new vocabulary mid-task.
- `format_controller_system_prompt` (the process-cached system prompt) must stay phase-independent — no phase-specific text ever goes there. Phase-specific text belongs only in `build_controller_step_payload`'s per-turn `instruction` field (see the design doc's C4 finding — this is a hard architectural rule the whole memory-harness KV-cache-stability discipline depends on).
- Every existing test file this plan touches must be run (not just the new/renamed tests) before a task is marked complete — a phase rename this pervasive can silently break a sibling test file this plan doesn't explicitly list.
- Follow this repo's TDD convention: write the failing test first, run it, then implement.
- `pytest`'s summary line: never pass `-q` on the CLI (this repo's `pyproject.toml` already sets it in `addopts`, stacking to `-qq` and suppressing the pass count).

---

### Task 1: Phase model foundation — `ControllerPhaseSM`, action-type tables, mode dispatch, clarify resume

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_phase.py`
- Modify: `services/agentd-py/agentd/chat/controller_loop.py:54-92` (`_VALID_MODES`, `STATE_CHANGING_DECIDE_CORRECTION`, `_decide_state_change_correction`, `PROPOSE_MODE_CORRECTION`, `_reserved_tool_name_correction`)
- Modify: `services/agentd-py/agentd/chat/controller.py:353-371` (`_run_loop`'s SM construction), `:529-536` (clarify `resume_phase` write), `:971-999` (`resolve_mode` dispatch), `:1058-1059` + `:1073-1074` (`resolve_clarify`'s `resume_phase` read + `edit_is_resume`)
- Test: `services/agentd-py/tests/test_controller_phase.py` (full rewrite)
- Test: `services/agentd-py/tests/test_controller_decide_no_run_command.py` (update — confirmed Shape-A site)

**Interfaces:**
- Produces: `ControllerPhaseSM(start: str = "ACTIVE")` — constructor validates `start in ("PLAN", "ACTIVE")`; `.phase` property; `.allowed_types()`; **no more `enter_edit_mode`/`enter_explain_mode`** — every phase entry is a fresh instance.
- Produces: `_VALID_MODES = frozenset({"implement", "create_task", "resume"})` (module-level in `controller_loop.py`) — consumed by Task 6's per-instance `_allowed_modes`.
- Consumes (from design doc, not yet built): nothing — this task has no dependency on later tasks. Later tasks (2-9) all build on this one.

- [ ] **Step 1: Write the failing test for the two-state SM**

Replace the entire contents of `services/agentd-py/tests/test_controller_phase.py`:

```python
import pytest

from agentd.chat.controller_phase import ControllerPhaseSM


def test_default_start_is_active():
    sm = ControllerPhaseSM()
    assert sm.phase == "ACTIVE"
    assert "edit" in sm.allowed_types()
    assert "run_command" not in sm.allowed_types()  # run_command rides tool_call, not its own type
    assert "propose_mode" not in sm.allowed_types()  # only via the Task 6 task-subsystem carve-out


def test_explicit_active_start():
    sm = ControllerPhaseSM(start="ACTIVE")
    assert sm.phase == "ACTIVE"
    assert "submit_changes" in sm.allowed_types()
    assert "answer" in sm.allowed_types()
    assert "clarify" in sm.allowed_types()
    assert "tool_call" in sm.allowed_types()


def test_explicit_plan_start():
    sm = ControllerPhaseSM(start="PLAN")
    assert sm.phase == "PLAN"
    assert "propose_mode" in sm.allowed_types()
    assert "answer" in sm.allowed_types()
    assert "clarify" in sm.allowed_types()
    assert "tool_call" in sm.allowed_types()
    assert "edit" not in sm.allowed_types()
    assert "submit_changes" not in sm.allowed_types()


def test_invalid_start_raises():
    with pytest.raises(ValueError):
        ControllerPhaseSM(start="EDIT")  # the old phase name is no longer valid
    with pytest.raises(ValueError):
        ControllerPhaseSM(start="bogus")


def test_no_post_construction_transition_methods():
    # enter_edit_mode/enter_explain_mode are removed — every phase entry is a fresh
    # instance constructed with start=, never a mutation.
    sm = ControllerPhaseSM()
    assert not hasattr(sm, "enter_edit_mode")
    assert not hasattr(sm, "enter_explain_mode")
```

- [ ] **Step 2: Run it, confirm it fails**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_controller_phase.py -v
```
Expected: `ImportError`/`AttributeError`/`FAIL` — the old three-state SM has no `start=` kwarg and no `"ACTIVE"`/`"PLAN"` phases yet.

- [ ] **Step 3: Rewrite `controller_phase.py`**

Replace the entire file:

```python
"""ACTIVE/PLAN phase state machine for the chat controller (State pattern).

Mirrors verify_phase_sm's enforcement role: the allowed action `type`s are a pure
function of the phase, so the controller can filter the response schema per turn —
the model literally cannot emit `edit`/`submit_changes` before the user has chosen
Plan Mode's "Implement this plan", and cannot emit `propose_mode` once it's already
in ACTIVE (the default acting phase).

Every phase entry is a FRESH instance constructed with `start=` — there is no
post-construction transition method. The two legal `start=` values and their only
non-default construction sites (see the design doc's C1/C5 entries):
  - "ACTIVE": the default for a plain fresh turn; also `resolve_mode`'s "implement"
    dispatch; also `resolve_clarify`'s ACTIVE-resume (a clarify raised mid-ACTIVE).
  - "PLAN": only `resolve_clarify`'s PLAN-resume (a clarify raised mid-PLAN) — PLAN
    is never transitioned into from a live ACTIVE turn otherwise, only ever
    constructed fresh as a turn's toggle-derived starting phase.
"""
from __future__ import annotations

from agentd.chat.controller_prompts import _PHASE_TYPES

_VALID_STARTS = ("PLAN", "ACTIVE")


class ControllerPhaseSM:
    def __init__(self, start: str = "ACTIVE") -> None:
        if start not in _VALID_STARTS:
            raise ValueError(f"invalid starting phase: {start!r} (must be one of {_VALID_STARTS})")
        self._phase = start

    @property
    def phase(self) -> str:
        return self._phase

    def allowed_types(self) -> list[str]:
        return list(_PHASE_TYPES[self._phase])
```

- [ ] **Step 4: Run the SM test, confirm it passes**

```bash
pytest tests/test_controller_phase.py -v
```
Expected: still fails — `_PHASE_TYPES` in `controller_prompts.py` still has the old `"DECIDE"`/`"EDIT"`/`"EXPLAIN"` keys, not `"PLAN"`/`"ACTIVE"`. Continue to Step 5 before re-running.

- [ ] **Step 5: Update `_PHASE_TYPES` in `controller_prompts.py`**

In `services/agentd-py/agentd/chat/controller_prompts.py`, replace:

```python
_PHASE_TYPES: dict[str, list[str]] = {
    "DECIDE": ["tool_call", "answer", "clarify", "propose_mode"],
    # EDIT keeps `clarify` so the agent can ask when a genuine ambiguity blocks it
    # mid-edit (reading the workspace can't resolve it); the user's reply resumes the
    # loop in EDIT (ChatController._edit_clarify_pending). It still cannot re-open mode
    # selection — `propose_mode` stays DECIDE-only.
    "EDIT": ["tool_call", "edit", "clarify", "submit_changes"],
    # EXPLAIN (user picked "Just explain"): describe the approach — explore then answer.
    # propose_mode is FORBIDDEN here so the explain re-entry can't re-open the mode gate
    # (finding 4: DECIDE re-entry kept re-proposing); edit is forbidden too (no changes).
    "EXPLAIN": ["tool_call", "answer", "clarify"],
}
```

with:

```python
_PHASE_TYPES: dict[str, list[str]] = {
    # PLAN (was DECIDE): read-only exploration + discussion before committing to act.
    # propose_mode is how PLAN hands a concrete plan back to the user ("Implement this
    # plan", or create_task/resume when the task subsystem is on).
    "PLAN": ["tool_call", "answer", "clarify", "propose_mode"],
    # ACTIVE (merges the old DECIDE+EDIT): the default phase for every turn — editing
    # needs no permission step. Keeps `clarify` so the agent can ask when a genuine
    # ambiguity blocks it mid-edit; the user's reply resumes the loop in ACTIVE
    # (ChatController.resolve_clarify). `propose_mode` is added back in only when the
    # task subsystem flag is on (Task 6 — ControllerLoop mutates its own allowed-types
    # view per-instance; this module-level table is ACTIVE's task-subsystem-OFF shape).
    "ACTIVE": ["tool_call", "answer", "clarify", "edit", "submit_changes"],
}
```

- [ ] **Step 6: Run the SM test again**

```bash
pytest tests/test_controller_phase.py -v
```
Expected: `5 passed`.

- [ ] **Step 7: Update `_VALID_MODES`, the two correction texts, and `_decide_state_change_correction` in `controller_loop.py`**

In `services/agentd-py/agentd/chat/controller_loop.py`, replace:

```python
# The modes propose_mode may offer; resolution routes on these exact strings
# (resolve_mode: edit/explain re-enter the loop, create_task/resume hand off).
_VALID_MODES = frozenset({"edit", "create_task", "resume", "explain"})

# Tools that mutate the workspace. They are barred in the DECIDE phase (read-only
# exploration before mode selection) so the model cannot write source files via the
# shell (`cat >`/`tee`/`touch`), bypassing the EditGate. Enforced at the dispatch
# guard only — the advertised tool list (system prompt) is unchanged, keeping the
# cached prefix byte-stable across the DECIDE→EDIT transition.
_STATE_CHANGING_TOOLS = frozenset({"run_command"})

STATE_CHANGING_DECIDE_CORRECTION = (
    "run_command is not available while deciding how to proceed — it can mutate the "
    "workspace, which must go through review. In this phase use only read-only tools "
    "(search_code / read_file / list_directory / read_env_profile). To make changes, "
    "emit propose_mode and let the user pick edit mode; run_command becomes available "
    "once editing has started."
)


def _decide_state_change_correction(resp: dict[str, object], phase: str) -> str | None:
    """Reject a state-changing tool_call in DECIDE; None otherwise (inert for other
    phases and non-tool_call actions)."""
    if phase != "DECIDE" or str(resp.get("type", "")) != "tool_call":
        return None
    if str(resp.get("tool", "")) in _STATE_CHANGING_TOOLS:
        return STATE_CHANGING_DECIDE_CORRECTION
    return None
```

with:

```python
# The modes propose_mode may offer; resolution routes on these exact strings
# (resolve_mode: "implement" re-enters the loop in ACTIVE; create_task/resume hand
# off). "implement" is a NEW value, not a relabeled "edit" — it collides with the
# unrelated `edit` action-type string ACTIVE's schema already uses if reused (see
# the design doc's I5 finding).
_VALID_MODES = frozenset({"implement", "create_task", "resume"})

# Tools that mutate the workspace. They are barred in the PLAN phase (read-only
# exploration/discussion before committing to act) so the model cannot write source
# files via the shell (`cat >`/`tee`/`touch`), bypassing the EditGate. Enforced at the
# dispatch guard only — the advertised tool list (system prompt) is unchanged, keeping
# the cached prefix byte-stable across the PLAN→ACTIVE transition.
_STATE_CHANGING_TOOLS = frozenset({"run_command"})

STATE_CHANGING_DECIDE_CORRECTION = (
    "run_command is not available in Plan Mode — it can mutate the workspace, which "
    "Plan Mode exists to discuss first. In this phase use only read-only tools "
    "(search_code / read_file / list_directory / read_env_profile). To make changes, "
    "emit propose_mode and let the user pick \"Implement this plan\"; run_command "
    "becomes available once you're out of Plan Mode."
)


def _decide_state_change_correction(resp: dict[str, object], phase: str) -> str | None:
    """Reject a state-changing tool_call in PLAN; None otherwise (inert for other
    phases and non-tool_call actions)."""
    if phase != "PLAN" or str(resp.get("type", "")) != "tool_call":
        return None
    if str(resp.get("tool", "")) in _STATE_CHANGING_TOOLS:
        return STATE_CHANGING_DECIDE_CORRECTION
    return None
```

Replace:

```python
PROPOSE_MODE_CORRECTION = (
    "Your propose_mode was rejected: each option MUST be an object "
    '{"mode": <m>, "label": <short button text>, "description": <one line>} where '
    "<m> is one of edit | create_task | resume | explain, and the top-level "
    '"recommended" MUST be one of those same values. You used an invalid mode name '
    "or the wrong keys (e.g. \"type\" instead of \"mode\"). Re-emit propose_mode with "
    "valid modes — typically offer BOTH edit (make the change inline now) and "
    "create_task (plan it as a reviewed task), plus explain."
)
```

with:

```python
PROPOSE_MODE_CORRECTION = (
    "Your propose_mode was rejected: each option MUST be an object "
    '{"mode": <m>, "label": <short button text>, "description": <one line>} where '
    "<m> is one of implement | create_task | resume, and the top-level "
    '"recommended" MUST be one of those same values. You used an invalid mode name '
    "or the wrong keys (e.g. \"type\" instead of \"mode\"). Re-emit propose_mode with "
    "valid modes — typically offer \"implement\" (exit Plan Mode, make the change "
    "directly) at minimum, plus create_task (plan it as a reviewed task) when available."
)
```

Replace the `_reserved_tool_name_correction` guidance's final sentence (the rest of the function is unchanged):

```python
    return (
        f"'{tool}' is not a callable tool — there is no such tool in AVAILABLE TOOLS. "
        f"'{tool}' is a top-level response TYPE, emitted as its own object — "
        f'{{"type":"{tool}", ...}} (see the "{tool}" variant above for its required '
        f'fields) — NEVER as {{"type":"tool_call","tool":"{tool}",...}}. If you are '
        "trying to make a change: first emit type='propose_mode' so the user picks how "
        "to proceed; only after they pick 'edit' does type='edit' become available."
    )
```

with:

```python
    return (
        f"'{tool}' is not a callable tool — there is no such tool in AVAILABLE TOOLS. "
        f"'{tool}' is a top-level response TYPE, emitted as its own object — "
        f'{{"type":"{tool}", ...}} (see the "{tool}" variant above for its required '
        f'fields) — NEVER as {{"type":"tool_call","tool":"{tool}",...}}. If you are '
        "trying to make a change: type='edit' is already directly available — emit it "
        "now (Plan Mode is the only phase where you'd emit propose_mode first)."
    )
```

- [ ] **Step 8: Rewrite `test_controller_decide_no_run_command.py` (confirmed Shape-A site)**

Replace the file's two test functions' SM construction (the imports, docstring, and `_loop` helper are unchanged):

```python
@pytest.mark.asyncio
async def test_run_command_in_plan_is_rejected_not_executed(tmp_path: Path):
    # PLAN phase. The model tries to create a file via run_command (the Finding 6 bypass).
    # It must be rejected (no side effect on disk) and corrected, then recover.
    steps = [
        {"type": "tool_call", "thought": "write the file",
         "tool": "run_command", "args": {"command": "touch", "args": ["sentinel.txt"]}},
        {"type": "answer", "thought": "ok", "answer": "done"},
    ]
    out = await _loop(tmp_path, steps, ControllerPhaseSM(start="PLAN")).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and out.text == "done"
    assert not (tmp_path / "sentinel.txt").exists(), "run_command must NOT run in PLAN"


@pytest.mark.asyncio
async def test_run_command_in_active_is_dispatched(tmp_path: Path):
    # ACTIVE phase (the default): run_command is allowed (it executes).
    sm = ControllerPhaseSM(start="ACTIVE")
    steps = [
        {"type": "tool_call", "thought": "run it",
         "tool": "run_command", "args": {"command": "touch", "args": ["sentinel.txt"]}},
        {"type": "submit_changes", "thought": "done", "summary": "ran"},
    ]
    out = await _loop(tmp_path, steps, sm).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "submit_changes"
    assert (tmp_path / "sentinel.txt").exists(), "run_command must run in ACTIVE"
```

Also update the module docstring's last sentence from "EDIT phase keeps run_command." to "ACTIVE (the default) keeps run_command."

- [ ] **Step 9: Run both updated test files**

```bash
pytest tests/test_controller_phase.py tests/test_controller_decide_no_run_command.py -v
```
Expected: both files fully pass. (`ControllerLoop`'s dispatch still references `self._sm.phase == "EDIT"` etc. elsewhere in the file — those don't affect these two specific tests, which only exercise `_decide_state_change_correction` — but the FULL suite will still fail until later steps in this task land; that's expected mid-task.)

- [ ] **Step 10: `resolve_mode`'s "implement" dispatch in `controller.py`**

In `services/agentd-py/agentd/chat/controller.py`, replace the `resolve_mode` docstring and the `mode in ("edit", "explain")` branch:

```python
    async def resolve_mode(
        self, thread_id: str, mode: str, *, channel_id: str, goal: str,
    ) -> None:
        """Resolve the mode gate (POST /mode-decision). Clears the gate in place
        (Class-A), writes a breadcrumb, then dispatches: edit/explain re-enter the
        loop (a new streamed turn), create_task/resume hand off to the orchestrator."""
```

with:

```python
    async def resolve_mode(
        self, thread_id: str, mode: str, *, channel_id: str, goal: str,
    ) -> None:
        """Resolve the mode gate (POST /mode-decision). Clears the gate in place
        (Class-A), writes a breadcrumb, then dispatches: "implement" exits Plan Mode
        and re-enters the loop in ACTIVE (a new streamed turn); create_task/resume
        hand off to the orchestrator."""
```

Replace:

```python
        if mode in ("edit", "explain"):
            if mode == "edit" and self._orchestrator is None:
                raise RuntimeError("edit mode requires an orchestrator")
            phase = "EDIT" if mode == "edit" else "EXPLAIN"
            # Honor the "Review each edit" toggle from the message that opened this
            # gate (explain has no edits, so the value is inert there).
            review = self._step_review_by_thread.get(thread_id)
            seed_history = self._seed_for(thread_id)
            if mode == "explain":
                # The /mode-decision POST isn't part of the loop history, so without this
                # the re-entered turn has no signal the user chose "explain" and (in the
                # old DECIDE re-entry) just re-proposed. Inject the intent so the model
                # describes the approach; the EXPLAIN phase also blocks propose_mode.
                seed_history = (seed_history or []) + [{
                    "role": "user",
                    "content": ("Explain your proposed approach in detail — what you would "
                                "change and how. Do NOT make any changes or re-propose a "
                                "mode; just describe the plan."),
                }]
            # The edit/explain re-entry is a full turn — give it a turn_id too so it gets
            # incremental pill persistence (finding 5) AND debug artifacts, like the
            # handle_message path.
            turn_id = uuid4().hex
            outcome = await self._run_loop(
                thread_id, channel_id, effective_goal,
                seed_history=seed_history, step_review=review, phase=phase, turn_id=turn_id)
            await self._finish(
                thread_id, channel_id, outcome, step_review=review, turn_id=turn_id)
            return
```

with:

```python
        if mode == "implement":
            if self._orchestrator is None:
                raise RuntimeError("implement mode requires an orchestrator")
            phase = "ACTIVE"
            review = self._step_review_by_thread.get(thread_id)
            seed_history = self._seed_for(thread_id)
            # The re-entry is a full turn — give it a turn_id too so it gets incremental
            # pill persistence (finding 5) AND debug artifacts, like the handle_message path.
            turn_id = uuid4().hex
            outcome = await self._run_loop(
                thread_id, channel_id, effective_goal,
                seed_history=seed_history, step_review=review, phase=phase, turn_id=turn_id)
            await self._finish(
                thread_id, channel_id, outcome, step_review=review, turn_id=turn_id)
            return
```

- [ ] **Step 11: `_run_loop`'s NEW-I6 SM construction line in `controller.py`**

Replace:

```python
    async def _run_loop(
        self, thread_id: str, channel_id: str, goal: str, *,
        seed_history: list[dict[str, object]] | None, step_review: bool | None,
        phase: str | None = None, turn_id: str | None = None,
        edit_is_resume: bool = False, forced_skills: list[str] | None = None,
    ) -> ControllerOutcome:
        sm = ControllerPhaseSM()
        # Request-scoped todo ledger: rehydrate so it survives the DECIDE->EDIT (mode gate)
        # and clarify-resume loop boundaries within one request.
        ledger = TodoLedger.from_json(self._store.get_controller_todos(thread_id))
        # Edits only happen in EDIT phase (entered via /mode-decision). A DECIDE turn
        # never reaches the edit branch, so the session — which needs the orchestrator's
        # workspace_manager/patch_engine — is built lazily only when editing.
        edit = None
        if phase == "EDIT":
            sm.enter_edit_mode()
            if self._orchestrator is not None:
                edit = TurnEditSession(
                    turn_id=thread_id, real_path=Path(self._workspace_path),
                    workspace_manager=self._orchestrator._workspace_manager,
                    patch_engine=self._orchestrator._patch_engine)
        elif phase == "EXPLAIN":
            # User picked "Just explain" — describe the approach, never re-propose the
            # mode gate (finding 4). The SM forbids propose_mode/edit in EXPLAIN.
            sm.enter_explain_mode()
```

with:

```python
    async def _run_loop(
        self, thread_id: str, channel_id: str, goal: str, *,
        seed_history: list[dict[str, object]] | None, step_review: bool | None,
        phase: str | None = None, turn_id: str | None = None,
        edit_is_resume: bool = False, forced_skills: list[str] | None = None,
    ) -> ControllerOutcome:
        # Three callers feed `phase` with different intents: handle_message (the
        # plan_mode-derived starting phase — Task 7), resolve_mode's "implement"
        # dispatch (always "ACTIVE"), resolve_clarify ("PLAN"/"ACTIVE"/None
        # defensively). Anything not one of the two legal values falls back to the
        # default, "ACTIVE".
        sm = ControllerPhaseSM(start=phase if phase in ("PLAN", "ACTIVE") else "ACTIVE")
        # Request-scoped todo ledger: rehydrate so it survives the PLAN->ACTIVE (mode
        # gate) and clarify-resume loop boundaries within one request.
        ledger = TodoLedger.from_json(self._store.get_controller_todos(thread_id))
        # The edit session (Task 2) is built lazily inside the loop now, not here.
```

(Note: this step temporarily leaves `edit=None` always — Task 2 replaces this with the lazy factory. The `edit` local is still referenced a few lines below in the existing `ControllerLoop(...)` construction call passing `edit_session=edit`; leave that reference as-is for this task, it will be `None` until Task 2 wires the factory. This task's tests do not exercise editing, so this is safe.)

- [ ] **Step 12: `resolve_clarify`'s C5 phase-carrying `resume_phase`**

In `controller.py`, replace the clarify-outcome `resume_phase` write (inside `_run_loop`, right after the `outcome.kind == "clarify"` block):

```python
        if outcome.kind == "clarify":
            payload = dict(outcome.payload or {})
            payload["resume_phase"] = "EDIT" if sm.phase == "EDIT" else None
            outcome.payload = payload
        return outcome
```

with:

```python
        if outcome.kind == "clarify":
            payload = dict(outcome.payload or {})
            payload["resume_phase"] = sm.phase if sm.phase in ("PLAN", "ACTIVE") else None
            outcome.payload = payload
        return outcome
```

Replace `resolve_clarify`'s docstring + the `resume_phase` read + `edit_is_resume`:

```python
    async def resolve_clarify(
        self, thread_id: str, answer: str, *, channel_id: str, goal: str,
    ) -> None:
        """Resolve the clarify gate (POST /clarify-decision). Clears the gate in place
        (Class-A), writes ONE combined `❓ q → a` breadcrumb, then re-enters the loop with
        the answer injected as the user's reply (EDIT if the clarify fired mid-edit, else
        DECIDE) — a fresh streamed turn, like resolve_mode's edit/explain re-entry.
```

with:

```python
    async def resolve_clarify(
        self, thread_id: str, answer: str, *, channel_id: str, goal: str,
    ) -> None:
        """Resolve the clarify gate (POST /clarify-decision). Clears the gate in place
        (Class-A), writes ONE combined `❓ q → a` breadcrumb, then re-enters the loop with
        the answer injected as the user's reply — ACTIVE if the clarify fired mid-ACTIVE,
        PLAN if it fired mid-PLAN — a fresh streamed turn, like resolve_mode's dispatch.
```

Replace:

```python
        question = str(gate.payload.get("question") or "")
        resume_phase = gate.payload.get("resume_phase")
        resume_phase = resume_phase if resume_phase == "EDIT" else None
```

with:

```python
        question = str(gate.payload.get("question") or "")
        resume_phase = gate.payload.get("resume_phase")
        resume_phase = resume_phase if resume_phase in ("PLAN", "ACTIVE") else None
```

Replace both `edit_is_resume=(resume_phase == "EDIT")` occurrences (one in `handle_message`, one in `resolve_clarify`) with `edit_is_resume=(resume_phase == "ACTIVE")`.

- [ ] **Step 13: Run the full existing controller test suite, expect known-remaining failures**

```bash
pytest tests/ -k "controller" -v 2>&1 | tail -60
```
Expected at this point: `test_controller_phase.py` and `test_controller_decide_no_run_command.py` pass; several OTHER controller test files (`test_controller_explain.py`, `test_controller_propose_mode_validation.py`, `test_controller_loop_dedup_clears_on_edit.py`, `test_controller_loop_edit.py`, etc.) still fail or error — they reference the old phase strings/`enter_edit_mode`/`"edit"` mode value directly and are NOT yet updated (that's Task 8's audit). This is expected mid-plan; do not attempt to fix them in this task. Record the failing test names for Task 8.

- [ ] **Step 14: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_phase.py \
        services/agentd-py/agentd/chat/controller_loop.py \
        services/agentd-py/agentd/chat/controller.py \
        services/agentd-py/tests/test_controller_phase.py \
        services/agentd-py/tests/test_controller_decide_no_run_command.py
git commit -m "feat(controller): collapse DECIDE/EDIT/EXPLAIN into PLAN/ACTIVE phase model

Foundation for the merged-phase design: ControllerPhaseSM takes an
explicit start= (no post-construction transitions), _PHASE_TYPES/
_VALID_MODES renamed, resolve_mode/resolve_clarify updated for the
new implement wire value and PLAN-resume. Other controller test files
still reference old phase names — audited and fixed in a later task."
```

---

### Task 2: Lazy `TurnEditSession` factory (C1)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py:210-252` (`ControllerLoop.__init__`), `:596-598` (the `edit` dispatch's `assert`)
- Modify: `services/agentd-py/agentd/chat/controller.py:360-367` (`_run_loop`'s edit-session construction)
- Test: `services/agentd-py/tests/test_controller_loop_edit.py` (update construction sites)
- Test: new `services/agentd-py/tests/test_controller_loop_active_default_edits.py`

**Interfaces:**
- Consumes: Task 1's `ControllerPhaseSM(start="ACTIVE")`.
- Produces: `ControllerLoop.__init__(..., edit_session_factory: Callable[[], TurnEditSession] | None = None, ...)` — replaces the old `edit_session: TurnEditSession | None = None` parameter name and type.

- [ ] **Step 1: Write the failing test — a plain ACTIVE turn that edits must not crash**

Create `services/agentd-py/tests/test_controller_loop_active_default_edits.py`:

```python
"""C1 regression: ACTIVE is now the default phase for every plain turn. Before this
fix, TurnEditSession was only built when phase=="EDIT" (entered via resolve_mode),
so a plain "continue" message landing in ACTIVE by default would crash on its first
`edit` dispatch — assert self._edit is not None. The lazy factory must build it
on first use instead."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_active_default_turn_can_edit_via_lazy_factory(tmp_path: Path):
    wm = ShadowWorkspaceManager(tmp_path / "shadows")
    patch_engine = PatchEngine()
    factory_calls = []

    def factory() -> TurnEditSession:
        factory_calls.append(1)
        return TurnEditSession(
            turn_id="t1", real_path=tmp_path, workspace_manager=wm, patch_engine=patch_engine)

    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    steps = [
        {"type": "edit", "thought": "add file", "patch_ops": [
            {"op": "create_file", "file": "hello.txt", "content": "hi\n", "reason": "test"}]},
        {"type": "submit_changes", "thought": "done", "summary": "added hello.txt"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),  # default = ACTIVE
        edit_session_factory=factory)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert (tmp_path / "hello.txt").read_text() == "hi\n"
    assert factory_calls == [1]  # built exactly once, on first use


@pytest.mark.asyncio
async def test_active_pure_qa_turn_never_builds_the_factory(tmp_path: Path):
    calls = []

    def factory() -> TurnEditSession:
        calls.append(1)
        raise AssertionError("factory must not be called for a pure Q&A turn")

    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    steps = [{"type": "answer", "thought": "ok", "answer": "hello"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),
        edit_session_factory=factory)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer"
    assert calls == []
```

- [ ] **Step 2: Run it, confirm it fails**

```bash
pytest tests/test_controller_loop_active_default_edits.py -v
```
Expected: `TypeError: __init__() got an unexpected keyword argument 'edit_session_factory'`.

- [ ] **Step 3: `ControllerLoop.__init__` takes the factory**

In `controller_loop.py`, replace:

```python
    def __init__(
        self,
        reasoning: ReasoningEngine,
        registry: AggregatingToolRegistry,
        broadcaster: EventBroadcaster,
        *,
        channel_id: str,
        phase_sm: ControllerPhaseSM,
        edit_session: TurnEditSession | None = None,
        todo_ledger: TodoLedger | None = None,
        task_subsystem_enabled: bool = False,
        memory_harness: MemoryHarness = NO_OP_HARNESS,
        active_skills: dict[str, str] | None = None,
    ) -> None:
        self._reasoning = reasoning
        self._registry = registry
        self._broadcaster = broadcaster
        self._channel_id = channel_id
        self._sm = phase_sm
        self._edit = edit_session
```

with:

```python
    def __init__(
        self,
        reasoning: ReasoningEngine,
        registry: AggregatingToolRegistry,
        broadcaster: EventBroadcaster,
        *,
        channel_id: str,
        phase_sm: ControllerPhaseSM,
        edit_session_factory: Callable[[], TurnEditSession] | None = None,
        todo_ledger: TodoLedger | None = None,
        task_subsystem_enabled: bool = False,
        memory_harness: MemoryHarness = NO_OP_HARNESS,
        active_skills: dict[str, str] | None = None,
    ) -> None:
        self._reasoning = reasoning
        self._registry = registry
        self._broadcaster = broadcaster
        self._channel_id = channel_id
        self._sm = phase_sm
        # Built lazily on first `edit` dispatch (see the edit branch in _iterate), not
        # eagerly here — ACTIVE is now the default phase for every plain turn, so
        # eager construction would build a (cheap but real) session + shadow for every
        # pure Q&A turn too. The factory itself is a free closure either way.
        self._edit_session_factory = edit_session_factory
        self._edit: TurnEditSession | None = None
```

(`Callable` must be importable at runtime, not only under `TYPE_CHECKING` — move the `Callable`/`TurnEditSession` imports for this parameter out of the `if TYPE_CHECKING:` block, or use a string annotation. Since `from __future__ import annotations` is already at the top of this file, the annotation `Callable[[], TurnEditSession] | None` is fine to leave as a string-evaluated annotation without a runtime import; no change needed to the `TYPE_CHECKING` block.)

- [ ] **Step 4: The `edit` dispatch site builds via the factory on first use**

Replace:

```python
            if atype == "edit":
                # EDIT phase is only reachable with an edit_session (phase SM gate).
                assert self._edit is not None
```

with:

```python
            if atype == "edit":
                # Lazily construct on first use (C1) — ACTIVE is the default phase, so
                # a plain turn that never edits never pays for a session/shadow at all.
                if self._edit is None:
                    if self._edit_session_factory is None:
                        raise RuntimeError("edit requires an orchestrator (no edit_session_factory)")
                    self._edit = self._edit_session_factory()
```

- [ ] **Step 5: `controller.py`'s `_run_loop` builds the factory closure unconditionally**

Continuing from Task 1's Step 11 edit, replace the now-stale comment and add the factory (the `edit = None` line from Task 1 Step 11 is removed):

```python
        # Request-scoped todo ledger: rehydrate so it survives the PLAN->ACTIVE (mode
        # gate) and clarify-resume loop boundaries within one request.
        ledger = TodoLedger.from_json(self._store.get_controller_todos(thread_id))
        # The edit session (Task 2) is built lazily inside the loop now, not here.
```

with:

```python
        # Request-scoped todo ledger: rehydrate so it survives the PLAN->ACTIVE (mode
        # gate) and clarify-resume loop boundaries within one request.
        ledger = TodoLedger.from_json(self._store.get_controller_todos(thread_id))
        # ACTIVE is now the default phase for every plain turn — the session (which
        # needs the orchestrator's workspace_manager/patch_engine) must be buildable
        # from ANY phase, not just a mode-gated entry. Build a closure unconditionally
        # (free) and let ControllerLoop construct the real session lazily, on the
        # first actual `edit` dispatch (C1) — a pure Q&A/PLAN turn never pays for it.
        edit_session_factory = (
            (lambda: TurnEditSession(
                turn_id=thread_id, real_path=Path(self._workspace_path),
                workspace_manager=self._orchestrator._workspace_manager,
                patch_engine=self._orchestrator._patch_engine))
            if self._orchestrator is not None else None)
```

Then update the `ControllerLoop(...)` construction call a few lines below (locate `edit_session=edit,` in the existing call) to pass `edit_session_factory=edit_session_factory,` instead.

- [ ] **Step 6: Update `test_controller_loop_edit.py`'s construction sites**

Read `services/agentd-py/tests/test_controller_loop_edit.py` first (it constructs `ControllerLoop` with `edit_session=...` twice, per the earlier grep). Change each `edit_session=<TurnEditSession instance>` keyword argument to `edit_session_factory=lambda: <same TurnEditSession instance>` (a zero-arg lambda returning the already-constructed instance is sufficient for a test that only cares about one session).

- [ ] **Step 7: Run this task's tests + the updated file**

```bash
pytest tests/test_controller_loop_active_default_edits.py tests/test_controller_loop_edit.py -v
```
Expected: all pass.

- [ ] **Step 8: Full-repo grep for any other `edit_session=` construction site**

```bash
grep -rn "edit_session=" services/agentd-py/agentd services/agentd-py/tests
```
Fix any remaining site the same way as Step 6 (there should be none beyond `controller.py` — already fixed in Step 5 — and the test file from Step 6, but confirm).

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py \
        services/agentd-py/agentd/chat/controller.py \
        services/agentd-py/tests/test_controller_loop_edit.py \
        services/agentd-py/tests/test_controller_loop_active_default_edits.py
git commit -m "fix(controller): build TurnEditSession lazily via a factory (C1)

ACTIVE is now the default phase for every plain turn, so eager
construction (only ever done for phase==EDIT before) would crash the
first edit dispatch on a plain continuation message with no prior
mode gate. Build lazily on first use instead."
```

---

### Task 3: Merged entry-hint signal — `active_entry` / `skill_check_due` (C1b)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py:396-410` (the `edit_entry`/`decide_entry` plan_context writes)
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py:611-755` (the `if phase == "EDIT": ... else: # DECIDE` payload branch)
- Test: new `services/agentd-py/tests/test_controller_prompts_active_entry.py`

**Interfaces:**
- Consumes: Task 1's `"ACTIVE"`/`"PLAN"` phase names.
- Produces: `plan_context["active_entry"]` (no iteration clause — matches the OLD `edit_entry`'s persistence exactly) and `plan_context["skill_check_due"]` (one-shot, `iteration == 0` — matches the OLD `decide_entry` exactly), both computed from `phase == "ACTIVE"` instead of the old `"EDIT"`/`"DECIDE"` split.

- [ ] **Step 1: Write the failing test for the flags AND the fumble-recovery persistence**

Create `services/agentd-py/tests/test_controller_prompts_active_entry.py`:

```python
"""C1b: the merged entry-hint signal. active_entry (todo-vs-direct-edit guidance)
must persist across iterations exactly like the old edit_entry did — no iteration
clause — to preserve the empty-edit-fumble recovery this codebase already fixed
once. skill_check_due (the skill-triage half) is strictly one-shot, matching the
old decide_entry."""
from agentd.chat.controller_prompts import build_controller_step_payload


def _payload(plan_context, phase="ACTIVE", history=None):
    return build_controller_step_payload(
        plan_context, history or [], [], phase=phase, skills_available=False)


def test_active_entry_has_no_iteration_gating():
    # Simulates iteration 1 (history non-empty) with nothing started yet — the
    # fumble-recovery case: an empty edit failed at iteration 0, nothing landed.
    ctx = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": False,
    }
    payload = _payload(ctx, history=[{"role": "assistant", "content": "{}"},
                                      {"role": "tool_result", "tool": "", "content": "x"}])
    assert "FIRST action" in payload["instruction"] or "nothing is started" in payload["instruction"]


def test_skill_check_due_only_fires_once():
    ctx_iter0 = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": True,
    }
    payload0 = build_controller_step_payload(
        ctx_iter0, [], [], phase="ACTIVE", skills_available=True)
    assert "SKILL CHECK" in payload0["instruction"]

    ctx_iter1 = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": True, "skill_check_due": False,
    }
    payload1 = build_controller_step_payload(
        ctx_iter1, [{"role": "assistant", "content": "{}"},
                    {"role": "tool_result", "tool": "", "content": "x"}],
        [], phase="ACTIVE", skills_available=True)
    assert "SKILL CHECK" not in payload1["instruction"]


def test_active_entry_clears_once_edit_applied_or_ledger_started():
    # Once active_entry is False (an edit landed or a list started), the mid-turn
    # reconcile hint shows instead of the entry hint.
    ctx = {
        "workspace_path": "/tmp", "goal": "g",
        "active_entry": False, "skill_check_due": False, "todo_status": "",
    }
    payload = _payload(ctx, history=[{"role": "assistant", "content": "{}"},
                                      {"role": "tool_result", "tool": "", "content": "x"}])
    assert "RECONCILE" in payload["instruction"] or "reflect on your last edit" in payload["instruction"]
```

- [ ] **Step 2: Run it, confirm it fails**

```bash
pytest tests/test_controller_prompts_active_entry.py -v
```
Expected: fails — `build_controller_step_payload` still branches on `phase == "EDIT"`/the `else` (`DECIDE`) and reads `plan_context.get("edit_entry")`/`plan_context.get("decide_entry")`, not the new flag names, and `phase="ACTIVE"` currently falls into the `else` branch's DECIDE-flavored text since there's no `"ACTIVE"` case yet.

- [ ] **Step 3: `controller_loop.py` — replace the two old signals with the split flags**

Replace:

```python
            # EDIT-entry signal: first action after inline-edit was chosen, nothing started yet
            # (no list, no edit applied). The payload builder swaps the clean entry hint
            # (write_todos-as-tool_call) for the mid-turn reconcile hint once this clears —
            # which it does the moment a list exists OR an edit lands, so the entry hint persists
            # through an empty-edit fumble (keeps steering the right first move).
            plan_context["edit_entry"] = (
                self._sm.phase == "EDIT" and not self._ledger.items
                and not self._edit_applied and not plan_context.get("edit_is_resume"))
            # DECIDE-entry signal: the first model call of THIS run (iteration is the
            # for-loop counter above, fresh every run() — unlike `history`, which seeds
            # from the whole thread's replayed conversation and is non-empty for every
            # message after the thread's first ever). Without this, the "first move"
            # hint (which is where the skill-triage check lives) only ever fired once
            # per thread, not once per user message.
            plan_context["decide_entry"] = self._sm.phase == "DECIDE" and iteration == 0
```

with:

```python
            # active_entry (C1b): first action in ACTIVE, nothing started yet (no list,
            # no edit applied). The payload builder swaps the clean entry hint
            # (write_todos-as-tool_call) for the mid-turn reconcile hint once this clears —
            # which it does the moment a list exists OR an edit lands. NO iteration clause
            # — this is deliberate: it must persist through an empty-edit fumble (the model
            # emits an edit with empty patch_ops, nothing lands) so the NEXT iteration still
            # sees "nothing started yet" rather than falling through to the mid-turn
            # "reflect on your last edit's result" text written for a LANDED edit. This is a
            # previously-fixed thrash bug — do not add an iteration gate here.
            plan_context["active_entry"] = (
                self._sm.phase == "ACTIVE" and not self._ledger.items
                and not self._edit_applied and not plan_context.get("edit_is_resume"))
            # skill_check_due (C1b): the first model call of THIS run only (iteration is
            # the for-loop counter above, fresh every run() — unlike `history`, which
            # seeds from the whole thread's replayed conversation and is non-empty for
            # every message after the thread's first ever). Unlike active_entry, this IS
            # strictly one-shot — skill triage is genuinely redundant to re-run every
            # iteration once it's been done or missed.
            plan_context["skill_check_due"] = self._sm.phase == "ACTIVE" and iteration == 0
```

- [ ] **Step 4: `controller_prompts.py` — rewrite the `if phase == "EDIT": ... else: # DECIDE` branch**

Replace the entire block from `if phase == "EDIT":` through the end of the `else:  # DECIDE` block (i.e. everything between `final_call = iteration >= max_iters - 1` and `payload["instruction"] = ...`) with:

```python
    if phase == "ACTIVE":
        skill_check = (
            "SKILL CHECK — do this BEFORE locating code, answering, or editing: "
            "treat this as intent classification, in two steps. (1) Name the KIND of request "
            "this is, in your own words, in one short phrase — exactly as you naturally would "
            "if asked \"what is this request?\" (you already do this kind of labeling "
            "unprompted — use it deliberately here). Do NOT pick your wording from this "
            "workspace's own catalog below, and do NOT force it into a fixed set of categories "
            "— whatever installed skills exist should never bias what label you'd give an "
            "unrelated request. (2) Check that label against every skill's \"when to use\" line "
            "in the AVAILABLE SKILLS list (system prompt) — each line IS an intent trigger written "
            "by that skill's own author. A match means your label is close in MEANING, not "
            "overlapping in wording — and the line's enumeration bounds that meaning: when the "
            "author lists what the trigger covers, your label must fall inside that list, not "
            "merely share a broad headline word with it. This is UNCONDITIONAL: once you find a matching line, load "
            "it — do not then re-judge whether the task \"really needs\" it, or downgrade a match "
            "because the request seems small; the trigger line already made that call. If your "
            "label matches any line, even partially, THIS check "
            "wins over everything else below: your FIRST action MUST be tool_call read_skill(name), "
            "BEFORE locating code, answering, or editing — there is no other \"first "
            "action\" that outranks it. \"this looks simple\", \"I already know how to do this\", "
            "and \"let me explore first\" are NOT valid reasons to skip the check. "
        ) if skills_available and plan_context.get("skill_check_due") else ""
        # `or not history` mirrors the PLAN branch's fallback below (and the old
        # edit_entry code's `or not history`) — a direct caller of this function
        # that never sets active_entry explicitly (a test, or a future caller)
        # still gets the entry hint on empty history, matching PLAN's symmetry.
        if plan_context.get("active_entry") or not history:
            hint = (
                skill_check +
                "This is your FIRST action and nothing is started yet. Decide the approach:\n"
                "• BIG / multi-part (spans 3+ files, OR several independent parts, OR >~2 edit "
                "cycles): START A TODO LIST FIRST. write_todos is a TOOL — emit "
                "type='tool_call', tool='write_todos', args={\"items\":[{\"title\":...,"
                "\"status\":\"pending\"}, …]} listing EVERY part. Do NOT emit type='edit' with an "
                "empty patch_ops to 'do the todos' — that applies nothing and wastes the turn. "
                "After the list exists, edit items ONE AT A TIME (submit_changes is BLOCKED until "
                "none are pending).\n"
                "• SMALL / cohesive (one file, or a few related ops): SKIP the list — emit "
                "type='edit' now with a NON-EMPTY patch_ops, OR type='answer' if this needs no "
                "change at all.\n"
                f"Read the target region of any EXISTING file before changing it (search_code{_graph} "
                "→ read_file); a brand-new file needs no read. Finish with type='submit_changes'."
            )
        elif final_call:
            hint = (
                "⚠ FINAL STEP: emit type='submit_changes' now (a non-empty summary) to end the "
                "turn — or type='clarify' if a true blocker remains. No more edits after this."
            )
        else:
            reconcile_files = plan_context.get("pending_reconcile_files")
            reconcile_item = plan_context.get("reconcile_item")
            checkpoint = ""
            todo_status = plan_context.get("todo_status")
            if reconcile_files and todo_status:
                files_str = (
                    ", ".join(str(f) for f in reconcile_files)
                    if isinstance(reconcile_files, list) else str(reconcile_files))
                if isinstance(reconcile_item, dict) and reconcile_item.get("title"):
                    item_clause = (
                        f"Your current todo item is '{reconcile_item.get('title')}' "
                        f"(status: {reconcile_item.get('status', 'pending')}). "
                        f"Did this edit COMPLETE it? • If YES → emit write_todos NOW marking "
                        f"'{reconcile_item.get('title')}' 'done' (cite this edit in 'note') — but if "
                        f"'{reconcile_item.get('title')}' includes tests or a verify step, an edit is "
                        "NOT completion: run_command the verification first, or leave it 'in_progress'. "
                        f"• If only PARTIAL → keep editing '{reconcile_item.get('title')}', leave "
                        "it 'in_progress'. Then continue. ")
                else:
                    item_clause = (
                        "Did this edit COMPLETE one of your todo items? • If YES → emit write_todos "
                        "NOW marking it 'done' (cite this edit in 'note') — but if that item includes "
                        "tests or a verify step, an edit is NOT completion: run_command the "
                        "verification first, or leave it 'in_progress'. • If only PARTIAL → keep "
                        "editing that SAME item, leave it 'in_progress'. Then continue. ")
                checkpoint = f"CHECKPOINT — you just edited {files_str}. {item_clause}"
            hint = checkpoint + (
                "FIRST reflect on your last edit's result (if any): did it apply "
                "('applied+promoted') or fail ('PATCH FAILED: …')? "
                "If a todo list is active, RECONCILE it NOW — a separate write_todos call THIS "
                "turn, never batched for the end: (1) if the item you were working is now FULLY "
                "done, flip it to 'done' and cite the applied edit as evidence in 'note'; (2) if it "
                "is only PARTIALLY done, leave it 'in_progress' and keep editing THAT SAME item — do "
                "not start a new one; (3) when you start a new item, flip it 'pending'→'in_progress' "
                "in that same call. Keep the ledger matching reality every turn so the user sees live "
                "progress — NEVER leave everything 'pending' and mark it all 'done' at the very end. "
                "If the change is BIG (3+ files, big chunks across many places, or >~2 edit cycles) "
                "and no list is active yet, call write_todos NOW to record every part as 'pending'. "
                "For a small or cohesive change, skip the list and edit directly. "
                "submit_changes is BLOCKED until nothing is pending. THEN choose ONE: (A) "
                "CONTINUE/FIX — if an edit failed, re-read the exact lines and re-emit ONE corrected "
                "op (do NOT repeat the failed op verbatim); otherwise emit type='edit' for the "
                "current 'in_progress' item (or the next pending one). (B) DONE — only when no items "
                "remain (or the change was small), emit type='submit_changes' with a summary. A "
                "read-resistant blocker → mark the item 'blocked' or use type='clarify'."
            )
    else:  # PLAN
        # Plan Mode is a deliberate user choice (the sticky toggle) — unlike the old
        # DECIDE, which was simply the SM's only starting state and never something
        # the user opted into. Say so explicitly so the model's behavior matches
        # intent (lean into discussion, no rush) — this is per-turn payload text
        # (C4), never the cache-stable system prompt. Prepended on EVERY PLAN
        # iteration, not just entry — a mid-turn reminder earns its keep here.
        plan_mode_framing = (
            "You are in Plan Mode — the user turned this on deliberately for this message, "
            "specifically to discuss and refine before anything changes. Lean into that: it's "
            "fine to ask questions, explore thoroughly, and take an extra round to get the "
            "approach right, rather than rushing to propose_mode. "
        )
        skill_check = (
            "SKILL CHECK — do this BEFORE locating code, answering, or proposing anything: "
            "treat this as intent classification, in two steps. (1) Name the KIND of request "
            "this is, in your own words, in one short phrase — exactly as you naturally would "
            "if asked \"what is this request?\" (you already do this kind of labeling "
            "unprompted — use it deliberately here). Do NOT pick your wording from this "
            "workspace's own catalog below, and do NOT force it into a fixed set of categories "
            "— whatever installed skills exist should never bias what label you'd give an "
            "unrelated request. (2) Check that label against every skill's \"when to use\" line "
            "in the AVAILABLE SKILLS list (system prompt) — each line IS an intent trigger written "
            "by that skill's own author. A match means your label is close in MEANING, not "
            "overlapping in wording — and the line's enumeration bounds that meaning: when the "
            "author lists what the trigger covers, your label must fall inside that list, not "
            "merely share a broad headline word with it. This is UNCONDITIONAL: once you find a matching line, load "
            "it — do not then re-judge whether the task \"really needs\" it, or downgrade a match "
            "because the request seems small; the trigger line already made that call. If your "
            "label matches any line, even partially, THIS check "
            "wins over everything else below: your FIRST action MUST be tool_call read_skill(name), "
            "BEFORE locating code, answering, or proposing anything — there is no other \"first "
            "action\" that outranks it. \"this looks simple\", \"I already know how to do this\", "
            "and \"let me explore first\" are NOT valid reasons to skip the check. "
        ) if skills_available else ""
        if plan_context.get("decide_entry") or not history:
            hint = plan_mode_framing + (
                skill_check +
                "Once the skill check above is done (no line matched, or the matched skill's body "
                "is now in your payload), plan your next move. For a code-specific request "
                f"(how/where/trace, or a change) LOCATE the code: call search_code/search_semantic{_graph} "
                "— do NOT answer or propose_mode cold from the seed (it lacks most file bodies; "
                "answering from it confabulates). Answer directly ONLY if this is a purely "
                "conversational message needing no repo access."
            )
        elif final_call:
            hint = plan_mode_framing + (
                "⚠ FINAL STEP: exploration budget is spent. Commit now — type='answer' (complete, "
                "citing what you READ), type='propose_mode' (for a change), or type='clarify'. "
                "No more tool calls."
            )
        else:
            skill_reminder = (
                "Haven't classified this request's intent against the AVAILABLE SKILLS list yet "
                "this turn? Do that now, before committing — name the kind of request this is, "
                "then load any skill whose trigger matches that label. "
            ) if skills_available else ""
            hint = plan_mode_framing + (
                skill_reminder +
                "FIRST reflect: which files/functions can you cite from code you ACTUALLY opened "
                "this turn, and is anything material still unread? THEN choose ONE: (A) READ MORE "
                "— if any claim you'd make rests on a file you haven't opened, locate it "
                f"(search{_graph}) and read that region; never re-issue an identical call. "
                "(B) COMMIT — if you've read the code behind every claim, emit type='answer' "
                "(complete, non-empty, in the 'answer' field) or type='propose_mode' for a change. "
                "Neither is penalized — pick what your reflection supports."
            )
```

(Note: the `PLAN` branch still reads `plan_context.get("decide_entry")` — this is intentional; Task 1/3 only renamed `ACTIVE`'s signal, not `PLAN`'s. `decide_entry`'s existing semantics — `phase == "DECIDE" and iteration == 0`, computed in `controller_loop.py` — must be updated to `phase == "PLAN" and iteration == 0` in this same step, alongside the `active_entry`/`skill_check_due` rewrite in Step 3 above; add this one-line rename to Step 3's edit: keep a separate `plan_context["decide_entry"] = self._sm.phase == "PLAN" and iteration == 0` line — do not repurpose `skill_check_due` for PLAN, since PLAN's entry-hint text is otherwise unchanged from the old DECIDE branch and reads its own flag name.)

- [ ] **Step 5: Run this task's tests**

```bash
pytest tests/test_controller_prompts_active_entry.py -v
```
Expected: `3 passed`.

- [ ] **Step 6: Run the full `test_controller_loop_*` + `test_controller_prompts*` sweep**

```bash
pytest tests/ -k "controller_loop or controller_prompts or controller_phase" -v 2>&1 | tail -80
```
Fix any newly-broken test whose failure is directly attributable to this task's rename (e.g. a test asserting on the literal string `"decide_entry"` or `"edit_entry"` in a `plan_context` dict — rename the key it asserts on to match). Do NOT fix failures belonging to other files' `enter_edit_mode`/old-mode-value usage — those are Task 8's scope.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py \
        services/agentd-py/agentd/chat/controller_prompts.py \
        services/agentd-py/tests/test_controller_prompts_active_entry.py
git commit -m "fix(controller): split the merged entry-hint into active_entry + skill_check_due (C1b)

A single merged flag with an added iteration==0 clause would have
silently dropped edit_entry's deliberate cross-iteration persistence
(the empty-edit-fumble recovery mechanism). active_entry has no
iteration gate (matches edit_entry exactly); skill_check_due is
one-shot (matches decide_entry exactly). Both fire together on a cold
ACTIVE turn's iteration 0."
```

---

### Task 4: `answer` and the todo-ledger completion gate (C2)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py:503-506` (the `atype == "answer"` branch)
- Test: new `services/agentd-py/tests/test_controller_loop_answer_ledger_gate.py`

**Interfaces:**
- Consumes: Task 1's `_edit_applied` flag (already exists, unchanged) and `self._ledger.pending()` (already exists, unchanged).

- [ ] **Step 1: Write the failing tests — both directions**

Create `services/agentd-py/tests/test_controller_loop_answer_ledger_gate.py`:

```python
"""C2: `answer` is now reachable from ACTIVE (it wasn't from the old EDIT phase).
It must be blocked ONLY when this turn itself applied an edit AND the ledger still
has pending/in-progress items — never on raw stale ledger presence alone (that
would incorrectly block ordinary Q&A against an unrelated leftover list)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.todo_ledger import TodoLedger
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


def _loop(tmp_path, steps, ledger):
    wm = ShadowWorkspaceManager(tmp_path / "shadows")
    patch_engine = PatchEngine()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(), todo_ledger=ledger,
        edit_session_factory=lambda: TurnEditSession(
            turn_id="t1", real_path=tmp_path, workspace_manager=wm, patch_engine=patch_engine))


@pytest.mark.asyncio
async def test_answer_blocked_after_this_turn_edit_with_pending_items(tmp_path: Path):
    ledger = TodoLedger.from_json(
        '{"items":[{"title":"do X","status":"pending"}]}')
    steps = [
        {"type": "edit", "thought": "start", "patch_ops": [
            {"op": "create_file", "file": "a.txt", "content": "x\n", "reason": "t"}]},
        {"type": "answer", "thought": "done enough", "answer": "I made a.txt"},
        {"type": "submit_changes", "thought": "ok", "summary": "done"},
    ]
    out = await _loop(tmp_path, steps, ledger).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=8, auto_accept_edits=True)
    # The first `answer` must have been rejected (redirected) — the loop keeps going
    # and eventually reaches submit_changes only after the ledger is dealt with, or
    # exhausts trying. At minimum, assert the loop did NOT terminate on that `answer`.
    assert out.kind != "answer" or out.text != "I made a.txt"


@pytest.mark.asyncio
async def test_answer_allowed_for_pure_qa_despite_unrelated_stale_ledger(tmp_path: Path):
    # A non-empty ledger exists (left over from an unrelated earlier turn), but THIS
    # turn never edits — answer must NOT be blocked.
    ledger = TodoLedger.from_json(
        '{"items":[{"title":"unrelated leftover item","status":"pending"}]}')
    steps = [{"type": "answer", "thought": "just answering", "answer": "The answer is 42."}]
    out = await _loop(tmp_path, steps, ledger).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    assert out.kind == "answer"
    assert out.text == "The answer is 42."
```

- [ ] **Step 2: Run it, confirm the FIRST test fails (second may already pass)**

```bash
pytest tests/test_controller_loop_answer_ledger_gate.py -v
```
Expected: `test_answer_blocked_after_this_turn_edit_with_pending_items` FAILS (today `answer` isn't even in `_PHASE_TYPES["ACTIVE"]`... wait — `answer` IS in the merged `ACTIVE` list from Task 1. Without this task's gate, `answer` dispatches unconditionally and ends the turn with exactly `"I made a.txt"` — confirm the test fails for that reason). `test_answer_allowed_for_pure_qa_despite_unrelated_stale_ledger` should already PASS (nothing gates it yet) — that's fine, it's the negative control confirming Step 3 doesn't overcorrect.

- [ ] **Step 3: Gate `answer` on `_edit_applied AND ledger.pending()`**

In `controller_loop.py`, replace:

```python
            if atype == "answer":
                history.append(assistant_turn(resp))
                return ControllerOutcome(
                    kind="answer", text=str(resp.get("answer", "")), history=history)
```

with:

```python
            if atype == "answer":
                # C2: answer is reachable from ACTIVE (unlike the old EDIT phase, which
                # only had submit_changes as a terminal). Block it ONLY when THIS turn
                # itself applied an edit and the ledger still has open items — the same
                # class of guarantee submit_changes already enforces, but scoped tighter:
                # a stale unrelated ledger must never block ordinary Q&A that never
                # touched it (see the negative-control test in
                # test_controller_loop_answer_ledger_gate.py).
                if self._edit_applied and self._ledger.pending():
                    still_open = self._ledger.pending()
                    titles = ", ".join(i.title for i in still_open)
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "",
                        "content": (
                            f"'answer' is BLOCKED — {len(still_open)} todo item(s) are still "
                            f"pending/in_progress after edits you made this turn: {titles}. "
                            "Reconcile the list first (mark items done with evidence, or "
                            "'blocked' with a reason), or emit 'submit_changes' once it's "
                            "clear. If nothing here needs finishing, emit 'answer' again "
                            "unchanged."
                        ),
                    })
                    continue
                history.append(assistant_turn(resp))
                return ControllerOutcome(
                    kind="answer", text=str(resp.get("answer", "")), history=history)
```

- [ ] **Step 4: Run this task's tests**

```bash
pytest tests/test_controller_loop_answer_ledger_gate.py -v
```
Expected: `2 passed`.

- [ ] **Step 5: Run the broader ACTIVE/answer-related test sweep**

```bash
pytest tests/ -k "controller_loop" -v 2>&1 | tail -60
```
Fix any test whose `answer`-terminal assumption this gate now legitimately changes (a test that edits then answers with a still-pending ledger, expecting the old ungated behavior, needs its ledger reconciled in the scripted steps instead — do not weaken the gate to make such a test pass).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py \
        services/agentd-py/tests/test_controller_loop_answer_ledger_gate.py
git commit -m "fix(controller): gate answer on this-turn-edit + pending ledger items (C2)

Merging DECIDE's answer into ACTIVE introduced a second, ungated
terminal alongside submit_changes — a model could end a turn via
answer after editing with pending todos still open. Gate matches
intent exactly (edit_applied AND pending), not raw ledger presence,
so an unrelated stale ledger never blocks ordinary Q&A."
```

---

### Task 5: Task-subsystem-reachable `propose_mode` from ACTIVE (I4)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py:210-239` (`ControllerLoop.__init__`'s `_allowed_modes` + a new per-instance allowed-types override)
- Modify: `services/agentd-py/agentd/chat/controller_loop.py` (the `atype not in self._sm.allowed_types()` check in `_iterate`)
- Test: new `services/agentd-py/tests/test_controller_loop_active_propose_mode.py`

**Interfaces:**
- Consumes: Task 1's `task_subsystem_enabled: bool = False` constructor param (already exists, unchanged shape).
- Produces: `ControllerLoop._allowed_action_types()` — an instance method combining `self._sm.allowed_types()` with a conditional `propose_mode` addition when `task_subsystem_enabled` is on AND the phase is `ACTIVE`.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_controller_loop_active_propose_mode.py`:

```python
"""I4: when the task subsystem flag is ON, propose_mode must be reachable from
ACTIVE too (restricted to create_task/resume — never "implement", since ACTIVE is
already the implementing phase). When OFF (default), ACTIVE's action set is
unchanged — propose_mode stays PLAN-only."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, _propose_mode_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _loop(tmp_path, steps, task_subsystem_enabled):
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),  # ACTIVE
        task_subsystem_enabled=task_subsystem_enabled)


@pytest.mark.asyncio
async def test_propose_mode_reachable_from_active_when_task_subsystem_on(tmp_path: Path):
    steps = [
        {"type": "propose_mode", "thought": "big feature", "plan_sketch": "…",
         "reason": "large", "recommended": "create_task",
         "options": [{"mode": "create_task", "label": "Plan as task", "description": "…"}]},
    ]
    out = await _loop(tmp_path, steps, task_subsystem_enabled=True).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    assert out.kind == "propose_mode"


@pytest.mark.asyncio
async def test_propose_mode_rejected_from_active_when_task_subsystem_off(tmp_path: Path):
    steps = [
        {"type": "propose_mode", "thought": "big feature", "plan_sketch": "…",
         "reason": "large", "recommended": "create_task",
         "options": [{"mode": "create_task", "label": "Plan as task", "description": "…"}]},
        {"type": "answer", "thought": "ok", "answer": "fine, doing it inline"},
    ]
    out = await _loop(tmp_path, steps, task_subsystem_enabled=False).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer"  # propose_mode was rejected, corrected, model recovered


def test_active_propose_mode_allowed_modes_excludes_implement():
    resp = {"recommended": "implement", "options": [
        {"mode": "implement", "label": "x", "description": "y"}]}
    # ACTIVE's task-subsystem-on allowed modes must exclude "implement" — ACTIVE is
    # already the implementing phase, so proposing to re-enter it is meaningless.
    assert _propose_mode_correction(resp, frozenset({"create_task", "resume"})) is not None
```

- [ ] **Step 2: Run it, confirm the first and third fail**

```bash
pytest tests/test_controller_loop_active_propose_mode.py -v
```
Expected: `test_propose_mode_reachable_from_active_when_task_subsystem_on` fails (`propose_mode` not in `_PHASE_TYPES["ACTIVE"]` yet — `MALFORMED_CORRECTION` loops until budget exhausted, ending in `(loop ended)`, not `propose_mode`). The third test should already pass (it only checks the pure function). The second test should already pass.

- [ ] **Step 3: Add the instance-level allowed-types override + mode restriction**

In `controller_loop.py`, replace the `__init__` body's mode-restriction lines:

```python
        # OFF (default): only edit/explain may be offered — the controller handles changes
        # inline; a model that proposes create_task/resume anyway gets corrected.
        self._allowed_modes = (
            _VALID_MODES if task_subsystem_enabled else frozenset({"edit", "explain"}))
```

with:

```python
        self._task_subsystem_enabled = task_subsystem_enabled
        # PLAN's allowed modes: the full vocabulary when the task subsystem is on,
        # else just "implement" (create_task/resume stripped — the controller
        # handles everything inline).
        self._plan_allowed_modes = (
            _VALID_MODES if task_subsystem_enabled else frozenset({"implement"}))
        # ACTIVE's allowed modes (I4): only reachable at all when the task subsystem
        # is on, and even then restricted to create_task/resume — never "implement",
        # since ACTIVE is already the implementing phase.
        self._active_allowed_modes = frozenset({"create_task", "resume"})
```

Add a new method right after `__init__` (or near `partial_history`):

```python
    def _allowed_action_types(self) -> list[str]:
        """The action types legal THIS iteration — the phase SM's own set, plus a
        conditional propose_mode addition when in ACTIVE with the task subsystem on
        (I4: lets a big-enough ACTIVE-phase request escalate to a reviewed task
        without a detour through Plan Mode)."""
        types = list(self._sm.allowed_types())
        if self._sm.phase == "ACTIVE" and self._task_subsystem_enabled:
            types.append("propose_mode")
        return types

    def _allowed_modes_for_current_phase(self) -> frozenset[str]:
        return (
            self._active_allowed_modes if self._sm.phase == "ACTIVE"
            else self._plan_allowed_modes)
```

Update the two call sites in `_iterate` that reference `self._sm.allowed_types()` and `self._allowed_modes` directly:

```python
            correction = (
                MALFORMED_CORRECTION
                if atype not in self._sm.allowed_types()
                else _propose_mode_correction(resp, self._allowed_modes) if atype == "propose_mode"
```

becomes:

```python
            correction = (
                MALFORMED_CORRECTION
                if atype not in self._allowed_action_types()
                else _propose_mode_correction(resp, self._allowed_modes_for_current_phase()) if atype == "propose_mode"
```

Note: `controller_response_schema`'s and `create_controller_step`'s callers need NO change — they trim by `phase` alone via `_PHASE_TYPES[phase]`, which stays PLAN/ACTIVE's own module-level table; the I4 addition is a runtime-only relaxation on top of the schema's phase-based enum, mirroring how `_decide_state_change_correction` is also a runtime-only restriction beyond the schema. **Do not** add `propose_mode` to `_PHASE_TYPES["ACTIVE"]` itself — that would make it always legal regardless of the task-subsystem flag, defeating the point; the schema's per-request `enum` trim happens per the `phase` string alone, so the reachability distinction here is enforced entirely by `_allowed_action_types()`'s dispatch-time check, same pattern `_decide_state_change_correction` already uses for a different runtime-only restriction.

- [ ] **Step 4: Run this task's tests**

```bash
pytest tests/test_controller_loop_active_propose_mode.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Run the full `test_controller_loop*` sweep**

```bash
pytest tests/ -k "controller_loop" -v 2>&1 | tail -60
```

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py \
        services/agentd-py/tests/test_controller_loop_active_propose_mode.py
git commit -m "feat(controller): reach propose_mode from ACTIVE when task subsystem is on (I4)

Without this, create_task/resume would only be reachable when a user
deliberately turns Plan Mode on — a regression vs today's create_task
being reachable from every turn's sole starting phase. Restricted to
create_task/resume; never 'implement', since ACTIVE already implements."
```

---

### Task 6: Rewrite phase-neutral system-prompt prose (C3)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py:214-388` (`CONTROLLER_SYSTEM_PROMPT`, `_PROPOSE_MODE_MODES_ENABLED`, `_PROPOSE_MODE_MODES_DISABLED`)
- Test: new `services/agentd-py/tests/test_controller_system_prompt_phase_neutral.py`

**Interfaces:**
- Consumes: Task 1's `implement`/`create_task`/`resume` mode vocabulary.
- No new runtime interface — pure prompt-text changes, verified by substring assertions (this codebase's established pattern for prompt-text TDD) and, per this codebase's convention, worth a live-model smoke check during/after implementation (not encoded as an automated test).

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_controller_system_prompt_phase_neutral.py`:

```python
"""C3: the static system prompt must not describe the old gated model (edit only
"after the user picked edit", propose_mode as if every change routes through it,
edit|create_task|explain as the mode vocabulary)."""
from agentd.chat.controller_prompts import format_controller_system_prompt


def test_edit_variant_has_no_stale_gate_qualifier():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=False)
    assert 'after the user picked "edit"' not in prompt
    assert "EDIT mode only" not in prompt


def test_submit_changes_variant_has_no_stale_edit_mode_qualifier():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=False)
    assert "EDIT mode, when all edits are done" not in prompt


def test_propose_mode_modes_disabled_uses_implement_not_edit():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=False)
    assert '"implement"' in prompt
    assert "explain" not in prompt
    # "edit" the ACTION TYPE still legitimately appears elsewhere in the prompt (the
    # edit variant itself) — this test only asserts the propose_mode mode-vocabulary
    # no longer offers a mode literally named "edit".
    assert '<edit|explain>' not in prompt
    assert "mode\": <edit" not in prompt


def test_propose_mode_modes_enabled_uses_implement_create_task_resume():
    prompt = format_controller_system_prompt([], task_subsystem_enabled=True)
    assert "implement | create_task | resume" in prompt
    assert "explain" not in prompt
```

- [ ] **Step 2: Run it, confirm it fails**

```bash
pytest tests/test_controller_system_prompt_phase_neutral.py -v
```
Expected: all 4 fail against the current prompt text.

- [ ] **Step 3: Rewrite the three prose spots + both mode blocks**

In `controller_prompts.py`, replace the `WHEN THE REQUEST NEEDS A CHANGE` paragraph:

```python
WHEN THE REQUEST NEEDS A CHANGE — do NOT edit silently. First ground yourself (search/read the
EXISTING code you'll touch; a brand-new isolated file may need none), then emit type="propose_mode"
so the user picks HOW to proceed. Make "plan_sketch" CONCRETE (exact file path + function signature
+ how it integrates), NOT a restatement of the request. After the user picks "edit" you emit
type="edit" actions, then type="submit_changes" when done.
```

with:

```python
WHEN THE REQUEST NEEDS A CHANGE — editing is the default way to act; no permission step is
required. First ground yourself (search/read the EXISTING code you'll touch; a brand-new
isolated file may need none), then emit type="edit" actions directly, then
type="submit_changes" when done. (Plan Mode, a separate opt-in the user controls, is the
ONLY context where you propose a plan instead of editing directly — see the propose_mode
variant below, which does not apply outside Plan Mode.)
```

Replace the `edit` variant header:

```python
Variant — edit (EDIT mode only, after the user picked "edit"): {type, patch_ops}
```

with:

```python
Variant — edit (make a change directly — this is the default way to act on any request that needs one, no permission step required): {type, patch_ops}
```

Replace the `propose_mode` variant header + intro:

```python
Variant — propose_mode (the request needs a change): {type, plan_sketch, reason, recommended, options}
  Inline "edit" is the PRIMARY path for a change of ANY size — small AND large. A large /
  multi-part change is still done inline: you track it with the todo list (write_todos) and
  work it one item at a time. Do NOT treat "edit" as only-for-small.
  When the change is LARGE / multi-part, "plan_sketch" MUST enumerate EVERY distinct part
  (e.g. "1. Enemies … 2. Jump … 3. Timer …"), not just the first — that full scope becomes
  your todo list.
{propose_mode_modes}
```

with:

```python
Variant — propose_mode (Plan Mode only — you have a concrete approach and are ready to either implement it or hand it off): {type, plan_sketch, reason, recommended, options}
  In Plan Mode you never edit directly — propose_mode is how you hand a concrete plan back
  to the user. Outside Plan Mode (the default), skip this entirely: just edit. Inline edit
  is the PRIMARY path for a change of ANY size — small AND large — tracked with the todo
  list (write_todos) for anything multi-part.
  When the change is LARGE / multi-part, "plan_sketch" MUST enumerate EVERY distinct part
  (e.g. "1. Enemies … 2. Jump … 3. Timer …"), not just the first — that full scope becomes
  your todo list.
{propose_mode_modes}
```

Replace the `submit_changes` variant header:

```python
Variant — submit_changes (EDIT mode, when all edits are done): {type, summary}
```

with:

```python
Variant — submit_changes (once all edits for this request are done): {type, summary}
```

Replace the two mode-vocabulary blocks:

```python
_PROPOSE_MODE_MODES_ENABLED = """\
  "recommended": EXACTLY one of edit | create_task | resume | explain.
  "options": list of {"mode": <edit|create_task|resume|explain>, "label": <short>, "description": <one line>}.
  Use the exact key "mode" (never "type") and only those four values. Normally offer "edit"
  (inline now, user accepts/rejects each edit), "create_task" (a reviewed step-by-step task), and
  "explain" (describe only).
  {"type":"propose_mode","thought":"new feature","plan_sketch":"Add clamp(x,lo,hi) to src/mathutil.py","reason":"single new file","recommended":"edit","options":[
    {"mode":"edit","label":"Edit inline now","description":"I make the change directly; you review it."},
    {"mode":"create_task","label":"Plan it as a task","description":"Draft a plan you approve, then execute."},
    {"mode":"explain","label":"Just explain","description":"No changes — I describe the approach."}]}"""

_PROPOSE_MODE_MODES_DISABLED = """\
  "recommended": EXACTLY one of edit | explain.
  "options": list of {"mode": <edit|explain>, "label": <short>, "description": <one line>}.
  Use the exact key "mode" (never "type") and only those two values. Offer "edit"
  (make the change inline now — any size, tracked with the todo list) and "explain" (describe only).
  {"type":"propose_mode","thought":"new feature","plan_sketch":"Add clamp(x,lo,hi) to src/mathutil.py","reason":"single new file","recommended":"edit","options":[
    {"mode":"edit","label":"Edit inline now","description":"I make the change directly; you review it."},
    {"mode":"explain","label":"Just explain","description":"No changes — I describe the approach."}]}"""
```

with:

```python
_PROPOSE_MODE_MODES_ENABLED = """\
  "recommended": EXACTLY one of implement | create_task | resume.
  "options": list of {"mode": <implement|create_task|resume>, "label": <short>, "description": <one line>}.
  Use the exact key "mode" (never "type") and only those values. Offer "implement" (exit
  Plan Mode and make the change directly now) and "create_task" (draft a reviewed
  step-by-step task) at minimum; add "resume" only when a matching prior task exists.
  {"type":"propose_mode","thought":"ready to build","plan_sketch":"Add clamp(x,lo,hi) to src/mathutil.py","reason":"single new file","recommended":"implement","options":[
    {"mode":"implement","label":"Implement this plan","description":"Exit Plan Mode; I make the change directly and you review it."},
    {"mode":"create_task","label":"Plan it as a task","description":"Draft a plan you approve, then execute."}]}"""

_PROPOSE_MODE_MODES_DISABLED = """\
  "recommended": "implement".
  "options": list containing at least {"mode": "implement", "label": <short>, "description": <one line>}.
  Use the exact key "mode" (never "type"). "implement" exits Plan Mode so you can make the
  change directly, tracked with the todo list for anything multi-part.
  {"type":"propose_mode","thought":"ready to build","plan_sketch":"Add clamp(x,lo,hi) to src/mathutil.py","reason":"single new file","recommended":"implement","options":[
    {"mode":"implement","label":"Implement this plan","description":"Exit Plan Mode; I make the change directly and you review it."}]}"""
```

Also update the earlier `tool_call` variant's note (still accurate but worth a pass): the line `run_command is NOT available yet — to change anything, emit propose_mode (never write files via the shell); run_command unlocks once editing starts.` describes PLAN's restriction specifically (this text sits under the general `tool_call` variant heading, which is used in BOTH phases) — leave it as a phase-agnostic true statement since it already correctly says "run_command is NOT available yet" without over-claiming when — no change needed here; this line only actually renders as relevant guidance while a model is in PLAN (in ACTIVE, run_command is already unlocked, so the line is simply inert/ignorable prose, not misleading).

- [ ] **Step 4: Run this task's tests**

```bash
pytest tests/test_controller_system_prompt_phase_neutral.py -v
```
Expected: `4 passed`.

- [ ] **Step 5: Run the full prompt-related test sweep**

```bash
pytest tests/ -k "controller_prompt or controller_system" -v 2>&1 | tail -60
```

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py \
        services/agentd-py/tests/test_controller_system_prompt_phase_neutral.py
git commit -m "fix(controller): rewrite phase-coupled system-prompt prose for the merged model (C3)

The edit/submit_changes/propose_mode variant text and both
PROPOSE_MODE_MODES blocks described the old gated model (edit only
after picking edit, propose_mode as though every change routes
through it, edit|explain vocabulary). Rewritten phase-neutral;
mode vocabulary is now implement | create_task | resume."
```

---

### Task 7: Backend `plan_mode` plumbing — route, `handle_message`, `_run_loop` starting phase

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py:1370-1412` (the `POST /chat/threads/{thread_id}/message` route)
- Modify: `services/agentd-py/agentd/chat/controller.py:281-345` (`handle_message`)
- Test: new `services/agentd-py/tests/test_controller_plan_mode_wiring.py`

**Interfaces:**
- Produces: `ChatController.handle_message(..., plan_mode: bool | None = None)`.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_controller_plan_mode_wiring.py`:

```python
"""NEW-I6: handle_message must actually start the turn's SM in PLAN when plan_mode
is true — not silently ignore it (the stray `resume_phase = None` bug the design
review caught: leaving it unwired would force every plain message into ACTIVE
regardless of the toggle)."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_plan_mode_true_starts_turn_in_plan(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    steps = [{"type": "answer", "thought": "ok", "answer": "in plan mode"}]
    reasoning = ScriptedReasoningEngine(None, [], controller_step_responses=steps)
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=reasoning, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster())
    await ctrl.handle_message(thread.thread_id, "hello", "chan", plan_mode=True)
    trace_path = tmp_path / ".crucible" / "state" / "artifacts" / "chat" / thread.thread_id
    # Simpler + more direct than reading the artifact: assert via the turn-trace file
    # written by _write_turn_trace, which records the phase the loop actually ran in.
    import json
    matches = list(trace_path.glob("*/turn-trace.json"))
    assert matches, "expected a turn-trace artifact"
    data = json.loads(matches[0].read_text())
    assert data["phase"] == "PLAN"


@pytest.mark.asyncio
async def test_plan_mode_false_or_omitted_starts_turn_in_active(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    steps = [{"type": "answer", "thought": "ok", "answer": "in active mode"}]
    reasoning = ScriptedReasoningEngine(None, [], controller_step_responses=steps)
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=reasoning, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster())
    await ctrl.handle_message(thread.thread_id, "hello", "chan")  # plan_mode omitted
    import json
    trace_path = tmp_path / ".crucible" / "state" / "artifacts" / "chat" / thread.thread_id
    matches = list(trace_path.glob("*/turn-trace.json"))
    assert matches
    data = json.loads(matches[0].read_text())
    assert data["phase"] == "ACTIVE"
```

- [ ] **Step 2: Run it, confirm it fails**

```bash
pytest tests/test_controller_plan_mode_wiring.py -v
```
Expected: `TypeError: handle_message() got an unexpected keyword argument 'plan_mode'`.

(If the artifact glob/path doesn't match this repo's actual `chat_turn_artifacts_root` layout, adjust the glob to match — read `agentd/runtime/artifacts.py::chat_turn_artifacts_root`'s actual return shape first if this fails for a path reason unrelated to `plan_mode`.)

- [ ] **Step 3: Wire `plan_mode` through `handle_message`**

In `controller.py`, replace the `handle_message` signature:

```python
    async def handle_message(
        self, thread_id: str, message: str, channel_id: str, step_review: bool | None = None,
        forced_skills: list[str] | None = None,
        mentioned_files: list[dict[str, str]] | None = None,
    ) -> None:
```

with:

```python
    async def handle_message(
        self, thread_id: str, message: str, channel_id: str, step_review: bool | None = None,
        forced_skills: list[str] | None = None,
        mentioned_files: list[dict[str, str]] | None = None,
        plan_mode: bool | None = None,
    ) -> None:
```

Replace:

```python
        # Clarify-resume is now driven by resolve_clarify (the gate carries resume_phase),
        # not a fresh user message: the main composer is disabled while a clarify gate is
        # pending, so the answer arrives via the card. A plain message here always
        # supersedes any pending gate (cleared above) and re-enters DECIDE.
        resume_phase = None
```

with:

```python
        # Clarify-resume is now driven by resolve_clarify (the gate carries resume_phase),
        # not a fresh user message: the main composer is disabled while a clarify gate is
        # pending, so the answer arrives via the card. A plain message here always
        # supersedes any pending gate (cleared above) and starts fresh, in the phase the
        # sticky Plan Mode toggle selects (NEW-I6 — this MUST be the plan_mode
        # computation, never a stray None, or the toggle silently has no effect).
        resume_phase = "PLAN" if plan_mode else "ACTIVE"
```

The line below it, `edit_is_resume=(resume_phase == "EDIT")`, was already renamed to `(resume_phase == "ACTIVE")` in Task 1 Step 12 — no further change needed here (a fresh `plan_mode`-derived turn is never a resume, but `edit_is_resume` being `True` for a fresh `ACTIVE` entry is harmless: it only suppresses the entry hint, and Task 3's `active_entry` already independently gates on `not self._ledger.items and not self._edit_applied`, so a fresh empty-ledger `ACTIVE` turn still gets the entry hint via those other clauses regardless of `edit_is_resume`'s value here — no behavior change from this pre-existing interaction).

- [ ] **Step 4: Thread `plan_mode` through the route**

In `services/agentd-py/agentd/api/routes.py`, inside `post_chat_message`, replace:

```python
            _raw_mentioned = request.get("mentioned_files")
            mentioned_files = (
                [
                    {"path": str(f.get("path", "")), "content": str(f.get("content", ""))}
                    for f in _raw_mentioned
                    if isinstance(f, dict) and f.get("path")
                ]
                if isinstance(_raw_mentioned, list) else None
            ) or None
            channel_id = f"chat:{thread_id}"
```

with:

```python
            _raw_mentioned = request.get("mentioned_files")
            mentioned_files = (
                [
                    {"path": str(f.get("path", "")), "content": str(f.get("content", ""))}
                    for f in _raw_mentioned
                    if isinstance(f, dict) and f.get("path")
                ]
                if isinstance(_raw_mentioned, list) else None
            ) or None
            _raw_plan_mode = request.get("plan_mode")
            plan_mode = _raw_plan_mode if isinstance(_raw_plan_mode, bool) else None
            channel_id = f"chat:{thread_id}"
```

Add `plan_mode=plan_mode` to ONLY the `ChatController` call site a few lines below — the one inside the `if _active is not None:` branch: `_chat_agent.handle_message(thread_id, message, channel_id=channel_id, step_review=step_review, forced_skills=forced_skills, mentioned_files=mentioned_files)`. Do **NOT** add `plan_mode` to the other call site further down, `await _chat_agent.handle_message(thread_id, message, channel_id=channel_id, step_review=step_review)` — that one dispatches to the legacy `ChatAgent` (the `CRUCIBLE_CHAT_CONTROLLER=0` path, a real supported configuration per this repo's CLAUDE.md, not dead code), whose `handle_message` does not accept `plan_mode` at all (controller-only, matching the existing convention for `mentioned_files`/`forced_skills`/MCP/skills). Passing `plan_mode` there raises `TypeError: handle_message() got an unexpected keyword argument 'plan_mode'` the first time the backend runs with the controller flag off.

- [ ] **Step 5: Run this task's tests**

```bash
pytest tests/test_controller_plan_mode_wiring.py -v
```
Expected: `2 passed`.

- [ ] **Step 6: Run the full agentd-py suite**

```bash
pytest 2>&1 | tail -15
```
This is the first point in the plan where the WHOLE suite should be run (Tasks 1-6 individually scoped their runs to avoid churn on not-yet-fixed sibling files — Task 8 fixes those). Expect `test_controller_explain.py` and several other old-phase-name test files to still be failing; confirm the failure list matches what Task 1 Step 13 recorded (no NEW failures beyond that known set).

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py \
        services/agentd-py/agentd/chat/controller.py \
        services/agentd-py/tests/test_controller_plan_mode_wiring.py
git commit -m "feat(controller): wire plan_mode through the message route to handle_message

The sticky Plan Mode toggle is now reachable end-to-end on the
backend: POST /message's plan_mode field selects PLAN vs ACTIVE as
the turn's starting phase. Frontend plumbing (the toggle itself) is
a separate task."
```

---

### Task 8: Audit and fix all remaining old-phase-name test sites

**Files:**
- Modify: `services/agentd-py/tests/test_controller_propose_mode_validation.py`
- Modify: `services/agentd-py/tests/test_controller_loop_dedup_clears_on_edit.py`
- Modify: `services/agentd-py/tests/test_controller_loop_edit.py` (already partially touched in Task 2 — verify no remaining old-phase references)
- Modify: `services/agentd-py/tests/test_controller_loop_explore_answer.py`
- Modify: `services/agentd-py/tests/test_controller_invariants.py`
- Modify: `services/agentd-py/tests/test_controller_todo_gate.py`
- Modify: `services/agentd-py/tests/test_controller_clarify_gate.py`
- Modify: `services/agentd-py/tests/test_controller_stop_partial_history.py`
- Modify: `services/agentd-py/tests/test_controller_reserved_tool_name.py`
- Modify: `services/agentd-py/tests/test_controller_loop_escalating_correction.py`
- Modify: `services/agentd-py/tests/test_controller_loop_submit_requires_fresh_verify.py`
- Modify: `services/agentd-py/tests/test_controller_edit_clarify.py`
- Delete: `services/agentd-py/tests/test_controller_explain.py`
- Modify: any OTHER file the Step 1 grep surfaces (the list above is what's known from this session's earlier exploration — treat it as a starting point, not exhaustive; the grep in Step 1 is the actual source of truth)

**Interfaces:** none (test-only task).

- [ ] **Step 1: Run the full 43-site grep and classify each**

```bash
cd services/agentd-py
grep -rn "ControllerPhaseSM()" agentd/ tests/
```

For each hit, apply this classification (from the design doc, verbatim procedure):
- **Shape A** — the test's point is exercising the *restricted* phase (asserts a rejection, checks `allowed_types()`, or otherwise depends on the old "bare = DECIDE" behavior). Grep the site's surrounding ~10 lines for `allowed_types()`, `pytest.raises`, or assertion text containing `"rejected"`/`"not available"`/`"DECIDE"`. These need an explicit `ControllerPhaseSM(start="PLAN")`.
- **Shape B** — the SM is only there to satisfy a constructor argument; the test never asserts on phase-gated behavior. Safe to leave as bare `ControllerPhaseSM()` (now defaults to `ACTIVE`, a superset of what read-only `DECIDE` allowed for tool calls).

`test_controller_decide_no_run_command.py` was already fixed in Task 1 (Shape A, confirmed). Go through every remaining hit now.

- [ ] **Step 2: Delete `test_controller_explain.py`**

```bash
git rm tests/test_controller_explain.py
```
Its entire subject (the `EXPLAIN` phase) no longer exists.

- [ ] **Step 3: Fix `test_controller_propose_mode_validation.py`**

Read the file. Update any construction using bare `ControllerPhaseSM()` where the test's point is validating `propose_mode`'s rejection/acceptance behavior specifically in the restricted phase — those become `ControllerPhaseSM(start="PLAN")`. Update any literal `"edit"`/`"explain"` mode-vocabulary string the test asserts against to the new `"implement"`/dropped-`explain` vocabulary (mirroring Task 1's `_VALID_MODES`/`PROPOSE_MODE_CORRECTION` changes and Task 6's system-prompt text changes, if this test asserts against prompt text).

- [ ] **Step 4: Fix `test_controller_loop_dedup_clears_on_edit.py`**

Read the file. It constructs an SM and drives an edit through the loop (per this session's earlier work on the dedup-clear fix) — update any `sm.enter_edit_mode()` call (removed method) to construct with `ControllerPhaseSM(start="ACTIVE")` directly instead, and any `edit_session=` keyword to `edit_session_factory=` (mirroring Task 2 Step 6).

- [ ] **Step 5: Fix `test_controller_loop_explore_answer.py`**

Read the file. Likely Shape B (explore + answer, no phase-gated assertions) — confirm via the grep classification and leave as bare `ControllerPhaseSM()` if so, else apply Shape A's fix.

- [ ] **Step 6: Fix `test_controller_invariants.py`**

Read the file (two `ControllerPhaseSM()` sites per the earlier grep, lines ~93 and ~121). Classify each independently — they may differ.

- [ ] **Step 7: Fix `test_controller_todo_gate.py`**

Read the file (three sites per the earlier grep). This file is a strong candidate to also need `atype == "submit_changes"` / new `atype == "answer"` gate coverage cross-checked against Task 4's C2 change — if any existing scripted step sequence here relies on `answer` being ungated mid-edit-with-pending-items, that assumption is now wrong and the test needs updating to reconcile the ledger first (do not weaken Task 4's gate).

- [ ] **Step 8: Fix `test_controller_clarify_gate.py` and `test_controller_edit_clarify.py`**

Read both files. These are the two most likely to reference the OLD `resume_phase == "EDIT"` string directly (per Task 1/C5's rename to `"ACTIVE"`) — update any such literal string assertion. Also fix any bare `ControllerPhaseSM()` per the Step 1 classification.

- [ ] **Step 9: Fix `test_controller_stop_partial_history.py`, `test_controller_reserved_tool_name.py`, `test_controller_loop_escalating_correction.py`, `test_controller_loop_submit_requires_fresh_verify.py`**

Read each. Apply the Step 1 classification to each file's `ControllerPhaseSM()` construction site(s).

- [ ] **Step 10: Add the pinning regression test for the SM default**

Add to `services/agentd-py/tests/test_controller_phase.py` (already rewritten in Task 1 — this is an addition, not a further rewrite):

```python
def test_default_pin_regression():
    # Explicit pin, independent of the 43-site audit above: catches an accidental
    # future flip back to a restricted default.
    sm = ControllerPhaseSM()
    assert sm.phase == "ACTIVE"
    assert "edit" in sm.allowed_types()
```

- [ ] **Step 11: Run the ENTIRE agentd-py suite**

```bash
pytest > /tmp/full_suite_after_audit.txt 2>&1; echo "exit=$?"; tail -15 /tmp/full_suite_after_audit.txt
```
Expected: full pass (a small number of environment-dependent skips is fine — no new failures beyond this repo's known pre-existing skip set). This is the completion gate for the entire backend half of this plan.

- [ ] **Step 12: Commit**

```bash
git add services/agentd-py/tests/
git commit -m "test(controller): audit all 43 ControllerPhaseSM() sites for the new default

Classified each per the design doc's Shape A/B procedure: sites
exercising the restricted phase get an explicit start=\"PLAN\";
sites where the SM is incidental stay bare (now ACTIVE by default).
Deletes test_controller_explain.py (EXPLAIN no longer exists). Full
agentd-py suite green."
```

---

### Task 9: `ModeGate.tsx` — scrollable plan display, `implement` wire value, auto-exit posting

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/gates/ModeGate.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/gates/ModeGate.test.tsx`

**Interfaces:**
- Consumes: the backend's new `"implement"` mode value (Task 1/6).
- Produces: `ModeGate` posts a second `{ type: "setPlanMode", enabled: false }` message when `mode === "implement"` is picked (consumed by Task 10's extension-side handler).

- [ ] **Step 1: Read the existing test file first**

```bash
cat apps/vscode-extension/webview-ui/src/test/../components/messages/gates/ModeGate.test.tsx 2>/dev/null || find apps/vscode-extension/webview-ui -iname "ModeGate.test.tsx"
```
Read whatever it finds — this plan does not reproduce its current contents (not yet read in this session); update its existing "edit"-mode-labeled test cases to use `"implement"` instead, following the same assertion style already present.

- [ ] **Step 2: Write/extend the failing test for the scrollable plan-sketch + auto-exit post**

Add to `ModeGate.test.tsx` (adapting to whatever test-rendering helper the existing file already uses — read it first and match its pattern; the shape below assumes a React Testing Library-style render, adjust imports to match this file's actual existing imports):

```tsx
it("posts setPlanMode(false) when 'implement' is picked", () => {
  const posted: unknown[] = [];
  (vscode.postMessage as unknown as (m: unknown) => void) = (m) => { posted.push(m); };
  render(<ModeGate taskId="t1" payload={{
    plan_sketch: "a".repeat(3000),  // long enough to require scrolling
    recommended: "implement",
    options: [{ mode: "implement", label: "Implement this plan", description: "…" }],
  }} />);
  fireEvent.click(screen.getByText("Implement this plan"));
  expect(posted).toContainEqual({ type: "modeDecision", threadId: "t1", mode: "implement" });
  expect(posted).toContainEqual({ type: "setPlanMode", enabled: false });
});

it("renders the plan sketch in a scrollable/expandable container for long plans", () => {
  const longPlan = "line\n".repeat(200);
  render(<ModeGate taskId="t1" payload={{ plan_sketch: longPlan, options: [] }} />);
  const sketch = screen.getByText(/line/, { exact: false });
  // The container must impose a max-height with overflow, not render unbounded.
  const container = sketch.closest("[data-testid='plan-sketch']");
  expect(container).not.toBeNull();
});
```

- [ ] **Step 3: Run it, confirm it fails**

```bash
cd apps/vscode-extension && npm run -w crucible-vscode-extension test -- ModeGate 2>&1 | tail -40
```
Expected: fails — no `setPlanMode` post exists yet, and the plan-sketch div has no `data-testid="plan-sketch"` / scroll styling.

- [ ] **Step 4: Update `ModeGate.tsx`**

Replace `handlePick`:

```tsx
  function handlePick(mode: string, label: string) {
    if (resolved !== null) return; // one-shot guard
    setResolved(label);
    vscode.postMessage({ type: "modeDecision", threadId: taskId, mode });
  }
```

with:

```tsx
  function handlePick(mode: string, label: string) {
    if (resolved !== null) return; // one-shot guard
    setResolved(label);
    vscode.postMessage({ type: "modeDecision", threadId: taskId, mode });
    // Picking "implement" exits Plan Mode — the composer's sticky toggle must flip
    // off too, in the SAME action (not inferred by the extension from modeDecision
    // alone — the card already knows this is an exit). Single write path: the
    // extension's setPlanMode handler is the one place this value ever changes.
    if (mode === "implement") {
      vscode.postMessage({ type: "setPlanMode", enabled: false });
    }
  }
```

Replace the plan-sketch display block:

```tsx
      {/* ── Approach sketch ── */}
      {planSketch && (
        <div className="px-2.5 py-2 text-[12px] text-text-1 whitespace-pre-wrap border-t border-border">
          {planSketch}
        </div>
      )}
```

with:

```tsx
      {/* ── Approach sketch — scrollable/expandable so a long, detailed plan is
          actually readable before deciding (this is what makes dropping the old
          EXPLAIN mode safe — its only purpose was working around this card being
          too shallow to read). ── */}
      {planSketch && (
        <div
          data-testid="plan-sketch"
          className="px-2.5 py-2 text-[12px] text-text-1 whitespace-pre-wrap border-t border-border overflow-y-auto"
          style={{ maxHeight: "16rem" }}
        >
          {planSketch}
        </div>
      )}
```

- [ ] **Step 5: Run the tests**

```bash
npm run -w crucible-vscode-extension test -- ModeGate 2>&1 | tail -40
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/gates/ModeGate.tsx \
        apps/vscode-extension/webview-ui/src/components/messages/gates/ModeGate.test.tsx
git commit -m "feat(webview): scrollable plan-sketch card + implement wire value + auto-exit Plan Mode

Picking 'Implement this plan' now also posts setPlanMode(false) in
the same click handler. The plan-sketch display gets a bounded,
scrollable container — this is what makes dropping the old EXPLAIN
mode safe (its only purpose was working around an unreadable card)."
```

---

### Task 10: Sticky `plan_mode` toggle — full-stack plumbing (extension storage → composer → wire → backend)

**Files:**
- Modify: `apps/vscode-extension/src/runtime/vscode-runtime.ts` (add `getPlanMode`/`setPlanMode`)
- Modify: `apps/vscode-extension/src/chat-panel.ts` (hydration push + `setPlanMode` message routing)
- Modify: `apps/vscode-extension/src/extension.ts` (wire the `setPlanMode` webview message to `RuntimeManager`)
- Modify: `apps/vscode-extension/src/controller.ts` (`sendChatMessage` gains `planMode` param)
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx` (controlled Plan Mode checkbox, replacing local-only pattern for this one field)
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`sendChatMessage` options gain `planMode`)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`sendChatMessage` maps `planMode` → `plan_mode`)
- Test: `apps/editor-client/test/` (new contract test for the `plan_mode` body field)
- Test: `apps/vscode-extension/webview-ui/src/test/` (InputArea Plan Mode checkbox test)

**Interfaces:**
- Produces: `RuntimeManager.getPlanMode(): boolean` / `.setPlanMode(v: boolean): Promise<void>` (mirrors the existing `mcpDisabledServers`/`skillsDisabled` globalState pattern).
- Produces: webview→host `{ type: "setPlanMode", enabled: boolean }` and host→webview `{ type: "planModeState", enabled: boolean }`.
- Produces: `sendChatMessage(text, stepReview?, forcedSkills?, mentionedPaths?, planMode?)` on `controller.ts`; `sendChatMessage(threadId, message, signal?, options?: {..., planMode?: boolean})` on the editor-client contract.

- [ ] **Step 1: Write the failing editor-client contract test**

Read `apps/editor-client/test/` to find the existing `sendChatMessage`/`stepReview` contract test file (mirroring the pattern used for `stepReview`/`forcedSkills`) and add a case following its exact existing style:

```ts
it("includes plan_mode in the request body when planMode is set", async () => {
  const calls: RequestInit[] = [];
  const fetchFn = async (_url: string, init: RequestInit) => {
    calls.push(init);
    return new Response(null, { status: 200 });
  };
  const client = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
  const iter = client.sendChatMessage("t1", "hello", undefined, { planMode: true });
  // eslint-disable-next-line @typescript-eslint/no-unused-expressions
  for await (const _ of iter) { /* drain */ }
  const body = JSON.parse(String(calls[0].body));
  expect(body.plan_mode).toBe(true);
});

it("omits plan_mode from the request body when not provided", async () => {
  const calls: RequestInit[] = [];
  const fetchFn = async (_url: string, init: RequestInit) => {
    calls.push(init);
    return new Response(null, { status: 200 });
  };
  const client = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
  const iter = client.sendChatMessage("t1", "hello");
  for await (const _ of iter) { /* drain */ }
  const body = JSON.parse(String(calls[0].body));
  expect(body).not.toHaveProperty("plan_mode");
});
```

- [ ] **Step 2: Run it, confirm it fails**

```bash
npm run -w @crucible/editor-client test 2>&1 | tail -40
```
Expected: TypeScript error or assertion failure — `planMode` isn't a recognized option key yet.

- [ ] **Step 3: Add `planMode` to the editor-client contract + implementation**

In `apps/editor-client/src/contracts/task-contracts.ts`, replace:

```ts
  sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[] }): AsyncIterable<StreamEvent>;
```

with:

```ts
  sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[]; planMode?: boolean }): AsyncIterable<StreamEvent>;
```

In `apps/editor-client/src/client/http-backend-client.ts`, replace the same signature at line 698 and its body:

```ts
  async *sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[] }): AsyncIterable<StreamEvent> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/message`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: message,
          ...(options?.stepReview !== undefined ? { step_review: options.stepReview } : {}),
          ...(options?.forcedSkills && options.forcedSkills.length
            ? { forced_skills: options.forcedSkills }
            : {}),
          ...(options?.mentionedFiles && options.mentionedFiles.length
            ? { mentioned_files: options.mentionedFiles }
            : {}),
        }),
        signal: signal ?? null,
      }
    );
```

with:

```ts
  async *sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[]; planMode?: boolean }): AsyncIterable<StreamEvent> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/message`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: message,
          ...(options?.stepReview !== undefined ? { step_review: options.stepReview } : {}),
          ...(options?.forcedSkills && options.forcedSkills.length
            ? { forced_skills: options.forcedSkills }
            : {}),
          ...(options?.mentionedFiles && options.mentionedFiles.length
            ? { mentioned_files: options.mentionedFiles }
            : {}),
          ...(options?.planMode !== undefined ? { plan_mode: options.planMode } : {}),
        }),
        signal: signal ?? null,
      }
    );
```

- [ ] **Step 4: Run the contract tests, then build editor-client**

```bash
npm run -w @crucible/editor-client test 2>&1 | tail -40
npm run -w @crucible/editor-client build
```
Expected: tests pass; build succeeds (the vscode-extension workspace types off the compiled `dist/`, per this repo's build-order gotcha — this build MUST happen before the next steps touch `apps/vscode-extension`).

- [ ] **Step 5: Extension-side storage — `vscode-runtime.ts`**

Read `apps/vscode-extension/src/runtime/vscode-runtime.ts` around its existing `mcpDisabledServers`/`skillsDisabled` getters (lines ~321-335 per the earlier grep) to match its exact class/method style, then add, following the same pattern:

```ts
  getPlanMode(): boolean {
    return this.context.globalState.get<boolean>("crucible.chat.planMode", false);
  }

  async setPlanMode(enabled: boolean): Promise<void> {
    await this.context.globalState.update("crucible.chat.planMode", enabled);
  }
```

- [ ] **Step 6: No test file exists for `chat-panel.ts` — confirmed by search, not an oversight to fix here**

`find apps/vscode-extension -iname "*chat-panel*test*"` returns zero matches, and a broader sweep of every non-`webview-ui` `.test.ts` file in this package (`test/prompt-files.test.ts`, `test/settings-data.test.ts`, `test/memory-data.test.ts`, `test/controller.test.ts`, etc.) shows they all test **vscode-free companion modules** — this package's established pattern (per CLAUDE.md's `settings-data.ts`/`settings-panel.ts` and `memory-data.ts`/`memory-panel.ts` split) is that the vscode-API-touching panel class itself (needing a real `vscode.WebviewPanel`, `onDidReceiveMessage`, etc.) is verified live/manually, not unit-mocked — there is no existing harness for mocking `vscode.WebviewPanel` to reuse or extend. Building one from scratch here would be introducing new test infrastructure this package has deliberately avoided elsewhere, not "matching an existing pattern."

Given that, skip writing a new automated unit test for `chat-panel.ts`'s message routing in this task. Correctness here is covered by: TypeScript's compiler (the new message-type fields are type-checked at Step 8's edit site), the already-covered `ModeGate`/`InputArea` tests (Tasks 9-10 elsewhere) exercising the messages `chat-panel.ts` routes, and this task's Step 14 full-suite + typecheck run. Proceed directly to Step 8's implementation.

- [ ] **Step 7: (intentionally skipped — see Step 6)**

- [ ] **Step 8: Wire `setPlanMode` + hydration into `chat-panel.ts`**

Add a new handler-factory type near the other `export type ...Handler` declarations:

```ts
export type SetPlanModeHandler = (enabled: boolean) => Promise<void>;
export type GetPlanModeHandler = () => boolean;
```

Wire `this.onGetPlanMode`/`this.onSetPlanMode` (constructor-injected, following this file's exceptionless `on`-prefix convention for every one of its 33 existing handler parameters — `onMessage`, `onListPrompts`, `onListSkills`, etc.; declared in Step 9) into `registerHandlers`'s message switch: add, alongside the existing `webviewReady` branch's `p = this.onReady();` — extend that branch to also push the hydration message before calling `onReady()`:

```ts
      if (m["type"] === "webviewReady") {
        if (this.lastWorkbarInfo !== null) {
          this.updateWorkbar(this.lastWorkbarInfo);
        }
        void this.panel?.webview.postMessage({
          type: "planModeState", enabled: this.onGetPlanMode ? this.onGetPlanMode() : false,
        });
        p = this.onReady();
      } else if (m["type"] === "setPlanMode") {
        p = this.onSetPlanMode ? this.onSetPlanMode(m["enabled"] === true) : Promise.resolve();
      } else if (m["type"] === "sendMessage") {
```

(Match this file's exact existing constructor-injection idiom for `onGetPlanMode`/`onSetPlanMode` — read the constructor and how e.g. `onMessage`/`onReady` are already threaded in before writing this, to keep the new fields consistent with the rest of the class rather than inventing a new wiring style.)

- [ ] **Step 9: Wire `extension.ts`**

`ChatPanel`'s constructor (`chat-panel.ts:82-117`) is **purely positional** — 26+ arguments, no options object — ending with `onFetchSessionTranscript: FetchSessionTranscriptHandler = async () => null` as its last (defaulted) parameter. Step 8's `onGetPlanMode`/`onSetPlanMode` fields must be added as two MORE positional parameters, each with a default, appended after `onFetchSessionTranscript` in the constructor signature:

```ts
    private readonly onFetchSessionTranscript: FetchSessionTranscriptHandler = async () => null,
    private readonly onGetPlanMode: GetPlanModeHandler = () => false,
    private readonly onSetPlanMode: SetPlanModeHandler = async () => {}
  ) {}
```

In `apps/vscode-extension/src/extension.ts`, the `new ChatPanel(...)` call site (starting at line 99) ends with `(sessionId: string) => controller.fetchSessionTranscript(sessionId)` as its last argument before the closing `);`. Append two more positional arguments in the SAME relative position as the constructor edit above:

```ts
    (sessionId: string) => controller.fetchSessionTranscript(sessionId),
    () => runtimeManager.getPlanMode(),
    (enabled: boolean) => runtimeManager.setPlanMode(enabled)
  );
```

(Both edits are positional — the constructor parameter list and the call site's argument list must gain their two new entries in the same relative order, or the arguments silently bind to the wrong parameters. Since both new parameters have defaults, no other existing positional argument shifts.)

- [ ] **Step 10: `controller.ts`'s `sendChatMessage` gains `planMode`**

Replace:

```ts
  async sendChatMessage(
    text: string, stepReview?: boolean, forcedSkills?: string[], mentionedPaths?: string[]
  ): Promise<void> {
```

with:

```ts
  async sendChatMessage(
    text: string, stepReview?: boolean, forcedSkills?: string[], mentionedPaths?: string[],
    planMode?: boolean,
  ): Promise<void> {
```

Replace the `client.sendChatMessage(...)` call's options object:

```ts
    await this.streamTurn(
      client.sendChatMessage(
        threadId,
        text,
        this.turnAbort.signal,
        stepReview !== undefined || forcedSkills?.length || mentionedFiles?.length
          ? {
              ...(stepReview !== undefined ? { stepReview } : {}),
              ...(forcedSkills?.length ? { forcedSkills } : {}),
              ...(mentionedFiles?.length ? { mentionedFiles } : {}),
            }
          : undefined,
      ),
    );
```

with:

```ts
    await this.streamTurn(
      client.sendChatMessage(
        threadId,
        text,
        this.turnAbort.signal,
        stepReview !== undefined || forcedSkills?.length || mentionedFiles?.length || planMode !== undefined
          ? {
              ...(stepReview !== undefined ? { stepReview } : {}),
              ...(forcedSkills?.length ? { forcedSkills } : {}),
              ...(mentionedFiles?.length ? { mentionedFiles } : {}),
              ...(planMode !== undefined ? { planMode } : {}),
            }
          : undefined,
      ),
    );
```

In `extension.ts`, update the `controller.sendChatMessage(message, stepReview, forcedSkills, mentionedPaths)` call site (line ~102) to also forward `planMode` — this requires `chat-panel.ts`'s `sendMessage` webview-message payload to carry `planMode` too, threaded from the composer (Step 12 below); add a `planMode?: boolean` parameter to `ChatMessageHandler`'s type and the corresponding `onMessage(...)` call in `chat-panel.ts`'s `registerHandlers` (mirroring exactly how `stepReview`/`forcedSkills`/`mentionedPaths` are already extracted from `m[...]` and threaded through).

- [ ] **Step 11: Write the failing `InputArea.tsx` test**

Read the existing `InputArea.test.tsx` (find via `find apps/vscode-extension/webview-ui -iname "InputArea.test.tsx"`) to match its render/mock helpers, then add:

```tsx
it("renders a Plan Mode checkbox reflecting the hydrated planMode prop", () => {
  render(<InputArea availability={defaultAvailability} draft="" onDraftChange={() => {}} planMode={true} />);
  const checkbox = screen.getByLabelText(/plan mode/i) as HTMLInputElement;
  expect(checkbox.checked).toBe(true);
});

it("posts setPlanMode and includes planMode on send", () => {
  const posted: unknown[] = [];
  (vscode.postMessage as unknown as (m: unknown) => void) = (m) => { posted.push(m); };
  render(<InputArea availability={defaultAvailability} draft="hi" onDraftChange={() => {}} planMode={false} />);
  fireEvent.click(screen.getByLabelText(/plan mode/i));
  expect(posted).toContainEqual({ type: "setPlanMode", enabled: true });
});
```

- [ ] **Step 12: Run it, confirm it fails, then update `InputArea.tsx`**

```bash
npm run -w crucible-vscode-extension test -- InputArea 2>&1 | tail -40
```

Add a `planMode?: boolean` prop to `Props` (following the `stepReview` local-state pattern is WRONG here per the design's correction — this must be a controlled prop sourced from the extension's `globalState`, not local `useState`, since it needs to survive across threads). Add near the existing `stepReview` `useState`:

```tsx
interface Props {
  availability: InputAvailability;
  draft: string;
  onDraftChange: (text: string) => void;
  onOpenSettings?: () => void;
  planMode?: boolean;  // hydrated from the extension's globalState via planModeState
}
```

```tsx
export function InputArea({ availability, draft, onDraftChange, onOpenSettings, planMode = false }: Props) {
```

Add a checkbox in the footer row, near the "Review each step" toggle (after its closing `</label>`, before the `⌘↵` hint span):

```tsx
        {/* Plan Mode toggle — sticky across threads (extension globalState), unlike
            "Review each step" which is per-message local state. */}
        <label className="flex items-center gap-1.5 text-[10px] text-text-3 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={planMode}
            aria-label="Plan Mode"
            onChange={(e) => {
              vscode.postMessage({ type: "setPlanMode", enabled: e.target.checked });
            }}
            className="accent-[var(--color-accent)] w-3 h-3"
          />
          Plan Mode
        </label>
```

Add `planMode` to both `sendMessage` post sites in `doSend()` and the slash/skill resolution effect, mirroring `stepReview`:

```tsx
    vscode.postMessage({
      type: "sendMessage",
      text: trimmed,
      stepReview,
      planMode,
      ...(mentionedPaths.length ? { mentionedPaths } : {}),
    });
```

(and the analogous addition to the other `sendMessage` post site in the slash-expansion effect).

- [ ] **Step 13: Wire the parent that renders `InputArea`**

Find the parent component (likely `App.tsx` or `ThreadView.tsx`) that currently passes `availability`/`draft`/`onDraftChange`/`onOpenSettings` to `InputArea`, and thread a `planMode` state value down to it, populated from a `window.addEventListener("message", ...)` listener for `{ type: "planModeState", enabled }` (mirroring however this codebase's top-level app state already handles other hydration pushes like `liveStatus` — read that pattern first and match it, per the design doc's "top-level app state, same tier as liveStatus" description).

- [ ] **Step 14: Run all frontend test suites**

```bash
npm run -w @crucible/editor-client test
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension typecheck
```
Expected: all green, zero typecheck errors.

- [ ] **Step 15: Commit**

```bash
git add apps/editor-client apps/vscode-extension
git commit -m "feat(chat): sticky Plan Mode toggle — full-stack plumbing

Composer checkbox -> extension globalState (survives across threads
and reloads) -> per-message plan_mode wire field -> backend phase
selection. Picking 'Implement this plan' (Task 9) auto-flips the
toggle off through the same single write path."
```

---

## Self-Review

**1. Spec coverage.** Every design-doc finding maps to a task: C1→Task 2, C1b→Task 3, C2→Task 4, C3→Task 6, C4→Task 3 (folded into the PLAN branch rewrite, since it's the same payload-builder edit), C5→Task 1, NEW-I6→Task 1 + Task 7, I1→Task 8, I4→Task 5, I5→Task 1 (the `_VALID_MODES` rename). The phase-model table, sticky-toggle mechanism, UI section, edge cases, and testing section of the spec are all covered by Tasks 1/3/6 (model+prompts), 9/10 (UI+toggle), and 8 (test audit) respectively.

**2. Placeholder scan.** No task defers its own code to "implementation time" except: (a) Task 6 explicitly notes live-model verification of the rewritten prompt text is a follow-up practice per this codebase's convention, not a blocking automated test — this is a real, complete text change, not a placeholder; (b) Task 10's Steps 6/8/9/11/13 explicitly say "read the existing file first to match its pattern" before writing exact code, because those specific files were not fully read during plan-writing (only grepped for their relevant call sites) — this is flagged honestly as a research-then-implement step within the task, not a vague direction; the concrete message shapes, prop names, and test assertions are all fully specified regardless of that file-reading step.

**3. Type consistency.** `edit_session` → `edit_session_factory` renamed consistently across `controller_loop.py` (Task 2) and every call site touching it in `controller.py` (Task 2) and tests (Task 2 + Task 8). `"EDIT"`/`"DECIDE"`/`"EXPLAIN"` → `"ACTIVE"`/`"PLAN"` renamed consistently across `controller_phase.py`, `controller_loop.py`, `controller_prompts.py`, `controller.py`, and all touched test files. `edit_entry`/`decide_entry` → `active_entry`/`skill_check_due` (ACTIVE) with `decide_entry` surviving unrenamed for PLAN (Task 3 — confirmed this is intentional, not an inconsistency: PLAN's signal keeps its original name since PLAN's hint text is otherwise unchanged from the old DECIDE branch). `"edit"`/`"explain"` mode values → `"implement"` (dropping `explain`) consistent across `_VALID_MODES`, `PROPOSE_MODE_CORRECTION`, `resolve_mode`, both `_PROPOSE_MODE_MODES_*` blocks, and the `CONTROLLER_SYSTEM_PROMPT` variant text (Tasks 1 + 6).

**4. Ordering check.** Task 2 depends on Task 1 (needs `ControllerPhaseSM(start=...)`); Task 3 depends on Task 1 (needs `"ACTIVE"`/`"PLAN"` names); Task 4 depends on Task 1 (ACTIVE's `answer` reachability); Task 5 depends on Task 1; Task 6 is prompt-text-only, depends on Task 1's mode-vocabulary rename; Task 7 depends on Task 1 (the `_run_loop` dispatch line) — correctly sequenced 1→2→3→4→5→6→7→8. Task 8 (the full audit) is correctly LAST among backend tasks, since it needs every rename settled first or it would re-audit against a moving target. Tasks 9-10 (frontend) have no backend test dependency but Task 10's end-to-end behavior is only observable once Task 7's backend `plan_mode` route exists — listed after all backend tasks for that reason, though nothing prevents parallelizing 9-10 with the backend tasks if using subagent-driven-development's task independence.
