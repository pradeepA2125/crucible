"""Payload-free attention metadata over real SQLite and HTTP routes."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "0")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path))
    handler = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[]),
        thread_store=store,
        orchestrator=None,
        broadcaster=EventBroadcaster(),
        retrieval_client=None,
    )
    app = FastAPI()
    orch = SimpleNamespace(broadcaster=handler._broadcaster, _running_tasks=set())
    app.include_router(build_router(InMemoryTaskStore(), orch, None, None, handler))
    yield app, handler, store, thread.thread_id, tmp_path
    store._conn.close()


@pytest.mark.asyncio
async def test_list_and_attention_expose_kinds_without_gate_payloads(fixture):
    app, _, store, tid, root = fixture
    store.add_controller_gate(tid, PendingGate.new("command", {"command": "PRIVATE_COMMAND"}))
    store.add_controller_gate(tid, PendingGate.new("clarify", {"question": "PRIVATE_QUESTION"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/chat/threads", params={"workspace": str(root)})
        assert response.status_code == 200
        summary = response.json()["threads"][0]
        assert summary["gate_kinds"] == ["clarify", "command"]
        assert summary["pending_gates"] == 2
        attention = await client.get("/v1/chat/attention", params={"workspace": str(root)})
        assert attention.json()[0]["gate_kinds"] == ["clarify", "command"]
        assert "PRIVATE_" not in attention.text
        assert "PRIVATE_" not in response.text


@pytest.mark.asyncio
async def test_empty_attention_has_no_invented_decisions(fixture):
    app, _, _, _, root = fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/v1/chat/attention", params={"workspace": str(root)})).json() == []
        summary = (await client.get("/v1/chat/threads", params={"workspace": str(root)})).json()["threads"][0]
        assert summary["gate_kinds"] == []
