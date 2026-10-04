"""Rewind routes: preview counts, refusals, and the destructive POST."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ValidationResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _build(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    task_store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    orch = AgentOrchestrator(
        store=task_store, reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(), workspace_manager=ws_manager, chat_store=chat_store,
    )
    controller = ChatController(
        workspace_path=str(ws), reasoning_engine=_NoopReasoning(), thread_store=chat_store,
        orchestrator=orch, broadcaster=orch.broadcaster,
        rewind_store=RewindStore(chat_store, ws),
    )
    app = FastAPI()
    app.include_router(build_router(task_store, orch, ws_manager, None, controller))
    thread = chat_store.create_thread(str(ws), "t")
    msg_id = chat_store.append_message(
        thread.thread_id, ChatMessage(role="user", content="go"))
    controller._rewind.open_checkpoint(
        thread.thread_id, msg_id, "turn1",
        thread=chat_store.get_thread(thread.thread_id), memory_anchor_md=None)
    return app, controller, thread.thread_id, msg_id


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_preview_returns_counts(tmp_path: Path):
    app, _c, tid, msg_id = _build(tmp_path)
    async with _client(app) as client:
        r = await client.get(f"/v1/chat/threads/{tid}/rewind-preview",
                             params={"message_id": msg_id})
    assert r.status_code == 200
    assert r.json()["messages"] == 1


@pytest.mark.asyncio
async def test_preview_unknown_message_is_404(tmp_path: Path):
    app, _c, tid, _m = _build(tmp_path)
    async with _client(app) as client:
        r = await client.get(f"/v1/chat/threads/{tid}/rewind-preview",
                             params={"message_id": "nope"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rewind_refuses_while_a_turn_is_in_flight(tmp_path: Path):
    app, controller, tid, msg_id = _build(tmp_path)
    controller._active_turns[tid] = object()
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
    assert r.status_code == 409
    assert "in flight" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rewind_truncates_and_returns_prefill(tmp_path: Path):
    app, controller, tid, msg_id = _build(tmp_path)
    controller._store.append_message(tid, ChatMessage(role="agent", content="dropped"))
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
    assert r.status_code == 200
    body = r.json()
    assert body["removed_messages"] == 2
    assert body["prefill_text"] == "go"
    assert controller._store.get_thread(tid).messages == []


@pytest.mark.asyncio
async def test_rewind_unknown_message_is_404(tmp_path: Path):
    app, _c, tid, _m = _build(tmp_path)
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": "nope"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rewind_refuses_while_an_agent_runs(tmp_path: Path):
    """Spec §8.10: a background agent could edit files the rewind restores."""
    from agentd.chat.models import AgentRecord

    app, controller, tid, msg_id = _build(tmp_path)
    controller._store.insert_agent(AgentRecord(
        agent_id="busy", thread_id=tid, turn_id="u", depth=1, name="explore",
        label="scout", prompt="p", status="running", dispatcher_id="main"))
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
        preview = await client.get(f"/v1/chat/threads/{tid}/rewind-preview",
                                   params={"message_id": msg_id})
    assert r.status_code == 409 and "scout" in r.json()["detail"]
    assert preview.json()["blocked_by_agents"] == ["scout"]


@pytest.mark.asyncio
async def test_rewind_to_a_queued_message_uses_its_notice_turn_checkpoint(tmp_path: Path):
    """Spec §5.3: a message delivered into a notice turn shares that turn's checkpoint."""
    app, controller, tid, msg_id = _build(tmp_path)
    queued = controller._store.append_message(tid, ChatMessage(
        role="user", content="also", metadata={"checkpoint_anchor": msg_id}))
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": queued})
    assert r.status_code == 200
    assert controller._store.get_thread(tid).messages == []


@pytest.mark.asyncio
async def test_rewind_drops_the_controllers_cached_history(tmp_path: Path):
    """The next turn must seed from the restored history, not the pre-rewind copy the
    controller cached in memory (found live: the model remembered rewound turns until
    the backend restarted)."""
    app, controller, tid, msg_id = _build(tmp_path)
    stale = [{"role": "user", "content": "a rewound turn"}]
    controller._histories[tid] = stale
    controller._seeds[tid] = {"stale": True}
    controller._observed_prompts[tid] = object()
    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
    assert r.status_code == 200
    assert tid not in controller._histories
    assert tid not in controller._seeds and tid not in controller._observed_prompts
    assert controller._seed_for(tid) == []   # what the checkpoint restored
