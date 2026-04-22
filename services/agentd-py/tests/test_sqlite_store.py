from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentd.domain.models import TaskRecord, TaskStatus
from agentd.domain.state_machine import transition
from agentd.storage.sqlite_store import SQLiteTaskStore


@pytest.mark.asyncio
async def test_sqlite_store_persists_tasks_and_events(tmp_path: Path) -> None:
    database_path = tmp_path / "agentd.sqlite3"
    store = SQLiteTaskStore(database_path=database_path)

    task = TaskRecord(task_id="task-1", goal="goal", workspace_path=str(tmp_path))
    await store.create(task)

    loaded = await store.get("task-1")
    assert loaded.task_id == "task-1"
    assert loaded.status == TaskStatus.QUEUED

    task = transition(task, TaskStatus.CONTEXT_READY, "context assembled")
    await store.save(task)

    reloaded = await store.get("task-1")
    assert reloaded.status == TaskStatus.CONTEXT_READY
    assert len(reloaded.events) == 1

    conn = sqlite3.connect(database_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?", ("task-1",)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert int(row[0]) == 1


# ---------------------------------------------------------------------------
# get_task_events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_events_empty_for_new_task(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)

    events = await store.get_task_events("t1")
    assert events == []


@pytest.mark.asyncio
async def test_get_task_events_returns_events_in_order(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)

    task = transition(task, TaskStatus.CONTEXT_READY, "context assembled")
    await store.save(task)
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "plan generated")
    await store.save(task)
    task = transition(task, TaskStatus.PLANNED, "plan approved")
    await store.save(task)

    events = await store.get_task_events("t1")

    assert len(events) == 3
    assert events[0].from_status == TaskStatus.QUEUED
    assert events[0].to_status == TaskStatus.CONTEXT_READY
    assert events[0].reason == "context assembled"
    assert events[1].from_status == TaskStatus.CONTEXT_READY
    assert events[1].to_status == TaskStatus.AWAITING_PLAN_APPROVAL
    assert events[2].from_status == TaskStatus.AWAITING_PLAN_APPROVAL
    assert events[2].to_status == TaskStatus.PLANNED


@pytest.mark.asyncio
async def test_get_task_events_at_is_timezone_aware(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)
    task = transition(task, TaskStatus.CONTEXT_READY, "init")
    await store.save(task)

    events = await store.get_task_events("t1")
    assert events[0].at.tzinfo is not None


@pytest.mark.asyncio
async def test_get_task_events_raises_for_unknown_task(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")

    with pytest.raises(KeyError, match="no-such-task"):
        await store.get_task_events("no-such-task")


@pytest.mark.asyncio
async def test_get_task_events_isolated_per_task(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")
    t1 = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    t2 = TaskRecord(task_id="t2", goal="g", workspace_path=str(tmp_path))
    await store.create(t1)
    await store.create(t2)

    t1 = transition(t1, TaskStatus.CONTEXT_READY, "t1 init")
    await store.save(t1)
    t1 = transition(t1, TaskStatus.FAILED, "t1 fail")
    await store.save(t1)

    # t2 gets no transitions
    assert await store.get_task_events("t2") == []
    t1_events = await store.get_task_events("t1")
    assert len(t1_events) == 2
    assert all(e.reason.startswith("t1") for e in t1_events)
