# Controller `progress` Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the chat controller a non-terminal `progress` action so the model can post a short user-visible status note mid-turn and keep working, instead of ending the turn with `answer` when it only wants to narrate.

**Architecture:** A new `progress` variant is added to the controller's flat + tight response schemas and to both phases' allowed-action lists. `ControllerLoop._iterate` gains a dispatch branch that persists the note (durable `agent/text` message via a threaded callback), broadcasts a `chat_progress` SSE event, appends an observation to history, and continues the loop. Two mechanical guardrails (no-two-in-a-row cap + exact-text dedup) route through the existing `_MAX_MALFORMED` correction chain. `answer` stays strictly terminal; the answer-intent-divergence guard's rejection text is updated to point the model at `progress` as the outlet.

**Tech Stack:** Python 3.13 / FastAPI (agentd-py backend), TypeScript (editor-client contracts + vscode-extension controller), React + Vite + Tailwind (webview-ui), pytest + vitest.

## Global Constraints

- **Branch base:** all work lands on `investigate/skill-handoff-gap` (the answer-intent-divergence guard this feature extends lives there, uncommitted, in worktree `.claude/worktrees/agent-aa9fd20186f12b845/services/agentd-py/`). Do NOT branch from `feat/merged-decide-edit-phase`. Confirm with `git -C <worktree> branch --show-current` → `investigate/skill-handoff-gap` before starting.
- **`answer` semantics unchanged** — it remains terminal. `progress` is additive; never add a flag to `answer`.
- **No-superiority framing** (project rule) — prompt copy states when each of `progress`/`answer` shines; never rank one over the other.
- **Note cap:** 500 chars, applied at dispatch (`note[:500]`).
- **Best-effort persistence** — a persist-callback failure logs and continues; a narration write must never kill a turn.
- **Guardrails are mechanical, not prompt-only** — prose teaching already failed for this exact pattern (Finding 3 reproduced live).
- **Backend test invocation** (per CLAUDE.md): run plain `pytest <paths>` (never add `-q` — `pyproject.toml` already sets it; a second `-q` suppresses the summary). Never pipe pytest (masks exit code). From `services/agentd-py` with the venv active.
- **Spec:** `docs/superpowers/specs/2026-07-25-controller-progress-action-design.md`.

---

### Task 1: Backend schema + prompt — the `progress` variant

Adds `progress` to the response schema (flat + tight), both phases' allowed types, and the system-prompt teaching. `ControllerPhaseSM.allowed_types()` reads `_PHASE_TYPES` directly (`controller_phase.py:36`), so adding `progress` there automatically makes it phase-allowed — no SM change needed.

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py`
- Test: `services/agentd-py/tests/test_controller_progress_schema.py` (create)

**Interfaces:**
- Produces: schema recognizes `type:"progress"` with a required `note` field, in both `ACTIVE` and `PLAN`. `controller_response_schema(phase=..., tight=True)` yields a `oneOf` branch for `progress`.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_controller_progress_schema.py`:

```python
from agentd.chat.controller_prompts import (
    CONTROLLER_RESPONSE_SCHEMA,
    _PHASE_TYPES,
    _VARIANT_SPECS,
    controller_response_schema,
)


def test_progress_in_both_phase_types():
    assert "progress" in _PHASE_TYPES["ACTIVE"]
    assert "progress" in _PHASE_TYPES["PLAN"]


def test_progress_variant_spec_requires_note():
    spec = _VARIANT_SPECS["progress"]
    assert spec["required"] == ["note"]
    assert "note" in spec["properties"]


def test_flat_schema_exposes_note_and_progress_enum():
    assert "progress" in CONTROLLER_RESPONSE_SCHEMA["properties"]["type"]["enum"]
    assert "note" in CONTROLLER_RESPONSE_SCHEMA["properties"]


def test_flat_phase_schema_trims_progress_into_enum():
    schema = controller_response_schema(phase="ACTIVE")
    assert "progress" in schema["properties"]["type"]["enum"]


def test_tight_schema_has_progress_branch():
    schema = controller_response_schema(phase="ACTIVE", tight=True)
    branches = schema["oneOf"]
    progress = next(
        b for b in branches if b["properties"]["type"]["const"] == "progress"
    )
    assert "note" in progress["required"]
    assert progress["additionalProperties"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_controller_progress_schema.py -v`
Expected: FAIL — `KeyError: 'progress'` on `_VARIANT_SPECS["progress"]` / `progress` not in enum.

- [ ] **Step 3: Add `progress` to the schema and phase tables**

In `controller_prompts.py`, in `CONTROLLER_RESPONSE_SCHEMA["properties"]["type"]["enum"]`, add `"progress"`:

```python
        "type": {
            "type": "string",
            "enum": ["tool_call", "answer", "clarify", "propose_mode", "edit", "submit_changes", "progress"],
        },
```

Add a `note` property to the flat schema's `properties` (next to the `answer`/`clarify` block comment):

