from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import TaskRecord, TaskStatus
from agentd.orchestrator.broadcaster import PatchEventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.storage.in_memory import InMemoryTaskStore


@pytest.fixture
def store():
    return InMemoryTaskStore()


@pytest.fixture
def orchestrator():
    orch = MagicMock(spec=AgentOrchestrator)
    orch.broadcaster = MagicMock(spec=PatchEventBroadcaster)
    orch._running_tasks = set()
    return orch


@pytest.fixture
def app(store, orchestrator):
    router = build_router(store, orchestrator, MagicMock())
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_stream_patch_404(app: FastAPI):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/tasks/unknown-task/stream-patch")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_patch_idle_task(app: FastAPI, store: InMemoryTaskStore):
    task_id = "task-idle"
    await store.create(
        TaskRecord(
            task_id=task_id,
            goal="goal",
            workspace_path=".",
            status=TaskStatus.FAILED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", f"/v1/tasks/{task_id}/stream-patch") as response:
            assert response.status_code == 200
            lines = []
            async for line in response.aiter_lines():
                if line.strip():
                    lines.append(line)

            assert len(lines) == 1
            assert json.loads(lines[0][6:]) == {"type": "done"}


@pytest.mark.asyncio
async def test_stream_patch_events_injection(
    app: FastAPI, store: InMemoryTaskStore, orchestrator: MagicMock
):
    task_id = "task-running"
    await store.create(
        TaskRecord(
            task_id=task_id,
            goal="goal",
            workspace_path=".",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    orchestrator._running_tasks.add(task_id)

    queue: asyncio.Queue[dict] = asyncio.Queue()
    events = [
        {"type": "candidate_start", "candidate_id": "c1", "step_id": "s1"},
        {
            "type": "operation_success",
            "candidate_id": "c1",
            "op_type": "replace_range",
            "path": "file.py",
        },
        {"type": "candidate_complete", "candidate_id": "c1", "status": "success"},
        {"type": "done"},
    ]
    for e in events:
        queue.put_nowait(e)

    orchestrator.broadcaster.subscribe.return_value = queue

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", f"/v1/tasks/{task_id}/stream-patch") as response:
            assert response.status_code == 200
            received_events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    received_events.append(json.loads(line[6:]))

            assert received_events == events

    orchestrator.broadcaster.unsubscribe.assert_called_once_with(task_id, queue)
