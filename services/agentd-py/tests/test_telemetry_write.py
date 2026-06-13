from pathlib import Path

import pytest

from agentd.domain.models import (
    PlanDocument,
    PlanStep,
    RunSummary,
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


def _plan(n: int) -> PlanDocument:
    return PlanDocument(
        analysis="s", expected_files=[], stop_conditions=[],
        steps=[PlanStep(id=f"s{i}", goal="g", targets=[], risk="low") for i in range(1, n + 1)],
    )


def test_finalize_run_summary_counts_completed_and_total(tmp_path: Path):
    orch = _orch(tmp_path)
    task = TaskRecord(task_id="t", goal="g", workspace_path="/w", budget=TaskBudget(),
                      completed_step_ids=["s1", "s2"], plan=_plan(4))
    task.execution_state.delta_replans_used = 1
    orch._finalize_run_summary(task)
    assert task.run_summary == RunSummary(
        steps_completed=2, steps_total=4, deviations=["1 delta replan(s)"],
    )


@pytest.mark.asyncio
async def test_failed_run_populates_both_summaries(tmp_path: Path, monkeypatch):
    """A crash during execution → FAILED with BOTH failure_summary and run_summary set
    and persisted."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("x=1\n")
    orch = _orch(tmp_path)
    shadow = await orch._workspace_manager.prepare("task-1", str(real))
    task = TaskRecord(task_id="task-1", goal="g", workspace_path=str(real),
                      shadow_workspace_path=str(shadow.shadow_path), budget=TaskBudget(),
                      status=TaskStatus.PLANNED, plan=_plan(3))
    await orch._store.create(task)

    async def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(orch, "_run_step_with_retries", _boom)
    out = await orch._execute_plan(task, shadow, RetrievalContext.empty(), [], 0)

    assert out.status == TaskStatus.FAILED
    assert out.failure_summary is not None
    assert out.failure_summary.error_class == "RuntimeError"
    assert "kaboom" in out.failure_summary.message
    assert out.run_summary is not None
    assert out.run_summary.steps_total == 3
    # persisted, not just in-memory
    stored = await orch._store.get("task-1")
    assert stored.failure_summary is not None and stored.run_summary is not None
