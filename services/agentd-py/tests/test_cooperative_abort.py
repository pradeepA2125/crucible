from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
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


class _AbortMidStep:
    """Reasoning stub whose first tool-step call raises TaskAborted — simulating the
    cooperative abort firing inside a step's ToolLoop iteration."""
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError
    async def summarize_run(self, **k): return {"headline": "x", "points": []}
    async def create_tool_step(self, *a, **k):
        from agentd.orchestrator.task_control import TaskAborted
        raise TaskAborted()


@pytest.mark.asyncio
async def test_abort_inside_step_toolloop_reaches_aborted_not_failed(tmp_path: Path):
    """Regression (smoke-found): TaskAborted raised inside a step's ToolLoop must unwind to
    ABORTED, not be caught by the per-attempt `except Exception` and retried until the step
    exhausts (which mis-reported a user Stop as a FAILED task and skipped the revert)."""
    from agentd.domain.models import PlanTarget, PlanTargetIntent
    real = tmp_path / "ws"; real.mkdir(); (real / "a.py").write_text("x=1\n")
    store = SQLiteTaskStore(tmp_path / "db.sqlite3")
    wm = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = AgentOrchestrator(store=store, reasoning_engine=_AbortMidStep(),
                             validator=_OkValidator(), patch_engine=PatchEngine(),
                             workspace_manager=wm)
    shadow = await wm.prepare("task-1", str(real))
    task = TaskRecord(
        task_id="task-1", goal="g", workspace_path=str(real),
        shadow_workspace_path=str(shadow.shadow_path), budget=TaskBudget(),
        status=TaskStatus.PLANNED,
        plan=PlanDocument(analysis="s", expected_files=[], stop_conditions=[],
                          steps=[PlanStep(id="s1", goal="edit",
                                          targets=[PlanTarget(path="a.py", intent=PlanTargetIntent.EXISTING)],
                                          risk="low")]),
    )
    await store.create(task)
    # control present (so the loop wires abort into the ToolLoop) but not pre-set, so the
    # between-steps top check doesn't fire first — the abort surfaces from inside the step.
    orch._register_task_control("task-1", step_review_auto_accept=True)
    import time
    out = await orch._execute_plan(task, shadow, RetrievalContext.empty(), [],
                                   int(time.time() * 1000))
    assert out.status == TaskStatus.ABORTED


@pytest.mark.asyncio
async def test_abort_route_sets_live_control(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("x=1\n")
    store = SQLiteTaskStore(tmp_path / "db.sqlite3")
    wm = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = AgentOrchestrator(store=store, reasoning_engine=_NoReason(),
                             validator=_OkValidator(), patch_engine=PatchEngine(),
                             workspace_manager=wm)
    shadow = await wm.prepare("task-1", str(real))
    task = _planned_task(real, Path(shadow.shadow_path))
    task.status = TaskStatus.EXECUTING
    await store.create(task)
    ctrl = orch._register_task_control("task-1", step_review_auto_accept=True)

    app = FastAPI()
    app.include_router(build_router(store, orch, wm, None, None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/tasks/task-1/abort", json={"revert": True})
    assert resp.status_code == 200
    assert ctrl.abort.is_set()
    assert ctrl.abort_revert is True


@pytest.mark.asyncio
async def test_abort_route_409_when_not_running(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    store = SQLiteTaskStore(tmp_path / "db.sqlite3")
    wm = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = AgentOrchestrator(store=store, reasoning_engine=_NoReason(),
                             validator=_OkValidator(), patch_engine=PatchEngine(),
                             workspace_manager=wm)
    task = TaskRecord(task_id="task-9", goal="g", workspace_path=str(real),
                      budget=TaskBudget(), status=TaskStatus.SUCCEEDED)
    await store.create(task)
    app = FastAPI()
    app.include_router(build_router(store, orch, wm, None, None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/tasks/task-9/abort", json={"revert": False})
    assert resp.status_code == 409
