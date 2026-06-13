from agentd.domain.models import (
    RunEvent, TaskBudget, TaskNarrative, TaskRecord,
)


def test_run_event_and_narrative_defaults():
    t = TaskRecord(task_id="t", goal="g", workspace_path="/w", budget=TaskBudget())
    assert t.execution_state.run_events == []
    assert t.task_narrative is None
    t.execution_state.run_events.append(
        RunEvent(kind="step_done", step_id="s1", goal="add foo", note="added foo()")
    )
    t.execution_state.run_events.append(
        RunEvent(kind="replan", reason="api changed", reverted_step_ids=["s2"], revised_step_ids=["s2"])
    )
    t.task_narrative = TaskNarrative(outcome="succeeded", headline="Added foo", points=["added foo()"])
    assert t.execution_state.run_events[0].kind == "step_done"
    assert t.execution_state.run_events[1].reverted_step_ids == ["s2"]
    assert t.task_narrative.headline == "Added foo"
