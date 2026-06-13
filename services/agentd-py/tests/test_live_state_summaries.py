from agentd.chat.live_state import resolve_live_state
from agentd.domain.models import (
    FailureSummary,
    RunSummary,
    TaskBudget,
    TaskRecord,
    TaskStatus,
)


def _get(task: TaskRecord):
    return lambda _id: task


def test_live_state_surfaces_failure_and_run_summary():
    task = TaskRecord(
        task_id="t", goal="g", workspace_path="/w", budget=TaskBudget(),
        status=TaskStatus.FAILED,
        failure_summary=FailureSummary(error_class="VerifyPhaseExhausted", message="m"),
        run_summary=RunSummary(steps_completed=2, steps_total=4, deviations=[]),
    )
    live = resolve_live_state(task.task_id, _get(task))
    assert live.failure_summary is not None
    assert live.failure_summary.error_class == "VerifyPhaseExhausted"
    assert live.run_summary is not None
    assert live.run_summary.steps_completed == 2


def test_live_state_omits_failure_summary_when_not_terminal():
    # run_summary surfaces whenever present; failure_summary only at FAILED/ABORTED.
    task = TaskRecord(
        task_id="t", goal="g", workspace_path="/w", budget=TaskBudget(),
        status=TaskStatus.EXECUTING,
        failure_summary=FailureSummary(error_class="X", message="m"),
        run_summary=RunSummary(steps_completed=1, steps_total=3, deviations=[]),
    )
    live = resolve_live_state(task.task_id, _get(task))
    assert live.failure_summary is None
    assert live.run_summary is not None
