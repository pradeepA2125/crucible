"""Smoke-found: an abort before any step completes leaves run_events empty, and the
LLM summarizer then defaults to 'aborted during initial planning phase' (wrong — the
abort may have happened mid-execution). When there are no run_events, the narrative
must be deterministic, not an LLM hallucination."""
from pathlib import Path

import pytest

from agentd.domain.models import PlanDocument, PlanStep, TaskBudget, TaskRecord
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _PlanningPhaseReasoner:
    """summarize_run returns a misleading 'planning phase' headline and records that it
    was called — so the test can prove the engine does NOT consult it when run_events
    is empty."""
    called = False

    async def summarize_run(self, *, goal, outcome, run_events, deviations, modified_files):
        type(self).called = True
        return {"headline": "Task aborted during initial planning phase", "points": ["x"]}

    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


def _orch(tmp_path: Path, reasoner) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"),
        reasoning_engine=reasoner,
        validator=None,
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


@pytest.mark.asyncio
async def test_aborted_with_no_run_events_uses_deterministic_narrative(tmp_path: Path):
    reasoner = _PlanningPhaseReasoner()
    orch = _orch(tmp_path, reasoner)
    task = TaskRecord(
        task_id="t", goal="add a feature", workspace_path="/w", budget=TaskBudget(),
        plan=PlanDocument(analysis="s", expected_files=[], stop_conditions=[],
                          steps=[PlanStep(id="s1", goal="g", targets=[], risk="low")]),
    )
    # run_events is empty (abort before any step_done/step_failed event)
    assert task.execution_state.run_events == []

    await orch._finalize_task_narrative(task, "aborted")

    assert reasoner.called is False, "summarize_run must NOT be called with an empty event log"
    assert task.task_narrative is not None
    assert task.task_narrative.outcome == "aborted"
    assert "planning phase" not in task.task_narrative.headline.lower()


@pytest.mark.asyncio
async def test_aborted_with_events_still_uses_llm_summary(tmp_path: Path):
    """Guard: the deterministic path is ONLY for the empty-log case — a real event log
    must still go through summarize_run."""
    from agentd.domain.models import RunEvent

    reasoner = _PlanningPhaseReasoner()
    orch = _orch(tmp_path, reasoner)
    task = TaskRecord(
        task_id="t2", goal="add a feature", workspace_path="/w", budget=TaskBudget(),
        plan=PlanDocument(analysis="s", expected_files=[], stop_conditions=[],
                          steps=[PlanStep(id="s1", goal="g", targets=[], risk="low")]),
    )
    task.execution_state.run_events.append(
        RunEvent(kind="step_done", step_id="s1", goal="g", note="did the thing")
    )

    await orch._finalize_task_narrative(task, "aborted")

    assert reasoner.called is True
