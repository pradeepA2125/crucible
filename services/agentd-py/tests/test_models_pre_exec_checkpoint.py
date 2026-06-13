from agentd.domain.models import TaskExecutionState


def test_execution_state_has_pre_execution_checkpoint_default_none():
    state = TaskExecutionState()
    assert state.pre_execution_checkpoint is None
    state.pre_execution_checkpoint = "/abs/path/_baselines/task-1/shadow"
    assert state.pre_execution_checkpoint.endswith("/shadow")
