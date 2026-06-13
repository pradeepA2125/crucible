# Task Narrative — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every finished task an LLM-authored narrative (headline + points) of what it did, rendered on the Review/Error cards and fed into the next chat turn's context.

**Architecture:** Accumulate an append-only `run_events` log during execution (per-step prose captured for free via a new `step_summary` field on the `verify_done` action; deterministic events for failed/replan paths). At each run outcome (READY_FOR_REVIEW success + FAILED/ABORTED) synthesize the log into a `TaskNarrative` with one `summarize_run` LLM call, reusing the Tier B terminal chokepoints. Persist on `TaskRecord`, expose via `/live` + `TaskResult`/`TaskView`, render on the cards, and surface to the next turn via the existing transcript-history + `_find_recent_task` plumbing.

**Tech Stack:** Python 3.11 (FastAPI, Pydantic, pytest-asyncio), TypeScript (editor-client Zod, vscode-extension controller, React webview-ui + vitest).

**Spec:** `docs/superpowers/specs/2026-06-13-task-narrative-design.md`

**Conventions verified against source (re-verify if the tree moved):** `StepRunResult` `domain/models.py:684`; `VerifyResult` `tools/loop.py:134`; verify_done branch `tools/loop.py:482` (returns `VerifyResult` ~514); `AGENT_STEP_RESPONSE_SCHEMA` `reasoning/tool_prompts.py` (action enum ~13, verify_done fields ~60); `_execute_plan` step-complete `engine.py:~1605` (`_mark_step_completed`), step-exhausted FAILED `~1545`, revision/PlanHandoff `~1500`, finally `~1770`, tool-loop `StepRunResult(step_completed)` `~2504`; `ReasoningEngine` Protocol `reasoning/contracts.py:12`; `ReasoningEngineImpl` `reasoning/engine.py` (uses `self._transport.generate_json`); `ScriptedReasoningEngine` `orchestrator/scripted_engine.py:6`; `_find_recent_task` `chat/agent.py:479` (passed to classifier `:296`, history built `:177`); `resolve_live_state` `chat/live_state.py:68`; `ThreadLiveState` `chat/models.py:62`.

