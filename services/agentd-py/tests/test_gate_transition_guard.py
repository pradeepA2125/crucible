"""Gate-raise transitions must only swallow TRUE re-entrancy, never invalid sources.

The gate callbacks wrap their status transition in ``except ValueError`` to tolerate
a benign re-entrant edge (status already == the target gate). But a blanket
``pass`` also swallows an INVALID-source transition (e.g. raising a scope gate while
the task is parked in AWAITING_STEP_REVIEW). When that happens the pending_* payload
is set but the status never moves, so ``/live`` (which keys the card off status)
renders the wrong gate and the task blocks on its decision future until timeout.

The guard must re-raise unless the task is genuinely already in the target state.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.domain.models import (
    PlanStep,
    ScopePolicy,
    ScopeTrigger,
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


def _make(tmp_path: Path, **kw) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "tasks.sqlite3"),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        **kw,
    )


async def _seed(orch: AgentOrchestrator, ws: Path, end: TaskStatus) -> TaskRecord:
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(ws))
    path = [
        (TaskStatus.CONTEXT_READY, "ctx"),
        (TaskStatus.AWAITING_PLAN_APPROVAL, "approval"),
        (TaskStatus.PLANNED, "planned"),
        (TaskStatus.EXECUTING, "executing"),
    ]
    for status, reason in path:
        task = transition(task, status, reason)
        if status == end:
            break
    if end == TaskStatus.AWAITING_STEP_REVIEW:
        task = transition(task, TaskStatus.AWAITING_STEP_REVIEW, "parked")
    await orch._store.create(task)
    return task


@pytest.mark.asyncio
async def test_scope_gate_from_invalid_source_raises(tmp_path: Path) -> None:
    ws = tmp_path / "ws"; ws.mkdir()
    orch = _make(tmp_path, scope_policy=ScopePolicy.ASK, scope_trigger=ScopeTrigger.ANY)
    # Park the task in AWAITING_STEP_REVIEW — an invalid source for a scope gate.
    task = await _seed(orch, ws, TaskStatus.AWAITING_STEP_REVIEW)
    step = PlanStep(id="s1", goal="g", targets=[], risk="low")
    cb = orch._build_scope_callback(task.task_id, "s1", step)

    # Before the fix: the invalid transition is swallowed and the callback blocks on
    # its future forever (wait_for surfaces TimeoutError, not the ValueError we want).
    with pytest.raises(ValueError, match="Invalid transition"):
        await asyncio.wait_for(cb(["extra.py"], "needs helper"), timeout=1.0)