```python
        # answer / clarify
        "answer": {"type": "string"},
        "question": {"type": "string"},
        # progress — a non-terminal user-visible status note; the turn continues
        "note": {"type": "string"},
```

Add `"progress"` to BOTH lists in `_PHASE_TYPES`:

```python
    "PLAN": ["tool_call", "answer", "clarify", "propose_mode", "progress"],
    ...
    "ACTIVE": ["tool_call", "answer", "clarify", "edit", "submit_changes", "progress"],
```

Add a `progress` entry to `_VARIANT_SPECS`:

```python
    "progress": {"required": ["note"], "properties": {"note": _STR}},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_controller_progress_schema.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Add the system-prompt teaching block**

In `CONTROLLER_SYSTEM_PROMPT`, add a variant line in the `OUTPUT` block right after the `answer` variant (`Variant — answer` … block, ends before `Variant — clarify`). Insert:

```
Variant — progress (post a short status note WITHOUT ending the turn): {type, note}
  Use when you want to tell the user what you are doing or about to do while you keep working
  in the SAME turn — e.g. between finishing one file and starting the next in a multi-step change.
  The 'note' is shown to the user immediately; the turn does NOT end and you run again right after.
  {"type":"progress","thought":"plan saved; starting task 1","note":"Plan saved. Now implementing task 1 — creating the commit-log struct."}
  progress is for a status update mid-work; 'answer' is for the finished, self-contained reply that
  ENDS the turn. If you have more to do this turn, use progress and then take the next action; do not
  use 'answer' to describe a step you have not taken yet.
```

Update the existing `answer` variant's "NEVER use 'answer' to narrate a NEXT step" guidance to cross-reference progress. Find the line in the `answer` variant block:

```
  emit that tool_call (or propose_mode/edit) THIS turn instead of describing it in "answer". Only
  emit "answer" when you are delivering the actual finished content, not a preview of intent.
```

Replace with:

```
  emit that tool_call (or propose_mode/edit) THIS turn instead of describing it in "answer" — or, if
  you just want to tell the user what you are about to do without ending the turn, emit "progress".
  Only emit "answer" when you are delivering the actual finished content, not a preview of intent.
```

- [ ] **Step 6: Verify prompt still builds and run ruff**

Run: `python -c "from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT; print('progress' in CONTROLLER_SYSTEM_PROMPT)"`
Expected: `True`

Run: `ruff check agentd/chat/controller_prompts.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/tests/test_controller_progress_schema.py
git commit -m "feat(chat): add progress variant to controller response schema + prompt"
```

---

### Task 2: Backend loop — `progress` dispatch, cap, dedup, divergence redirect

Adds the constructor param, two guardrail corrections, empty-note handling, the dispatch branch, and updates the divergence guard's text. This is the core of the feature.

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py`
- Test: `services/agentd-py/tests/test_controller_progress_action.py` (create)

**Interfaces:**
- Consumes: `_MAX_MALFORMED`/`consecutive_malformed` correction chain (`controller_loop.py:660-720`), `assistant_turn` (`reasoning/react_common.py:12`), the `seen` dedup dict pattern, `_broadcaster.broadcast`.
- Produces: `ControllerLoop(..., progress_note_cb: Callable[[str], Awaitable[None]] | None = None)`. On a `progress` response, calls `progress_note_cb(note)`, broadcasts `{"type":"chat_progress","payload":{"note": note}}`, appends a `tool_result` observation, continues.

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_controller_progress_action.py`. Use the existing scripted-engine test harness. Model this on the fixtures in `tests/test_controller_answer_intent_divergence.py` (same directory — open it to reuse its `ControllerLoop` construction helper and `ScriptedReasoningEngine` shape). The tests:

```python
import asyncio

import pytest

from agentd.chat.controller_loop import (
    ControllerLoop,
    _progress_repeat_correction,
    _progress_dedup_correction,
    _empty_action_correction,
    _answer_intent_divergence_correction,
)


# ---- pure correction-unit tests (no loop) ----

def test_repeat_correction_fires_only_after_progress():
    resp = {"type": "progress", "note": "still working"}
    assert _progress_repeat_correction(resp, "progress", True) is not None
    assert _progress_repeat_correction(resp, "progress", False) is None
    assert _progress_repeat_correction({"type": "answer"}, "answer", True) is None


def test_dedup_correction_fires_on_exact_repeat():
    resp = {"type": "progress", "note": "creating crc.go"}
    seen = {"creating crc.go"}
    assert _progress_dedup_correction(resp, "progress", seen) is not None
    assert _progress_dedup_correction({"type": "progress", "note": "new"}, "progress", seen) is None
    assert _progress_dedup_correction(resp, "answer", seen) is None


def test_empty_note_correction():
    assert _empty_action_correction({"type": "progress", "note": "  "}, "progress") is not None
    assert _empty_action_correction({"type": "progress", "note": "ok"}, "progress") is None


