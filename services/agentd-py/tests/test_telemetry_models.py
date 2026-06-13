from agentd.domain.models import FailureSummary, RunSummary, TaskBudget, TaskRecord


def test_summaries_default_none_and_assignable():
    t = TaskRecord(task_id="t", goal="g", workspace_path="/w", budget=TaskBudget())
    assert t.failure_summary is None and t.run_summary is None
    t.failure_summary = FailureSummary(step_id="s1", step_index=3, error_class="VerifyPhaseExhausted", message="m")
    t.run_summary = RunSummary(steps_completed=2, steps_total=4, deviations=["scope: x.py"])
    assert t.failure_summary.error_class == "VerifyPhaseExhausted"
    assert t.run_summary.steps_completed == 2
