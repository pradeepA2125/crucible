from agentd.orchestrator.task_control import TaskAborted, TaskControl


def test_task_control_defaults_and_mutation():
    c = TaskControl(step_review_auto_accept=True)
    assert not c.abort.is_set()
    assert c.abort_revert is False
    assert c.step_review_auto_accept is True
    c.abort_revert = True
    c.abort.set()
    c.step_review_auto_accept = False
    assert c.abort.is_set() and c.abort_revert and not c.step_review_auto_accept


def test_task_aborted_is_exception():
    assert issubclass(TaskAborted, Exception)
