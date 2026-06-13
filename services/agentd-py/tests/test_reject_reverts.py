from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import TaskBudget, TaskRecord, TaskStatus
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


@pytest.mark.asyncio
async def test_reject_reverts_real_workspace(tmp_path: Path):
    real = tmp_path / "ws"
    (real / "src").mkdir(parents=True)
    (real / "src" / "keep.py").write_text("original\n")
    store = SQLiteTaskStore(tmp_path / "db.sqlite3")
    wm = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = AgentOrchestrator(store=store, reasoning_engine=_NoReason(),
                             validator=_OkValidator(), patch_engine=PatchEngine(),
                             workspace_manager=wm)
    shadow = await wm.prepare("task-1", str(real))
    shadow_path = Path(shadow.shadow_path)
    task = TaskRecord(task_id="task-1", goal="g", workspace_path=str(real),
                      shadow_workspace_path=str(shadow_path), budget=TaskBudget(),
                      status=TaskStatus.READY_FOR_REVIEW,
                      modified_files=["src/keep.py", "src/new.py"])
    orch._create_pre_execution_checkpoint(task, shadow_path)
    await store.create(task)
    # simulate a completed run: real workspace already has the task's changes
    (real / "src" / "keep.py").write_text("changed\n")
    (real / "src" / "new.py").write_text("created\n")

    app = FastAPI()
    app.include_router(build_router(store, orch, wm, None, None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/tasks/task-1/reject", json={"reason": "not needed"})

    assert resp.status_code == 200
    assert (real / "src" / "keep.py").read_text() == "original\n"   # reverted
    assert not (real / "src" / "new.py").exists()                    # created file removed
    assert (await store.get("task-1")).status == TaskStatus.ABORTED
