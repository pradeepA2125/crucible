from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import TaskRecord, TaskStatus
from agentd.domain.state_machine import transition
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _DummyOrchestrator:
    async def run_task(self, task_id: str) -> None: ...
    async def continue_task(self, task_id: str, feedback: str | None = None) -> None: ...
    async def resume_task(self, task_id: str) -> None: ...


def _build_app(store: InMemoryTaskStore | SQLiteTaskStore, tmp_path: Path) -> FastAPI:
    app = FastAPI()
    workspace_manager = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    app.include_router(build_router(store, _DummyOrchestrator(), workspace_manager))
    return app


def _client(store: InMemoryTaskStore | SQLiteTaskStore, tmp_path: Path) -> AsyncClient:
    transport = ASGITransport(_build_app(store, tmp_path))
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# InMemoryTaskStore — endpoint behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_events_endpoint_returns_empty_list_for_new_task(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)

    async with _client(store, tmp_path) as client:
        resp = await client.get("/v1/tasks/t1/events")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_events_endpoint_returns_404_for_unknown_task(tmp_path: Path) -> None:
    store = InMemoryTaskStore()

    async with _client(store, tmp_path) as client:
        resp = await client.get("/v1/tasks/does-not-exist/events")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_events_endpoint_returns_events_in_order(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)

    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    await store.save(task)
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "planned")
    await store.save(task)
    task = transition(task, TaskStatus.PLANNED, "approved")
    await store.save(task)
    task = transition(task, TaskStatus.EXECUTING, "executing")
    await store.save(task)

    async with _client(store, tmp_path) as client:
        resp = await client.get("/v1/tasks/t1/events")

    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 4

    statuses = [(e["from_status"], e["to_status"]) for e in events]
    assert statuses == [
        ("QUEUED", "CONTEXT_READY"),
        ("CONTEXT_READY", "AWAITING_PLAN_APPROVAL"),
        ("AWAITING_PLAN_APPROVAL", "PLANNED"),
        ("PLANNED", "EXECUTING"),
    ]


@pytest.mark.asyncio
async def test_events_endpoint_response_shape(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)

    task = transition(task, TaskStatus.CONTEXT_READY, "context assembled")
    await store.save(task)

    async with _client(store, tmp_path) as client:
        resp = await client.get("/v1/tasks/t1/events")

    event = resp.json()[0]
    assert set(event.keys()) >= {"at", "from_status", "to_status", "reason"}
    assert event["from_status"] == "QUEUED"
    assert event["to_status"] == "CONTEXT_READY"
    assert event["reason"] == "context assembled"
    # at is an ISO datetime string
    assert "T" in event["at"]


# ---------------------------------------------------------------------------
# SQLiteTaskStore — events survive persistence round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_events_endpoint_reads_from_sqlite(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    await store.create(task)

    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    await store.save(task)
    task = transition(task, TaskStatus.FAILED, "exploded")
    await store.save(task)

    # Reopen store to ensure reads come from DB, not memory
    store2 = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")

    async with _client(store2, tmp_path) as client:
        resp = await client.get("/v1/tasks/t1/events")

    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 2
    assert events[-1]["to_status"] == "FAILED"
    assert events[-1]["reason"] == "exploded"


@pytest.mark.asyncio
async def test_events_endpoint_404_from_sqlite_for_unknown_task(tmp_path: Path) -> None:
    store = SQLiteTaskStore(database_path=tmp_path / "agentd.sqlite3")

    async with _client(store, tmp_path) as client:
        resp = await client.get("/v1/tasks/ghost/events")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_events_not_contaminated_across_tasks(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    t1 = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    t2 = TaskRecord(task_id="t2", goal="g", workspace_path=str(tmp_path))
    await store.create(t1)
    await store.create(t2)

    t1 = transition(t1, TaskStatus.CONTEXT_READY, "t1 only")
    await store.save(t1)

    async with _client(store, tmp_path) as client:
        r1 = await client.get("/v1/tasks/t1/events")
        r2 = await client.get("/v1/tasks/t2/events")

    assert len(r1.json()) == 1
    assert r2.json() == []
