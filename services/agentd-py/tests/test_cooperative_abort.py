from pathlib import Path

import pytest

from agentd.domain.models import (
    PlanDocument,
    PlanStep,
    TaskBudget,
    TaskRecord,
    TaskStatus,
)
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.retrieval.artifact_client import RetrievalContext
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoReason:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _OkValidator:
    async def run(self, _p):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _orch(tmp_path: Path) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"),
        reasoning_engine=_NoReason(),
        validator=_OkValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


def _planned_task(real: Path, shadow_path: Path) -> TaskRecord:
    return TaskRecord(
        task_id="task-1", goal="g", workspace_path=str(real),
        shadow_workspace_path=str(shadow_path), budget=TaskBudget(),
        status=TaskStatus.PLANNED,
        plan=PlanDocument(
            analysis="s",
            steps=[PlanStep(id="s1", goal="noop", targets=[], risk="low")],
            expected_files=[], stop_conditions=[],
        ),
    )


@pytest.mark.asyncio
async def test_abort_between_steps_marks_aborted(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("x=1\n")
    orch = _orch(tmp_path)
    shadow = await orch._workspace_manager.prepare("task-1", str(real))
    task = _planned_task(real, Path(shadow.shadow_path))
    await orch._store.create(task)

    # Register a control whose abort is already set → loop should bail before running s1.
    ctrl = orch._register_task_control(task.task_id, step_review_auto_accept=True)
    ctrl.abort.set()
    out = await orch._execute_plan(task, shadow, RetrievalContext.empty(), [], 0)
    assert out.status == TaskStatus.ABORTED
    # s1 never ran (no patch applied), so the file is untouched.
    assert (real / "a.py").read_text() == "x=1\n"
    # Control released by _execute_plan's finally.
    assert orch.get_task_control(task.task_id) is None