def test_divergence_correction_names_progress():
    msg = _answer_intent_divergence_correction(
        {"type": "answer", "thought": "I should use write_todos next",
         "answer": "Let me start by calling write_todos."},
        "answer", frozenset({"write_todos"}),
    )
    assert msg is not None
    assert "progress" in msg
```

For the integration tests, reuse the harness from `test_controller_answer_intent_divergence.py`. Add:

```python
# ---- integration through ControllerLoop.run() ----
# Reuse the scripted-engine + loop builder from test_controller_answer_intent_divergence.py.
# (Import or copy its `build_loop(responses, *, progress_note_cb=None)` helper — a
# ScriptedReasoningEngine returning `responses` in order, real ControllerLoop with a
# NO_OP registry + ACTIVE phase SM.)

def test_progress_persists_and_continues():
    notes: list[str] = []

    async def cb(note: str) -> None:
        notes.append(note)

    loop = build_loop(
        [
            {"type": "progress", "thought": "t", "note": "Working on task 1."},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = asyncio.run(loop.run({"goal": "x"}, max_iters=8))
    assert outcome.kind == "answer"
    assert notes == ["Working on task 1."]  # persisted once
    # loop continued past the progress note to the terminal answer


def test_progress_repeat_is_corrected():
    notes: list[str] = []

    async def cb(note: str) -> None:
        notes.append(note)

    loop = build_loop(
        [
            {"type": "progress", "thought": "t", "note": "first"},
            {"type": "progress", "thought": "t", "note": "second"},  # rejected: two in a row
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = asyncio.run(loop.run({"goal": "x"}, max_iters=8))
    assert outcome.kind == "answer"
    assert notes == ["first"]  # the second never dispatched


def test_progress_dedup_is_corrected():
    notes: list[str] = []

    async def cb(note: str) -> None:
        notes.append(note)

    loop = build_loop(
        [
            {"type": "progress", "thought": "t", "note": "same"},
            {"type": "tool_call", "thought": "t", "tool": "read_file", "args": {"path": "a"}},
            {"type": "progress", "thought": "t", "note": "same"},  # exact repeat → rejected
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = asyncio.run(loop.run({"goal": "x"}, max_iters=8))
    assert notes == ["same"]  # dispatched once; the repeat corrected


def test_progress_without_cb_still_continues():
    loop = build_loop(
        [
            {"type": "progress", "thought": "t", "note": "no cb wired"},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=None,
    )
    outcome = asyncio.run(loop.run({"goal": "x"}, max_iters=8))
    assert outcome.kind == "answer"


def test_progress_allowed_in_plan_phase():
    notes: list[str] = []

    async def cb(note: str) -> None:
        notes.append(note)

    loop = build_loop(
        [
            {"type": "progress", "thought": "t", "note": "exploring"},
            {"type": "answer", "thought": "t", "answer": "Here's the approach."},
        ],
        progress_note_cb=cb,
        phase="PLAN",
    )
    asyncio.run(loop.run({"goal": "x"}, max_iters=8))
    assert notes == ["exploring"]
```

If `test_controller_answer_intent_divergence.py` has no reusable `build_loop`, add a local one at the top of this file (a `ScriptedReasoningEngine` that pops from a list on each `create_controller_step`, a NO_OP-style registry with `read_file` defined, `ControllerPhaseSM(phase)`, and `ControllerLoop(...)` with the new `progress_note_cb`). Keep it under 40 lines.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_controller_progress_action.py -v`
Expected: FAIL — `ImportError: cannot import name '_progress_repeat_correction'`.

- [ ] **Step 3: Add the constructor param**

In `ControllerLoop.__init__` (`controller_loop.py:~308`), add a param after `active_skill_persist_cb`:

```python
        active_skill_persist_cb: Callable[[str | None], Awaitable[None]] | None = None,
        progress_note_cb: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
```

And store it (near the other cb assignments):

```python
        # Persists a non-terminal `progress` note as a durable transcript message. The
        # loop broadcasts the live chat_progress event itself; this cb is the durable
        # half (reload). None → live-only (broadcast still fires).
        self._progress_note_cb = progress_note_cb
```

- [ ] **Step 4: Add the two guardrail corrections + empty-note handling**

Add two module-level pure functions near `_answer_intent_divergence_correction` (`controller_loop.py:~201`):

```python
def _progress_repeat_correction(
    resp: dict[str, object], atype: str, last_was_progress: bool,
) -> str | None:
    """Reject a `progress` note that immediately follows another `progress` with no real
    action between — a weak model can turn a free non-terminal action into a narration
    attractor (spam notes instead of acting). Mirrors the emit_patch-dedup discipline;
    routes through the same _MAX_MALFORMED correction chain (no new retry primitive)."""
    if atype != "progress" or not last_was_progress:
        return None
    return (
        "You already posted a progress note and took no action after it. A progress note "
        "does NOT count as doing the work. Take the actual next action now — emit "
        "type='tool_call' / 'edit' / 'submit_changes' (or 'answer' if you are truly done)."
    )


def _progress_dedup_correction(
    resp: dict[str, object], atype: str, seen_notes: set[str],
) -> str | None:
    """Reject an exact-duplicate progress note already emitted this turn (same attractor
    class as _progress_repeat_correction, but catches non-adjacent repeats)."""
    if atype != "progress":
        return None
    note = str(resp.get("note", "")).strip()
    if note and note in seen_notes:
        return (
            "You already posted that exact progress note this turn. Do not repeat it — "
            "take the next real action instead (tool_call / edit / submit_changes / answer)."
        )
    return None
```

In `_empty_action_correction` (`controller_loop.py:~120`), add a `progress` branch alongside the `answer`/`clarify` ones:

```python
    if atype == "progress" and _blank("note"):
        return (
            "Your 'note' was empty. Re-emit type='progress' with a short non-empty 'note' "
            "describing what you are doing, or take a real action instead."
        )
```

- [ ] **Step 5: Update the divergence guard's redirect text**

In `_answer_intent_divergence_correction` (`controller_loop.py:~226`), change the final return to name `progress` as the outlet:

```python
    return (
        f"Your own thought/answer describes a next step you have not taken ('{mentioned}' "
        f"mentioned alongside forward-looking language like \"I should\"/\"let me start\"). "
        "'answer' ENDS the turn — nothing you described will actually run, and the user will "
        f"have to prompt you again just to get you to do what you already said. If '{mentioned}' "
        "(or whatever the real next action is) is genuinely next, TAKE it now: emit "
        "type='tool_call' with that tool (or type='edit'/'submit_changes' if that's the real "
        "next action) instead of narrating it in 'answer'. If you only want to TELL the user "
        "what you are about to do without ending the turn, emit type='progress' with a 'note', "
        "then take the action."
    )
```

- [ ] **Step 6: Wire the corrections into the chain + add loop state**

In `_iterate`, initialize the two state vars alongside `seen` (near `controller_loop.py:~549`, where `seen: dict[str, int] = {}` is — that's actually in `run()`; the loop counter is in `_iterate`). Add inside `_iterate`, just before the `for iteration in range(...)` loop:

```python
        # progress-action guardrail state (see _progress_repeat_correction / _dedup).
        last_was_progress = False
        seen_notes: set[str] = set()
```

Add both corrections to the `correction = (...)` chain (`controller_loop.py:~665`), after `_answer_intent_divergence_correction`:

```python
            correction = (
                MALFORMED_CORRECTION
                if atype not in self._allowed_action_types()
                else _propose_mode_correction(resp, self._allowed_modes_for_current_phase()) if atype == "propose_mode"
                else _reserved_tool_name_correction(resp, atype)
                or _decide_state_change_correction(resp, self._sm.phase)
                or _empty_action_correction(resp, atype)
                or _answer_intent_divergence_correction(resp, atype, tool_names)
                or _progress_repeat_correction(resp, atype, last_was_progress)
                or _progress_dedup_correction(resp, atype, seen_notes)
            )
```

Immediately after `consecutive_malformed = 0` (`controller_loop.py:~715`, the line that runs once a response passes all corrections), capture the pre-reset value (so the progress branch can restore it — a note is not evidence of progress, per the spec) and reset the adjacency flag so any accepted non-progress action clears it:

```python
            prev_malformed = consecutive_malformed
            consecutive_malformed = 0
            # Any accepted action clears the progress-adjacency flag; the progress branch
            # below sets it back to True. (Placed here so it only runs on an ACCEPTED
            # action — a corrected response `continue`s above and never reaches this.)
            last_was_progress = False
```

- [ ] **Step 7: Add the dispatch branch**

Add the `progress` branch after the `answer` branch's `return` and before `if atype == "tool_call":` (`controller_loop.py:~739`):

```python
            if atype == "progress":
                # A progress note is not evidence of progress: preserve the malformed
                # counter (spec) so a stuck model can't launder its malformed streak
                # through interleaved notes to evade _MAX_MALFORMED.
                consecutive_malformed = prev_malformed
                note = str(resp.get("note", "")).strip()[:500]
                seen_notes.add(note)
                last_was_progress = True
                # Live event (durable half is the cb below). Mirrors tool_call's
                # broadcast-live / persist-separately split.
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "chat_progress", "payload": {"note": note}})
                if self._progress_note_cb is not None:
                    try:
                        await self._progress_note_cb(note)
                    except Exception:  # noqa: BLE001 — a narration write must never kill a turn
                        logger.warning("[controller] progress_note_cb failed", exc_info=True)
                history.append(assistant_turn(resp))
                history.append({
                    "role": "tool_result", "tool": "",
                    "content": (
                        f'Progress note posted to the user: "{note}". This did NOT end the '
                        "turn — now take the actual next action."
                    ),
                })
                continue
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_controller_progress_action.py -v`
Expected: PASS (all tests).

- [ ] **Step 9: Targeted regression + ruff**

Run: `pytest tests/ -k "controller or skills or chat"`
Expected: the prior 317 still pass, plus the new ones; 0 failures.

Run: `ruff check agentd/chat/controller_loop.py`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/tests/test_controller_progress_action.py
git commit -m "feat(chat): progress dispatch branch + cap/dedup guardrails in controller loop"
```

---

### Task 3: Backend wiring — persist the note from `ChatController`

Threads a real `progress_note_cb` into the loop that persists a durable `agent/text` message tagged `metadata.progress=true` (the durable half; the loop already broadcast the live event).

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Test: `services/agentd-py/tests/test_controller_progress_persist.py` (create)

**Interfaces:**
- Consumes: `ControllerLoop(..., progress_note_cb=...)` from Task 2; `self._store.append_message`, `ChatMessage` (mirror `_write_breadcrumb`, `controller.py:~350`).
- Produces: `ChatController._progress_note_cb(thread_id, note)` async method.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_controller_progress_persist.py`. Reuse the `ChatController` construction pattern from an existing controller test (e.g. `test_controller_loop_answer_ledger_gate.py` or `test_controller_required_subskill_autoload.py` in the same dir — copy its fixture that builds a `ChatController` with an in-memory store + `ScriptedReasoningEngine`). Drive one turn whose scripted engine emits `progress` then `answer`, then assert a persisted message with `metadata.progress` exists:

```python
import asyncio


def test_progress_note_persisted_as_durable_message(build_controller):
    # build_controller: fixture/helper yielding (controller, store, thread_id) with a
    # ScriptedReasoningEngine that returns the given responses in order.
    controller, store, thread_id = build_controller(
        [
            {"type": "progress", "thought": "t", "note": "Implementing task 1."},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ]
    )
    asyncio.run(controller.handle_message(thread_id, "do the thing", channel_id="c"))
    msgs = store.get_thread(thread_id).messages
    progress_msgs = [m for m in msgs if m.metadata.get("progress") is True]
    assert len(progress_msgs) == 1
    assert progress_msgs[0].content == "Implementing task 1."
    assert progress_msgs[0].role == "agent"
    assert progress_msgs[0].type == "text"
```

(Match `handle_message`'s real signature — check the existing controller test's call; pass whatever `step_review`/`plan_mode` args it requires.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_controller_progress_persist.py -v`
Expected: FAIL — no message with `metadata.progress` (cb not wired yet).

- [ ] **Step 3: Add the persist callback**

In `controller.py`, add a method next to `_write_breadcrumb` (`controller.py:~350`):

```python
    async def _progress_note_cb(self, thread_id: str, note: str) -> None:
        """Persist a non-terminal progress note as a durable transcript message. The
        live chat_progress event is broadcast by the loop; this is the reload half.
        Best-effort — the loop already guards the call, but keep the write minimal."""
        self._store.append_message(thread_id, ChatMessage(
            role="agent", content=note, type="text", metadata={"progress": True}))
```

- [ ] **Step 4: Thread it into the loop construction**

In `_run_loop`, at the `ControllerLoop(...)` construction (`controller.py:~430`), add the param:

```python
            skill_catalog_loader=skill_catalog_loader,
            active_skill_persist_cb=active_skill_persist_cb,
            progress_note_cb=partial(self._progress_note_cb, thread_id))
```

(`partial` is already imported — it's used for `active_skill_persist_cb` just above.)

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_controller_progress_persist.py -v`
Expected: PASS.

- [ ] **Step 6: Regression + ruff**

Run: `pytest tests/ -k "controller or chat"`
Expected: 0 failures.

Run: `ruff check agentd/chat/controller.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_controller_progress_persist.py
git commit -m "feat(chat): persist controller progress notes as durable transcript messages"
```

---

### Task 4: editor-client contract — `chat_progress` StreamEvent

Adds the event to the TS discriminated union so both the extension and any typed consumer know its shape.

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts:205`
- Test: `apps/editor-client/src/contracts/task-contracts.test.ts` (append) OR a new small type test — see Step 1.

**Interfaces:**
- Produces: `StreamEvent` union member `{ type: "chat_progress"; payload: { note: string } }`.

- [ ] **Step 1: Write the failing test**

`StreamEvent` is a pure TS union (SSE events are not Zod-parsed), so the "test" is a compile-time assertion. Append to `apps/editor-client/src/contracts/task-contracts.test.ts` (or create it if absent — check first with `ls apps/editor-client/src/contracts/`):

```typescript
import { describe, it, expect } from "vitest";
import type { StreamEvent } from "./task-contracts";

describe("StreamEvent chat_progress", () => {
  it("accepts a chat_progress event shape", () => {
    const ev: StreamEvent = { type: "chat_progress", payload: { note: "hi" } };
    expect(ev.type).toBe("chat_progress");
    // @ts-expect-error note is required
    const bad: StreamEvent = { type: "chat_progress", payload: {} };
    void bad;
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w @crucible/editor-client test -- task-contracts`
Expected: FAIL — TS error: `"chat_progress"` not assignable to `StreamEvent` (and the `@ts-expect-error` is unused → also errors).

- [ ] **Step 3: Add the union member**

In `task-contracts.ts`, add after the `chat_breadcrumb` line (`:205`):

```typescript
  | { type: "chat_breadcrumb"; payload: { text: string; task_id: string } }
  | { type: "chat_progress"; payload: { note: string } }
```

- [ ] **Step 4: Run test + build to verify pass**

Run: `npm run -w @crucible/editor-client test -- task-contracts`
Expected: PASS.

Run: `npm run -w @crucible/editor-client build`
Expected: builds clean (the extension types off the compiled `dist/` — this must succeed before Task 5).

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client/src/contracts/task-contracts.ts apps/editor-client/src/contracts/task-contracts.test.ts
git commit -m "feat(editor-client): add chat_progress StreamEvent"
```

---

### Task 5: Extension controller — render `chat_progress` live

Handles the live event in both stream loops, appending a chat message tagged `metadata.progress` so it renders immediately (durable copy arrives on reload from Task 3).

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts` (two sites: `~752` in `streamTurn`, `~1018` in the channel handler — both currently branch on `chat_breadcrumb`)
- Test: `apps/vscode-extension/src/test/controller.test.ts` (append — locate the existing controller stream test)

**Interfaces:**
- Consumes: `StreamEvent` `chat_progress` (Task 4); `this.ui.appendChatMessage(...)`.

- [ ] **Step 1: Write the failing test**

Find the existing controller stream test (`grep -rn "chat_breadcrumb" apps/vscode-extension/src/test/`). Append a case that feeds a `chat_progress` event through the same stream entry point the breadcrumb test uses and asserts `appendChatMessage` was called with `metadata.progress === true`:

```typescript
it("renders chat_progress as a progress-tagged agent message", async () => {
  // Reuse the harness the chat_breadcrumb test uses (stub ControllerUI capturing
  // appendChatMessage calls, feed events through the same method).
  const ui = makeStubUi();
  const controller = makeController(ui);
  await feedEvents(controller, [
    { type: "chat_progress", payload: { note: "Working on task 1." } },
    { type: "chat_done", payload: {} },
  ]);
  const progressMsg = ui.appendedMessages.find(
    (m) => m.metadata?.progress === true,
  );
  expect(progressMsg?.content).toBe("Working on task 1.");
  expect(progressMsg?.role).toBe("agent");
});
```

(Adapt `makeStubUi`/`makeController`/`feedEvents` to the actual helpers in that test file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- controller`
Expected: FAIL — no message with `metadata.progress` (event falls through unhandled).

- [ ] **Step 3: Add the handler in `streamTurn` (~752)**

After the `chat_breadcrumb` branch at `controller.ts:752`, add:

```typescript
        } else if (event.type === "chat_progress") {
          // Non-terminal mid-turn status note. Render live; the durable copy
          // (metadata.progress) arrives on reload from the backend persist.
          this.ui.appendChatMessage({
            role: "agent",
            content: (event.payload["note"] as string) ?? "",
            type: "text",
            taskId: "",
            timestamp: this.now(),
            metadata: { progress: true },
          });
```

- [ ] **Step 4: Add the handler in the channel loop (~1018)**

After the `chat_breadcrumb` branch at `controller.ts:1018`, add:

```typescript
        } else if (event.type === "chat_progress") {
          this.ui.appendChatMessage({
            role: "agent",
            content: event.payload.note,
            type: "text",
            timestamp: this.now(),
            metadata: { progress: true },
          });
```

(Match the surrounding branch's exact `appendChatMessage` field shape — the two sites differ slightly, as the breadcrumb branches show.)

- [ ] **Step 5: Run test + typecheck to verify pass**

Run: `npm run -w crucible-vscode-extension test -- controller`
Expected: PASS.

Run: `npm run -w crucible-vscode-extension typecheck`
Expected: clean (depends on Task 4's built `dist/` — rebuild editor-client first if it errors on the event type).

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/controller.ts apps/vscode-extension/src/test/controller.test.ts
git commit -m "feat(vscode): render chat_progress live in controller stream handlers"
```

---

### Task 6: Webview — render a persisted progress message

Renders a message tagged `metadata.progress` as a de-emphasized agent line (breadcrumb-adjacent, but distinct: muted italic, no marker-icon stripping).

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx:151` (add a `progress` branch beside the `breadcrumb` one)
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx` (add a `progress` prop + `ProgressLine`)
- Test: `apps/vscode-extension/webview-ui/src/test/components.test.tsx` (append)

**Interfaces:**
- Consumes: `msg.metadata?.progress` (from Task 3 persist + Task 5 live append); `AgentRow`.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/webview-ui/src/test/components.test.tsx` (match its existing render helpers):

```tsx
it("renders a progress-tagged message as a muted progress line", () => {
  const { getByText } = render(
    <MessageRow
      msg={{
        role: "agent",
        content: "Implementing task 1.",
        type: "text",
        timestamp: 0,
        metadata: { progress: true },
      }}
    />,
  );
  const line = getByText("Implementing task 1.");
  expect(line).toBeTruthy();
  // progress line is muted (text-text-3), distinct from a normal answer
  expect(line.className).toContain("text-text-3");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- components`
Expected: FAIL — a `metadata.progress` message currently falls through to the normal `MarkdownContent` path (no `text-text-3`).

- [ ] **Step 3: Route `metadata.progress` in `MessageRow`**

In `MessageRow.tsx`, add a branch before the `breadcrumb` check (`:151`):

```tsx
      if (msg.metadata?.progress === true) {
        return <AgentRow content={msg.content} progress />;
      }

      if (msg.metadata?.breadcrumb === true) {
```

- [ ] **Step 4: Add the `progress` prop + `ProgressLine` to `AgentRow`**

In `AgentRow.tsx`, add `progress?: boolean;` to `Props`, destructure it, and add a render branch. Change the content-render conditional (`:79`) so `progress` is handled first:

```tsx
        {progress ? (
          <ProgressLine text={content} />
        ) : breadcrumb ? (
          <BreadcrumbLine text={content} />
        ) : streaming ? (
```

Add the component next to `BreadcrumbLine` (`:126`):

```tsx
function ProgressLine({ text }: { text: string }) {
  // De-emphasized, breadcrumb-adjacent but distinct: muted italic, no marker stripping.
  return (
    <div className="flex items-center gap-2 text-[11px] text-text-3 italic">
      <Icon name="loader" size={11} className="text-text-3" />
      <span>{text}</span>
    </div>
  );
}
```

(If `"loader"` is not a valid `Icon` name, use one that is — check `apps/vscode-extension/webview-ui/src/components/Icon.tsx` for the union; `"retry"` and `"check"` are known-good from `MARKER_ICONS`. Pick any existing subtle glyph, or omit the `<Icon>` entirely if none fits.)

Add `progress` to the destructure in the `AgentRow` function signature:

```tsx
export function AgentRow({
  content,
  breadcrumb,
  progress,
  thinkingLog,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run -w crucible-vscode-extension test -- components`
Expected: PASS.

- [ ] **Step 6: Full webview + extension test suites**

Run: `npm run -w crucible-vscode-extension test`
Expected: 0 failures (webview 255+ / ext 94+ baselines from memory, plus the new tests).

Run: `npm run -w crucible-vscode-extension typecheck`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/MessageRow.tsx apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx apps/vscode-extension/webview-ui/src/test/components.test.tsx
git commit -m "feat(webview): render persisted progress notes as muted progress lines"
```

---

### Task 7: Full-stack verification

Confirms all suites green together and the wiring is coherent end to end (no live model needed — that's the follow-up dogfood run).

**Files:** none (verification only).

- [ ] **Step 1: Backend full targeted regression**

From `services/agentd-py` (venv active):
Run: `pytest tests/ -k "controller or skills or chat or progress"`
Expected: 0 failures. Note any pre-existing flaky (per CLAUDE.md, one order-dependent failure is known) and reproduce it in isolation before attributing it here.

- [ ] **Step 2: Backend full suite (redirect + check exit)**

Run: `pytest tests/ --color=no > /tmp/progress-suite.txt 2>&1; echo exit=$?; tail -5 /tmp/progress-suite.txt`
Expected: `exit=0` (or only the one documented pre-existing flaky, confirmed unrelated).

- [ ] **Step 3: Rebuild editor-client, then TS suites**

```bash
npm run -w @crucible/editor-client build
npm run -w @crucible/editor-client test
npm run -w crucible-vscode-extension test
npm run typecheck
```
Expected: all green.

- [ ] **Step 4: ruff across touched backend files**

Run: `ruff check agentd/chat/controller_loop.py agentd/chat/controller.py agentd/chat/controller_prompts.py`
Expected: no errors.

- [ ] **Step 5: Final commit (if any verification-driven fixups were needed)**

```bash
git add -A
git commit -m "test(chat): full-stack verification pass for progress action"
```

Then proceed to Task 8.

---

### Task 8: Live smoke — a real model uses `progress` and distinguishes it from `answer`

The whole point of the feature is a behavioral change in a real model. Tasks 1-7 prove the mechanism is *available, guarded, persisted, and rendered*; this task is the only one that proves it **works**. It resolves spec Open Item A.

**Requires a live model.** This repo's default local path is TurboQuant (`CRUCIBLE_REASONING_BACKEND="turboquant"`, `CRUCIBLE_TURBOQUANT_MODEL="qwen3.6:35b-a3b-q4_K_M"`) — deliberately a *weak* model, which is the right test: the narrate-instead-of-act failure this feature fixes was observed on exactly this model.

**Files:** none (verification only). Any bug found becomes its own fix commit.

- [ ] **Step 1: Start the model server**

```bash
bash scripts/start-tqp.sh
# wait for the server, then confirm:
curl -s http://127.0.0.1:11435/health
```

- [ ] **Step 2: Start the backend against a scratch workspace**

Use a workspace OUTSIDE any ignored-named ancestor dir (never under `.tmp/` — `is_ignored_path` matches ignored dir names against the FULL absolute path, so an indexer under `.tmp/` silently indexes zero files).

```bash
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
bash scripts/start-backend.sh \
  --backend turboquant \
  --workspace "$PWD/workspaces/progress-smoke" \
  --validation-profile none
curl -s http://localhost:8000/health
```

`CRUCIBLE_CHAT_CONTROLLER=1` is required (`start-backend.sh` defaults it on). **Always quote `--workspace`** — an unquoted path with a space corrupts every derived var.

- [ ] **Step 3: Drive a multi-step turn that should produce narration**

Create a thread and send a prompt whose natural shape is "do several things in sequence" — the condition under which the model previously emitted a terminal `answer` mid-execution:

```bash
TID=$(curl -s -X POST http://localhost:8000/v1/chat/threads \
  -H 'Content-Type: application/json' \
  -d '{"workspace":"'"$PWD"'/workspaces/progress-smoke","title":"progress smoke"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -sN --no-buffer -X POST "http://localhost:8000/v1/chat/threads/$TID/message" \
  -H 'Content-Type: application/json' \
  -d '{"content":"Create a Python module calc.py with add and multiply functions, then a test file test_calc.py covering both, then run the tests."}' \
  | tee /tmp/progress-smoke-sse.txt
```

- [ ] **Step 4: Assert the four behaviors that matter**

From the SSE stream and the debug artifacts, confirm:

1. **`progress` actually fires** — at least one `chat_progress` event in the stream:
   ```bash
   grep -c "chat_progress" /tmp/progress-smoke-sse.txt
   ```
2. **The turn CONTINUES after it** — a `progress` event is followed by more `tool_call`/`patch_applied` activity, not `chat_done`. This is the non-terminal property.
3. **`answer` is still used correctly and distinctly** — the turn ends with exactly one terminal `answer` (or `submit_changes`) delivering finished content, NOT a status update. Compare the `answer` text against the `progress` notes: the notes should be status ("now creating the test file"), the answer should be a result ("Added add/multiply to calc.py; 2 tests pass").
4. **No narrate-and-stop regression** — the turn must not end with an `answer` that describes work it never did. Inspect the per-iteration artifacts for the exact model bytes:
   ```bash
   ls workspaces/progress-smoke/.crucible/state/artifacts/chat/$TID/*/controller-turn-*.json
   ```
   Grep each `raw_result` for `"type"` values in order — that sequence IS the behavioral record.

- [ ] **Step 5: Verify durable persistence**

```bash
sqlite3 workspaces/progress-smoke/.crucible/state/chat.sqlite3 \
  "SELECT messages_json FROM chat_threads WHERE id='$TID';" \
  | python3 -m json.tool | grep -A2 '"progress"'
```
Expected: one persisted `agent`/`text` message per note, each with `metadata.progress = true`, content equal to the note (capped at 500 chars).

- [ ] **Step 6: Verify the guardrails fire on a real model (not just in tests)**

Watch the log for the cap/dedup corrections. They should fire rarely or never on a well-behaved turn — but if the model does spam notes, confirm the correction lands rather than the loop spinning:
```bash
grep -E "already posted|progress" .tmp/stress-*/logs/agentd.log | tail -20
```

- [ ] **Step 7: Verify UI rendering in the dev host**

```bash
npm run build
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/progress-smoke"
```
Send the same prompt through the chat panel. Confirm: notes appear as muted progress lines *during* the turn (not only at the end), the composer stays in "working" state across them, and a reload mid-turn still shows the notes (durable path).

- [ ] **Step 8: Record the outcome**

Write findings to `docs/superpowers/2026-07-25-progress-action-live-smoke.md`: the observed action-type sequence, verbatim examples of a `progress` note and the final `answer` side by side (the differentiation evidence), whether any guardrail fired, and any bug found.

**If the model does NOT use `progress`** — it keeps emitting terminal `answer` mid-turn — that is a real finding, not a failed task: the mechanism works but the steering doesn't. Report it with the artifact evidence; the fix is prompt-side (per project memory, few-shot worked examples beat abstract rules for this model), and it becomes its own follow-up spec rather than being patched blindly here.

---

## Notes for the executor

- **Tasks 1-7 do not prove the feature works.** They make `progress` *available, guarded, persisted, and rendered*. Whether a real model *uses it well* — and clearly distinguishes it from `answer` — is Task 8, and Task 8 is the acceptance gate. Do not report the feature complete after Task 7.
- **The two `controller.ts` handler sites are not identical** — copy each one's surrounding `appendChatMessage` field shape exactly (the breadcrumb branches at `:752` and `:1018` differ in whether they pass `taskId`). Don't unify them.
- **If `build_loop` / stub helpers don't exist** in the referenced test files, write minimal local ones — do not refactor the existing tests to extract shared helpers (out of scope; risks touching the fix-branch's own new tests).
```
