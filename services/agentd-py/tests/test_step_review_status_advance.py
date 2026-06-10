"""Accepting a step review must advance the orchestrator's OWN task object.

Field crash: after the final step's review was accepted, ``_execute_plan`` tried
``transition(task, VALIDATING)`` and hit
``ValueError: Invalid transition: AWAITING_STEP_REVIEW -> VALIDATING``.

Root cause: ``_pause_for_step_review`` re-fetched a *fresh* task record and reset
THAT to EXECUTING, while the caller kept a stale reference that the gate-raise had
mutated in place to AWAITING_STEP_REVIEW. The caller then both (a) crashed on the
validation transition and (b) re-saved the stale AWAITING_STEP_REVIEW gate, so the
card reappeared on reload and the resolved decision future answered 409.

This test pins the contract: after the gate resolves, the caller's object — and the
persisted record — must be back in EXECUTING with the pending review cleared.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.domain.models import (
    PlanStep,
    StepRunResult,
    TaskRecord,
    TaskStatus,
    ValidationResult,
)
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


def _make(tmp_path: Path) -> AgentOrchestrator:
    # SQLite store (not InMemory): get() must return a FRESH copy, like production.
    # InMemoryTaskStore aliases the stored object, which masks the stale-reference bug.
    return AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "tasks.sqlite3"),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


async def _seed_executing(orch: AgentOrchestrator, ws: Path) -> TaskRecord:
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(ws))
    for status, reason in [
        (TaskStatus.CONTEXT_READY, "ctx"),
        (TaskStatus.AWAITING_PLAN_APPROVAL, "approval"),
        (TaskStatus.PLANNED, "planned"),
        (TaskStatus.EXECUTING, "executing"),
    ]:
        task = transition(task, status, reason)
    await orch._store.create(task)
    return task


async def _wait_pending(d: dict, key: str) -> None:
    for _ in range(200):
        await asyncio.sleep(0)
        if key in d:
            return
    raise AssertionError(f"gate future never registered for {key}")


@pytest.mark.asyncio
async def test_step_review_accept_advances_caller_task_to_executing(tmp_path: Path) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    orch = _make(tmp_path)
    task = await _seed_executing(orch, ws)
    step = PlanStep(id="s1", goal="g", targets=[], risk="low")
    step_result = StepRunResult(
        step_id="s1", outcome="step_completed",
        validation_result="validation_passed", attempts_used=1, touched_files=[],
    )

    gate = asyncio.create_task(
        orch._pause_for_step_review(task, step, step_result, shadow, ws)
    )
    await _wait_pending(orch._pending_step_decisions, task.task_id)
    orch._pending_step_decisions[task.task_id].set_result("accept")
    decision = await gate

    assert decision == "accept"
    # The SAME object the caller (_execute_plan) holds must be back in EXECUTING.
    assert task.status == TaskStatus.EXECUTING
    # The persisted record must agree — no stale gate left behind for /live to render.
    stored = await orch._store.get(task.task_id)
    assert stored.status == TaskStatus.EXECUTING
    assert stored.execution_state.pending_step_review is None
    # The exact field crash: the caller proceeds to full validation. Must not raise.
    transition(task, TaskStatus.VALIDATING, "full validation started")
