"""Step-review pause must be re-entrancy-safe.

If a task is already AWAITING_STEP_REVIEW (e.g. an out-of-band step decision races
the orchestrator's own pause), _pause_for_step_review must NOT crash on a
AWAITING_STEP_REVIEW -> AWAITING_STEP_REVIEW self-transition. Mirrors the
command-decision gate's existing re-entrant guard.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.domain.models import PlanStep, StepRunResult, TaskRecord, TaskStatus, ValidationResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NullValidator:
    async def run(self, workspace_path: str) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


@pytest.mark.asyncio
async def test_pause_for_step_review_is_reentrant_safe(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=ScriptedReasoningEngine(plan=None, patches=[]),
        validator=_NullValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    task = TaskRecord(
        task_id="t-reentry",
        goal="x",
        workspace_path=str(tmp_path),
        status=TaskStatus.AWAITING_STEP_REVIEW,  # already in the gate (re-entrant)
    )
    await store.create(task)  # direct create bypasses the state machine
    step = PlanStep(id="s1", goal="g", targets=[], risk="low")
    step_result = StepRunResult(
        step_id="s1",
        outcome="step_completed",
        validation_result="validation_passed",
        attempts_used=1,
        touched_files=[],
    )

    pause = asyncio.create_task(
        orchestrator._pause_for_step_review(task, step, step_result, tmp_path, tmp_path)
    )
    await asyncio.sleep(0.05)  # buggy code raises on the transition before this returns

    assert not pause.done(), (
        f"pause crashed instead of waiting: "
        f"{pause.exception() if pause.done() else ''}"
    )

    # resolve the decision so the coroutine finishes cleanly
    orchestrator._pending_step_decisions["t-reentry"].set_result("accept")
    decision = await pause
    assert decision == "accept"
