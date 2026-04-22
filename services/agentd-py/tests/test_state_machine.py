import pytest

from agentd.domain.models import TaskEvent, TaskRecord, TaskStatus
from agentd.domain.state_machine import can_transition, transition


def test_valid_transition_path() -> None:
    task = TaskRecord(task_id="t1", goal="goal", workspace_path=".")
    assert task.status == TaskStatus.QUEUED
    assert can_transition(TaskStatus.QUEUED, TaskStatus.CONTEXT_READY)
    assert can_transition(TaskStatus.READY_FOR_REVIEW, TaskStatus.PROMOTING)
    assert can_transition(TaskStatus.PLANNED, TaskStatus.EXECUTING)
    assert can_transition(TaskStatus.VALIDATING, TaskStatus.VALIDATED)


def test_invalid_direct_review_transitions() -> None:
    assert not can_transition(TaskStatus.PLANNED, TaskStatus.READY_FOR_REVIEW)
    assert not can_transition(TaskStatus.EXECUTING, TaskStatus.READY_FOR_REVIEW)
    assert not can_transition(TaskStatus.REPAIRING, TaskStatus.READY_FOR_REVIEW)


def test_task_record_normalizes_legacy_patched_status() -> None:
    task = TaskRecord.model_validate(
        {
            "task_id": "legacy",
            "goal": "goal",
            "workspace_path": ".",
            "status": "PATCHED",
        }
    )
    assert task.status == TaskStatus.EXECUTING


def test_task_event_normalizes_legacy_patched_statuses() -> None:
    event = TaskEvent.model_validate(
        {
            "at": "2026-03-20T00:00:00+00:00",
            "from_status": "PATCHED",
            "to_status": "PATCHED",
            "reason": "legacy",
        }
    )
    assert event.from_status == TaskStatus.EXECUTING
    assert event.to_status == TaskStatus.EXECUTING


def test_transition_requires_validated_before_review() -> None:
    task = TaskRecord(task_id="t2", goal="goal", workspace_path=".")
    task = transition(task, TaskStatus.CONTEXT_READY, "context")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "awaiting")
    task = transition(task, TaskStatus.PLANNED, "planned")
    task = transition(task, TaskStatus.EXECUTING, "executing")
    task = transition(task, TaskStatus.VALIDATING, "validating")
    with pytest.raises(ValueError, match="Invalid transition"):
        transition(task, TaskStatus.READY_FOR_REVIEW, "invalid")
