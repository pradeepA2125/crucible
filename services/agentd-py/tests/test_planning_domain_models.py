from agentd.domain.models import (
    DeltaReplanRequest,
    PlanRevisionResult,
    PlanningResult,
    RevisedStep,
    TaskBudget,
    TaskExecutionState,
    TaskRecord,
)
from datetime import datetime, timezone


def test_task_budget_new_fields():
    b = TaskBudget()
    assert b.max_planning_tool_calls == 20
    assert b.max_revision_tool_calls == 10
    assert b.max_delta_replans == 3


def test_task_execution_state_defaults():
    s = TaskExecutionState()
    assert s.current_step_id is None
    assert s.step_checkpoints == {}
    assert s.delta_replans_used == 0
    assert s.delta_replan_requests == []


def test_task_record_has_execution_state():
    r = TaskRecord(task_id="t1", goal="g", workspace_path="/ws")
    assert isinstance(r.execution_state, TaskExecutionState)


def test_revised_step_model():
    rs = RevisedStep(
        step_id="s1",
        goal="Fix auth",
        targets=[{"path": "src/auth.py", "intent": "existing"}],
        implementation_details="Add logging",
    )
    assert rs.risk == "low"
    assert rs.edge_cases == ""


def test_delta_replan_request_fields():
    r = DeltaReplanRequest(
        requested_by_step_id="s2",
        reason="wrong file",
        evidence="grep found it in other.py",
        hinted_affected_steps=["s3"],
        requested_at=datetime.now(timezone.utc),
    )
    assert r.requested_by_step_id == "s2"
    assert r.hinted_affected_steps == ["s3"]
