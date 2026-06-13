import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import (
    StepReviewPayload,
    TaskBudget,
    TaskRecord,
    TaskStatus,
)
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
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


def _build(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "db.sqlite3")
    wm = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = AgentOrchestrator(store=store, reasoning_engine=_NoReason(),
                             validator=_OkValidator(), patch_engine=PatchEngine(),
                             workspace_manager=wm)
    app = FastAPI()
    app.include_router(build_router(store, orch, wm, None, None))
    return store, wm, orch, app


@pytest.mark.asyncio
async def test_review_pref_route_updates_live_control(tmp_path: Path):
    store, _wm, orch, app = _build(tmp_path)
    task = TaskRecord(task_id="task-1", goal="g", workspace_path=str(tmp_path),
                      budget=TaskBudget(), status=TaskStatus.EXECUTING)
    await store.create(task)
    orch._register_task_control("task-1", step_review_auto_accept=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/tasks/task-1/review-pref", json={"auto_accept": True})
    assert resp.status_code == 200
    assert orch.get_task_control("task-1").step_review_auto_accept is True


@pytest.mark.asyncio
async def test_review_pref_409_when_not_running(tmp_path: Path):
    store, _wm, _orch, app = _build(tmp_path)
    task = TaskRecord(task_id="task-2", goal="g", workspace_path=str(tmp_path),
                      budget=TaskBudget(), status=TaskStatus.SUCCEEDED)
    await store.create(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/tasks/task-2/review-pref", json={"auto_accept": True})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_review_pref_auto_accept_resolves_pending_step_gate(tmp_path: Path):
    store, _wm, orch, app = _build(tmp_path)
    task = TaskRecord(task_id="task-3", goal="g", workspace_path=str(tmp_path),
                      budget=TaskBudget(), status=TaskStatus.AWAITING_STEP_REVIEW)
    task.execution_state.pending_step_review = StepReviewPayload(
        step_id="s1", step_title="t", diff_entries=[],
    )
    await store.create(task)
    orch._register_task_control("task-3", step_review_auto_accept=False)
    # Simulate the engine parked at the step gate, awaiting its decision future.
    future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
    orch._pending_step_decisions["task-3"] = future

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/tasks/task-3/review-pref", json={"auto_accept": True})
    assert resp.status_code == 200
    assert future.done() and future.result() == "accept"
