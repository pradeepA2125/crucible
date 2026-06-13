from pathlib import Path

import pytest

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


def _orch(tmp_path: Path) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"),
        reasoning_engine=_NoReason(),
        validator=_OkValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


@pytest.mark.asyncio
async def test_rollback_restores_modified_and_deletes_created(tmp_path: Path):
    # real workspace: keep.py (will be modified), original content
    real = tmp_path / "ws"
    (real / "src").mkdir(parents=True)
    (real / "src" / "keep.py").write_text("original\n")
    orch = _orch(tmp_path)
    shadow = await orch._workspace_manager.prepare("task-1", str(real))
    shadow_path = Path(shadow.shadow_path)

    task = TaskRecord(task_id="task-1", goal="g", workspace_path=str(real),
                      shadow_workspace_path=str(shadow_path), budget=TaskBudget())
    # capture baseline BEFORE any edit
    orch._create_pre_execution_checkpoint(task, shadow_path)
    assert task.execution_state.pre_execution_checkpoint is not None

    # simulate execution: modify keep.py and create new.py in BOTH shadow and real
    # (partial-promote already copied them to real during the run)
    for root in (shadow_path, real):
        (root / "src" / "keep.py").write_text("changed by task\n")
        (root / "src" / "new.py").write_text("created by task\n")
    task.modified_files = ["src/keep.py", "src/new.py"]

    await orch._rollback_to_pre_execution(task)

    assert (real / "src" / "keep.py").read_text() == "original\n"   # restored
    assert not (real / "src" / "new.py").exists()                    # created file deleted


@pytest.mark.asyncio
async def test_execute_plan_captures_baseline_before_first_step(tmp_path: Path, monkeypatch):
    """After EXECUTING, the pre-execution checkpoint is pinned before any step runs."""
    from agentd.domain.models import PlanDocument, PlanStep
    from agentd.retrieval.artifact_client import RetrievalContext

    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("x = 1\n")
    orch = _orch(tmp_path)
    shadow = await orch._workspace_manager.prepare("task-2", str(real))
    task = TaskRecord(
        task_id="task-2", goal="g", workspace_path=str(real),
        shadow_workspace_path=str(shadow.shadow_path), budget=TaskBudget(),
        status=TaskStatus.PLANNED,
        plan=PlanDocument(
            analysis="s",
            steps=[PlanStep(id="s1", goal="noop", targets=[], risk="low")],
            expected_files=[], stop_conditions=[],
        ),
    )
    await orch._store.create(task)
    captured: dict[str, object] = {}

    # Stop after the first step begins: assert the baseline was already captured.
    # _execute_plan swallows the raise (except Exception -> FAILED), so we don't
    # expect it to propagate — we read the captured value + the FAILED outcome.
    async def _fake_run_step(*a, **k):
        captured["baseline"] = task.execution_state.pre_execution_checkpoint
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr(orch, "_run_step_with_retries", _fake_run_step)
    out = await orch._execute_plan(task, shadow, RetrievalContext.empty(), [], 0)
    assert captured["baseline"] is not None
    assert out.status == TaskStatus.FAILED