**Deferred (from spec — do BEFORE Task 3's replan event):** re-trace the delta-replan path end-to-end (`_apply_revision` `engine.py:2171` + the PlanHandoff site) to confirm `reverted_step_ids` vs `revised_steps` semantics and the exact append ordering. Carry this note into the chat-UI handoff at its next update.

**Slice order:** 1 (model + accumulation) → 2 (synthesis) → 3 (exposure + chat consumption) → 4 (contracts + frontend). Each slice is independently testable and committable.

---

## Slice 1 — Event log model + accumulation

### Task 1: `RunEvent` / `TaskNarrative` models + state/record fields

**Files:**
- Modify: `services/agentd-py/agentd/domain/models.py` (new models; `TaskExecutionState`; `TaskRecord`)
- Test: `services/agentd-py/tests/test_narrative_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_narrative_models.py
from agentd.domain.models import (
    RunEvent, TaskBudget, TaskNarrative, TaskRecord,
)


def test_run_event_and_narrative_defaults():
    t = TaskRecord(task_id="t", goal="g", workspace_path="/w", budget=TaskBudget())
    assert t.execution_state.run_events == []
    assert t.task_narrative is None
    t.execution_state.run_events.append(
        RunEvent(kind="step_done", step_id="s1", goal="add foo", note="added foo()")
    )
    t.execution_state.run_events.append(
        RunEvent(kind="replan", reason="api changed", reverted_step_ids=["s2"], revised_step_ids=["s2"])
    )
    t.task_narrative = TaskNarrative(outcome="succeeded", headline="Added foo", points=["added foo()"])
    assert t.execution_state.run_events[0].kind == "step_done"
    assert t.execution_state.run_events[1].reverted_step_ids == ["s2"]
    assert t.task_narrative.headline == "Added foo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_narrative_models.py -q`
Expected: FAIL — `RunEvent` import error.

- [ ] **Step 3: Add the models + fields**

In `domain/models.py`, before `class TaskRecord` (next to `FailureSummary`/`RunSummary`):

```python
class RunEvent(BaseModel):
    """One append-only entry in the run's event log (Task Narrative). step_done/step_failed
    carry a per-step note; replan records a course-correction. Never pruned — the log keeps
    the dead-ends so the synthesized narrative can tell the whole story."""
    kind: Literal["step_done", "step_failed", "replan"]
    step_id: str | None = None
    goal: str | None = None
    note: str | None = None
    reason: str | None = None
    reverted_step_ids: list[str] = Field(default_factory=list)
    revised_step_ids: list[str] = Field(default_factory=list)


class TaskNarrative(BaseModel):
    """LLM-authored story of the run (distinct from the deterministic RunSummary counts)."""
    outcome: Literal["succeeded", "failed", "aborted"]
    headline: str
    points: list[str] = Field(default_factory=list)
```

In `class TaskExecutionState`, after `pre_execution_checkpoint`:

```python
    # Append-only event log backing the Task Narrative. Never pruned (a delta replan that
    # reverts steps leaves their step_done events in place — the journey is the story).
    run_events: list[RunEvent] = Field(default_factory=list)
```

In `class TaskRecord`, next to `failure_summary` / `run_summary`:

```python
    task_narrative: TaskNarrative | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && python -m pytest tests/test_narrative_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/domain/models.py services/agentd-py/tests/test_narrative_models.py
git commit -m "feat(models): RunEvent + TaskNarrative + run_events log on TaskExecutionState"
```

### Task 2: `step_summary` on `verify_done` → `VerifyResult` → `StepRunResult`

**Files:**
- Modify: `services/agentd-py/agentd/reasoning/tool_prompts.py` (`AGENT_STEP_RESPONSE_SCHEMA`)
- Modify: `services/agentd-py/agentd/tools/loop.py` (`VerifyResult` dataclass `:134`; verify_done return `~514`)
- Modify: `services/agentd-py/agentd/domain/models.py` (`StepRunResult` `:684`)
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (tool-loop `StepRunResult(...)` at `~2504`)
- Test: `services/agentd-py/tests/test_step_summary_capture.py`

- [ ] **Step 1: Write the failing test** (asserts the schema carries the field and VerifyResult exposes it)

```python
# tests/test_step_summary_capture.py
from agentd.reasoning.tool_prompts import AGENT_STEP_RESPONSE_SCHEMA
from agentd.tools.loop import VerifyResult


def test_schema_has_step_summary_field():
    props = AGENT_STEP_RESPONSE_SCHEMA["properties"]
    assert "step_summary" in props


def test_verify_result_carries_step_summary():
    vr = VerifyResult(patch_document={}, touched_files=[], verified=True,
                      test_output="", tool_trace=None, step_summary="did the thing")
    assert vr.step_summary == "did the thing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_step_summary_capture.py -q`
Expected: FAIL — no `step_summary` in schema / `VerifyResult` has no `step_summary`.

- [ ] **Step 3: Add the field in all three places**

In `reasoning/tool_prompts.py`, inside `AGENT_STEP_RESPONSE_SCHEMA["properties"]` next to the verify_done fields (`verified`/`test_output`):

```python
        "step_summary": {
            "type": "string",
            "description": (
                "One concise sentence summarizing what THIS step changed, for the task "
                "narrative (optional, set it on verify_done)."
            ),
        },
```

In `tools/loop.py`, add to the `VerifyResult` dataclass (`:134`) after `test_output`:

```python
    step_summary: str = ""              # model-authored one-liner from verify_done (optional)
```

…and in the verify_done `return VerifyResult(...)` (`~514`) add:

```python
                    step_summary=str(response.get("step_summary", "")),
```

In `domain/models.py` `class StepRunResult` (`:684`), add after `last_failure`:

```python
    step_summary: str | None = None     # carried from VerifyResult for the narrative event log
```

In `orchestrator/engine.py` the tool-loop `StepRunResult(...)` at `~2504` (where `step_outcome` is the `VerifyResult`), add:

```python
                        step_summary=step_outcome.step_summary or None,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && python -m pytest tests/test_step_summary_capture.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/reasoning/tool_prompts.py services/agentd-py/agentd/tools/loop.py services/agentd-py/agentd/domain/models.py services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/test_step_summary_capture.py
git commit -m "feat: step_summary on verify_done flows through VerifyResult to StepRunResult"
```

### Task 3: Append `run_events` in `_execute_plan` (step_done / step_failed / replan)

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (`_execute_plan` — step-complete `~1605`, step-exhausted FAILED `~1545`, PlanHandoff/replan site `~1500`; add a `_append_run_event` helper)
- Test: `services/agentd-py/tests/test_run_events_accumulate.py`

> Implementer note: do the deferred delta-replan re-trace first. The `replan` event must be appended at the PlanHandoff handling site **before** `self._apply_revision(...)` runs, using `step_result.reason` and `revision.reverted_step_ids` / `[r.step_id for r in revision.revised_steps]`.

- [ ] **Step 1: Write the failing test** (uses the scripted engine + a step that completes, asserts a `step_done` event with the model's note lands)

```python
# tests/test_run_events_accumulate.py
from pathlib import Path

import pytest

from agentd.domain.models import TaskRecord, TaskStatus
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _OkValidator:
    async def run(self, _p):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _make_plan_raw():
    return {
        "analysis": "a",
        "steps": [{"id": "s1", "goal": "create hello", "targets": [{"path": "hello.py", "intent": "new"}], "risk": "low"}],
        "expected_files": ["hello.py"],
        "stop_conditions": ["done"],
    }


def _make_patch_ops():
    return [{"op": "create_file", "file": "hello.py", "content": "x = 1\n", "reason": "seed"}]


@pytest.mark.asyncio
async def test_step_done_event_records_model_step_summary(tmp_path: Path):
    ws = tmp_path / "ws"; ws.mkdir()
    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(),
        patches=[{"candidates": [{"candidate_id": "c1", "patch_ops": _make_patch_ops()}]}],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "create", "patch_ops": _make_patch_ops()},
            {"type": "verify_done", "thought": "ok", "verified": True, "test_output": "1 passed",
             "step_summary": "created hello.py with x=1"},
        ],
    )
    orch = AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"), reasoning_engine=reasoning,
        validator=_OkValidator(), patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )
    task = TaskRecord(task_id="t1", goal="create", workspace_path=str(ws))
    await orch._store.create(task)
    await orch.run_task("t1")
    result = await orch.continue_task("t1", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
    events = result.execution_state.run_events
    done = [e for e in events if e.kind == "step_done"]
    assert any(e.step_id == "s1" and e.note == "created hello.py with x=1" for e in done)
```

> If `ScriptedReasoningEngine.create_tool_step` does not yet pass `step_summary` through, mirror its existing `verified`/`test_output` handling so the scripted response's `step_summary` reaches the loop (grep `tool_step_responses` in `orchestrator/scripted_engine.py`). It typically returns the dict verbatim, so no change is needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_run_events_accumulate.py -q`
Expected: FAIL — `run_events` is empty (no append wired yet).

- [ ] **Step 3: Add the helper + the three append sites**

In `orchestrator/engine.py`, add a helper near `_finalize_run_summary`:

```python
    def _append_run_event(self, task: TaskRecord, event: "RunEvent") -> None:
        task.execution_state.run_events.append(event)
```

Import `RunEvent` at the top of `engine.py` (add to the `from agentd.domain.models import (...)` block).

At the step-complete site in `_execute_plan` (right after `self._mark_step_completed(task, step.id)`, `~1605`):

```python
                note = (step_result.step_summary
                        or f"edited {', '.join(step_result.touched_files) or step.goal[:80]}")
                self._append_run_event(task, RunEvent(
                    kind="step_done", step_id=step.id, goal=step.goal, note=note,
                ))
```

At the step-exhausted FAILED site (`~1545`, where it `transition(task, FAILED, "step execution exhausted")`), BEFORE the transition:

```python
                self._append_run_event(task, RunEvent(
                    kind="step_failed", step_id=step.id, goal=step.goal,
                    note=f"step did not complete after retries",
                ))
```

At the PlanHandoff/replan site (`~1500`, right before `self._apply_revision(task, shadow_path, revision)`):

```python
                    self._append_run_event(task, RunEvent(
                        kind="replan", reason=step_result.reason,
                        reverted_step_ids=list(revision.reverted_step_ids),
                        revised_step_ids=[r.step_id for r in revision.revised_steps],
                    ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && python -m pytest tests/test_run_events_accumulate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/test_run_events_accumulate.py
git commit -m "feat(engine): append step_done/step_failed/replan run_events (append-only)"
```

---

## Slice 2 — Synthesis

### Task 4: `summarize_run` reasoning method (Protocol + impl + scripted)

**Files:**
- Modify: `services/agentd-py/agentd/reasoning/contracts.py` (`ReasoningEngine` Protocol `:12`)
- Create: `services/agentd-py/agentd/reasoning/narrative_prompts.py` (prompt builder + `TASK_NARRATIVE_RESPONSE_SCHEMA`)
- Modify: `services/agentd-py/agentd/reasoning/engine.py` (`ReasoningEngineImpl.summarize_run`)
- Modify: `services/agentd-py/agentd/orchestrator/scripted_engine.py` (`ScriptedReasoningEngine.summarize_run`)
- Test: `services/agentd-py/tests/test_summarize_run.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_summarize_run.py
import pytest

from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_scripted_summarize_run_returns_headline_and_points():
    eng = ScriptedReasoningEngine(
        plan={"analysis": "a", "steps": [], "expected_files": [], "stop_conditions": []},
        patches=[],
        tool_step_responses=[],
        run_narrative={"headline": "Did the thing", "points": ["added foo", "ran tests"]},
    )
    out = await eng.summarize_run(
        goal="g", outcome="succeeded", run_events=[], deviations=[], modified_files=["a.py"],
    )
    assert out["headline"] == "Did the thing"
    assert out["points"] == ["added foo", "ran tests"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_summarize_run.py -q`
Expected: FAIL — `ScriptedReasoningEngine.__init__` has no `run_narrative` / no `summarize_run`.

- [ ] **Step 3: Implement Protocol + scripted + real impl**

In `reasoning/contracts.py`, add to the `ReasoningEngine` Protocol:

```python
    async def summarize_run(
        self,
        *,
        goal: str,
        outcome: str,
        run_events: list[dict[str, object]],
        deviations: list[str],
        modified_files: list[str],
    ) -> dict[str, object]:
        ...
```

Create `reasoning/narrative_prompts.py`:

```python
"""Prompt + schema for the task-narrative synthesis (summarize_run)."""
from __future__ import annotations

TASK_NARRATIVE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One line: what the task did."},
        "points": {
            "type": "array", "items": {"type": "string"},
            "description": "3-6 short bullet points: what changed / what happened, in order.",
        },
    },
    "required": ["headline", "points"],
}

_SYSTEM = (
    "You write a short, factual narrative of an automated coding task's run for a human "
    "reviewer and for re-use as context in the next chat turn. Be concrete and specific; "
    "name files and what changed. If the run failed or was aborted, say what was attempted "
    "and where it stopped. If there were replans, mention the course-correction briefly."
)


def build_narrative_payload(
    *, goal: str, outcome: str, run_events: list[dict[str, object]],
    deviations: list[str], modified_files: list[str],
) -> dict[str, object]:
    return {
        "goal": goal,
        "outcome": outcome,
        "run_events": run_events,
        "deviations": deviations,
        "modified_files": modified_files,
    }


def format_narrative_system_prompt() -> str:
    return _SYSTEM
```

In `reasoning/engine.py`, add to `ReasoningEngineImpl` (mirror `create_tool_step`'s `generate_json` call):

```python
    async def summarize_run(
        self, *, goal, outcome, run_events, deviations, modified_files,
    ) -> dict[str, object]:
        from agentd.reasoning.narrative_prompts import (
            TASK_NARRATIVE_RESPONSE_SCHEMA, build_narrative_payload, format_narrative_system_prompt,
        )
        payload = build_narrative_payload(
            goal=goal, outcome=outcome, run_events=run_events,
            deviations=deviations, modified_files=modified_files,
        )
        result = await self._transport.generate_json(
            system_instructions=format_narrative_system_prompt(),
            user_payload=payload,
            schema=TASK_NARRATIVE_RESPONSE_SCHEMA,
            schema_name="task_narrative",
        )
        return result
```

> Match `generate_json`'s actual parameter names by copying an existing call in this file (e.g. `create_tool_step`'s at `~163`) — the kwargs (`system_instructions`/`user_payload`/`schema`/`schema_name`) may differ; use whatever that call uses.

In `orchestrator/scripted_engine.py`, add a `run_narrative` constructor kwarg (default `None`) and:

```python
    async def summarize_run(self, *, goal, outcome, run_events, deviations, modified_files):
        return self._run_narrative or {"headline": f"{outcome}: {goal[:60]}", "points": []}
```

- [ ] **Step 4: Run + commit**

Run: `cd services/agentd-py && python -m pytest tests/test_summarize_run.py -q` → PASS.

```bash
git add services/agentd-py/agentd/reasoning/contracts.py services/agentd-py/agentd/reasoning/narrative_prompts.py services/agentd-py/agentd/reasoning/engine.py services/agentd-py/agentd/orchestrator/scripted_engine.py services/agentd-py/tests/test_summarize_run.py
git commit -m "feat(reasoning): summarize_run synthesis method (Protocol + impl + scripted)"
```

### Task 5: Generate + persist `task_narrative` at terminal/READY_FOR_REVIEW

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (a `_finalize_task_narrative` helper called in the `_execute_plan` finally, alongside `_finalize_run_summary`)
- Test: `services/agentd-py/tests/test_run_events_accumulate.py` (add a case)

- [ ] **Step 1: Write the failing test** (append)

```python
@pytest.mark.asyncio
async def test_task_narrative_synthesized_at_ready_for_review(tmp_path: Path):
    ws = tmp_path / "ws"; ws.mkdir()
    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(),
        patches=[{"candidates": [{"candidate_id": "c1", "patch_ops": _make_patch_ops()}]}],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "create", "patch_ops": _make_patch_ops()},
            {"type": "verify_done", "thought": "ok", "verified": True, "test_output": "", "step_summary": "created hello.py"},
        ],
        run_narrative={"headline": "Created hello.py", "points": ["added x=1"]},
    )
    orch = AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"), reasoning_engine=reasoning,
        validator=_OkValidator(), patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )
    task = TaskRecord(task_id="t2", goal="create", workspace_path=str(ws))
    await orch._store.create(task)
    await orch.run_task("t2")
    result = await orch.continue_task("t2", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert result.task_narrative is not None
    assert result.task_narrative.outcome == "succeeded"
    assert result.task_narrative.headline == "Created hello.py"
    # persisted
    stored = await orch._store.get("t2")
    assert stored.task_narrative is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_run_events_accumulate.py::test_task_narrative_synthesized_at_ready_for_review -q`
Expected: FAIL — `task_narrative` is None.

- [ ] **Step 3: Implement the helper + call it in the finally**

In `engine.py`, add (import `TaskNarrative` at the top):

```python
    async def _finalize_task_narrative(self, task: TaskRecord, outcome: str) -> None:
        """Synthesize the run narrative from the event log (one LLM call). Best-effort:
        a synthesis failure must never fail the task — log and skip."""
        try:
            es = task.execution_state
            dev = task.run_summary.deviations if task.run_summary else []
            raw = await self._reasoning_engine.summarize_run(
                goal=task.goal, outcome=outcome,
                run_events=[e.model_dump(mode="json") for e in es.run_events],
                deviations=dev, modified_files=list(task.modified_files),
            )
            task.task_narrative = TaskNarrative(
                outcome=outcome,
                headline=str(raw.get("headline", ""))[:300],
                points=[str(p) for p in (raw.get("points") or [])][:10],
            )
        except Exception:
            logger.exception("Task narrative synthesis failed", extra={"task_id": task.task_id})
```

In the `_execute_plan` `finally` (`~1770`), inside the existing `READY_FOR_REVIEW` branch (after `_finalize_run_summary`) and the terminal branch:

```python
            if task.status == TaskStatus.READY_FOR_REVIEW:
                self._finalize_run_summary(task)
                await self._finalize_task_narrative(task, "succeeded")
                await self._store.save(task)
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}:
                self._finalize_run_summary(task)
                if task.status == TaskStatus.FAILED and task.failure_summary is None:
                    ...  # existing fallback
                outcome = "aborted" if task.status == TaskStatus.ABORTED else (
                    "succeeded" if task.status == TaskStatus.SUCCEEDED else "failed")
                await self._finalize_task_narrative(task, outcome)
                self._clear_pre_execution_checkpoint(task)
                await self._store.save(task)
```

(Keep the existing `_finalize_run_summary`/`_clear`/`save` lines; only add the two `_finalize_task_narrative` calls + the `outcome` computation.)

- [ ] **Step 4: Run + commit**

Run: `cd services/agentd-py && python -m pytest tests/test_run_events_accumulate.py -q` → PASS.

```bash
git add services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/test_run_events_accumulate.py
git commit -m "feat(engine): synthesize + persist task_narrative at READY_FOR_REVIEW and terminals"
```

---

## Slice 3 — Exposure + chat-turn consumption

### Task 6: Expose `task_narrative` via `/live` + `TaskResult`/`TaskView`

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py` (`ThreadLiveState`), `services/agentd-py/agentd/chat/live_state.py` (`resolve_live_state`)
- Modify: `services/agentd-py/agentd/domain/models.py` (`TaskResult`, `TaskView`)
- Modify: `services/agentd-py/agentd/api/routes.py` (`_to_task_result`, `_to_task_view`)
- Test: `services/agentd-py/tests/test_narrative_exposed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_narrative_exposed.py
from agentd.chat.live_state import resolve_live_state
from agentd.domain.models import TaskBudget, TaskNarrative, TaskRecord, TaskStatus


def test_live_state_surfaces_task_narrative():
    task = TaskRecord(task_id="t", goal="g", workspace_path="/w", budget=TaskBudget(),
                      status=TaskStatus.READY_FOR_REVIEW,
                      task_narrative=TaskNarrative(outcome="succeeded", headline="Did X", points=["a"]))
    live = resolve_live_state(task.task_id, lambda _id: task)
    assert live.task_narrative is not None
    assert live.task_narrative.headline == "Did X"
```

- [ ] **Step 2: Run to verify it fails** → FAIL (`ThreadLiveState` has no `task_narrative`).

- [ ] **Step 3: Add the field + passthrough**

In `chat/models.py` import `TaskNarrative` (already imports `FailureSummary, RunSummary` from `agentd.domain.models` — add `TaskNarrative`) and add to `ThreadLiveState`:

```python
    task_narrative: TaskNarrative | None = None
```

In `chat/live_state.py` `resolve_live_state` return, add:

```python
        task_narrative=task.task_narrative,
```

In `domain/models.py`, add `task_narrative: TaskNarrative | None = None` to both `TaskResult` and `TaskView`. In `api/routes.py`, add `task_narrative=task.task_narrative,` to both `_to_task_result` and `_to_task_view`.

- [ ] **Step 4: Run + commit**

Run: `cd services/agentd-py && python -m pytest tests/test_narrative_exposed.py -q` → PASS.

```bash
git add services/agentd-py/agentd/chat/models.py services/agentd-py/agentd/chat/live_state.py services/agentd-py/agentd/domain/models.py services/agentd-py/agentd/api/routes.py services/agentd-py/tests/test_narrative_exposed.py
git commit -m "feat(api): expose task_narrative via /live and TaskResult/TaskView"
```

### Task 7: Persist narrative as a transcript message + surface via `_find_recent_task`

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py` (write a durable chat message in `_finalize_task_narrative` when `chat_channel_id` is set)
- Modify: `services/agentd-py/agentd/chat/agent.py` (`_find_recent_task` `:479` — include the task's `task_narrative` in the returned dict)
- Test: `services/agentd-py/tests/test_narrative_chat_context.py`

- [ ] **Step 1: Write the failing test** (a chat-linked task persists a narrative message)

```python
# tests/test_narrative_chat_context.py
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import TaskNarrative, TaskRecord
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoReason:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError
    async def summarize_run(self, **k): return {"headline": "Did X", "points": ["a"]}


class _OkValidator:
    async def run(self, _p):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


@pytest.mark.asyncio
async def test_narrative_persisted_as_transcript_message(tmp_path: Path):
    chat = ChatThreadStore(tmp_path / "chat.db")
    thread = chat.create_thread(str(tmp_path))
    orch = AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"), reasoning_engine=_NoReason(),
        validator=_OkValidator(), patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        chat_store=chat,
    )
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path),
                      chat_channel_id=f"chat:{thread.thread_id}")
    await orch._finalize_task_narrative(task, "succeeded")
    orch._write_chat_narrative(task)   # the new writer (see Step 3)
    msgs = chat.get_thread(thread.thread_id).messages
    assert any("Did X" in (m.content or "") for m in msgs)
```

- [ ] **Step 2: Run to verify it fails** → FAIL (`_write_chat_narrative` missing).

- [ ] **Step 3: Implement the writer + recent-task surfacing**

In `engine.py`, add a writer mirroring `_write_step_completed_breadcrumb` / `write_chat_breadcrumb` (grep those in `engine.py`):

```python
    def _write_chat_narrative(self, task: TaskRecord) -> None:
        """Persist the task narrative as a durable agent/text transcript message so the next
        chat turn inherits it via history. No-op if not chat-linked or no narrative."""
        if not task.chat_channel_id or self._chat_store is None or task.task_narrative is None:
            return
        from agentd.chat.models import ChatMessage
        thread_id = task.chat_channel_id[len("chat:"):]
        n = task.task_narrative
        body = n.headline + ("\n" + "\n".join(f"- {p}" for p in n.points) if n.points else "")
        msg = ChatMessage(role="agent", content=body, type="text", task_id=task.task_id,
                          metadata={"task_id": task.task_id, "task_narrative": True})
        self._chat_store.append_message(thread_id, msg)  # type: ignore[union-attr]
```

Call `self._write_chat_narrative(task)` immediately after `_finalize_task_narrative` in BOTH finally branches (Task 5's call sites).

In `chat/agent.py`, in `_find_recent_task` (`:479`) where it builds the returned dict for the recent task, add the narrative (fetch the task record it already resolves and include `task_narrative` model-dumped):

```python
        # inside _find_recent_task, when a recent task record `rec` is resolved:
        if rec.task_narrative is not None:
            result["task_narrative"] = rec.task_narrative.model_dump(mode="json")
```

> Match `_find_recent_task`'s actual local variable names and return shape — read the method first; it returns `dict[str, object] | None` with `task_id`.

- [ ] **Step 4: Run + commit**

Run: `cd services/agentd-py && python -m pytest tests/test_narrative_chat_context.py -q` → PASS.

```bash
git add services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/agentd/chat/agent.py services/agentd-py/tests/test_narrative_chat_context.py
git commit -m "feat(chat): persist task_narrative as transcript message + surface via recent_task"
```

---

## Slice 4 — Contracts + frontend

### Task 8: editor-client `TaskNarrative` contract

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/http-backend-client.test.ts`

- [ ] **Step 1: Write the failing test** — mirror the Tier B `getThreadLiveState maps failure_summary` test: a `/live` response with `task_narrative: {outcome, headline, points}` maps to `live.taskNarrative.headline`. Add to `apps/editor-client/test/http-backend-client.test.ts`.
- [ ] **Step 2: Run** `npm run -w @ai-editor/editor-client test` → FAIL.
- [ ] **Step 3: Implement.** Add `TaskNarrativeSchema` (`outcome`, `headline`, `points: z.array(z.string()).default([])`) + `taskNarrative: TaskNarrativeSchema.nullable().optional()` on `TaskViewSchema`/`TaskResultSchema`/`ThreadLiveStateSchema`. Add a `private toTaskNarrative(raw)` mapper (mirror `toRunSummary` — snake→camel, `outcome`/`headline`/`points`) and wire it into `toTaskView`/`toTaskResult`/`getThreadLiveState`.
- [ ] **Step 4: Build + test:** `npm run -w @ai-editor/editor-client build && npm run -w @ai-editor/editor-client test` → PASS. (Build BEFORE extension typecheck.)
- [ ] **Step 5: Commit** `feat(contracts): TaskNarrative on TaskView/TaskResult/ThreadLiveState`.

### Task 9: Extension controller forwards the narrative to the cards

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (`LiveReviewView` + `LiveErrorView` gain optional `narrative`), `apps/vscode-extension/src/controller.ts` (interface mirror + `pollThreadLiveState`)
- Modify: `apps/vscode-extension/test/controller.test.ts`

- [ ] **Step 1: Write failing controller tests** (stub client): a `READY_FOR_REVIEW` live state with `taskNarrative` → `renderLiveReview` receives `narrative.headline`/`points`; a `FAILED` live state with `taskNarrative` → `renderLiveError` receives it. Add `taskNarrative` to the stub `getThreadLiveState` responses.
- [ ] **Step 2: Run** `npm run -w @ai-editor/vscode-extension test` → FAIL.
- [ ] **Step 3: Implement.** Add `narrative?: { headline: string; points: string[] }` to `LiveReviewView` and `LiveErrorView` (extension `controller.ts` `ControllerUI` types AND the webview `types.ts` copies — keep them in sync). In `pollThreadLiveState`, pass `narrative: live.taskNarrative ? { headline, points } : undefined` into the `renderLiveReview` and `renderLiveError` payloads.
- [ ] **Step 4: typecheck + test** `npm run -w @ai-editor/vscode-extension typecheck && npm run -w @ai-editor/vscode-extension test` → PASS.
- [ ] **Step 5: Commit** `feat(extension): forward task_narrative into Review/Error live cards`.

### Task 10: webview ReviewCard + ErrorCard render the narrative

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/ReviewCard.tsx`, `ErrorCard.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/ReviewError.test.tsx`

- [ ] **Step 1: Write failing vitest cases:** ReviewCard given `narrative={headline, points}` renders the headline + each point; ErrorCard likewise. (Pass `narrative` through the existing `LiveReviewView`/`LiveErrorView` props.)
- [ ] **Step 2: Run** `npm --prefix apps/vscode-extension/webview-ui test` → FAIL.
- [ ] **Step 3: Implement.** In `ReviewCard.tsx`, when `narrative` is present render a block above the file rows: `headline` (emphasized) + a `<ul>` of `points`. Same in `ErrorCard.tsx` (below the status/detail). Keep the existing file/step/deviation rows.
- [ ] **Step 4: Run** `npm --prefix apps/vscode-extension/webview-ui test` → PASS.
- [ ] **Step 5: Commit** `feat(webview): ReviewCard + ErrorCard render the task narrative`.

---

## Final task: full suites + smoke

- [ ] **Step 1:** `npm run build && npm run test && npm run typecheck` (build editor-client before extension) — all TS green.
- [ ] **Step 2:** `cd services/agentd-py && python -m pytest -q` — only the documented pre-existing failures remain (gemini/groq transports + `@requires_live_snapshot` graph-walker). Read the FAILED lines; never trust a piped exit code.
- [ ] **Step 3: Dev-host smoke** (backend via `start-backend.sh`, workspace OUTSIDE `.tmp`): run a multi-step task → at READY_FOR_REVIEW the ReviewCard shows a headline + points describing the change; force a failure → ErrorCard shows a "attempted … stopped at …" narrative; trigger a delta replan (a goal that makes a step revise) → the narrative mentions the course-correction; send a follow-up chat message → confirm the agent's next turn references the prior task (narrative in history/recent_task).
- [ ] **Step 4: Commit** any smoke fixes; update CLAUDE.md "Task Lifecycle" (run_events log + task_narrative + summarize_run) and the chat section (narrative transcript message + recent_task surfacing). Add the deferred delta-replan re-trace note to the chat-UI handoff.
