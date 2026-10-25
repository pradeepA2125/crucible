"""The /message + /mode-decision routes detach the turn (create_task, subscribe-
relay) so a client disconnect does NOT cancel the work, and a concurrent /message
returns 409. Driven through the real FastAPI router with a ChatController handler."""
import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


class _SlowController(ChatController):
    """A controller whose turn blocks until released — lets the test hold the turn
    in flight while issuing a second request (the 409 path)."""
    def __init__(self, *args, gate: asyncio.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self._gate = gate

    async def handle_message(self, thread_id, message, channel_id, step_review=None,
                             forced_skills=None, mentioned_files=None, plan_mode=None,
                             message_id=None):
        await self._gate.wait()
        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})


@pytest.fixture
def _app(tmp_path):
    # Minimal store/orchestrator/workspace stubs: the chat routes only touch the
    # chat handler; task routes are unused here.
    from agentd.storage.in_memory import InMemoryTaskStore

    store = InMemoryTaskStore()
    chat_store = ChatThreadStore(tmp_path / "chat.sqlite3")
    gate = asyncio.Event()
    handler = _SlowController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=chat_store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None, gate=gate)

    class _Orch:  # build_router reads .broadcaster + ._running_tasks for task routes
        broadcaster = handler._broadcaster
        _running_tasks: set = set()

    app = FastAPI()
    # build_router already self-prefixes "/v1" (see main.py: include_router with no
    # extra prefix), so mount it bare — routes live at /v1/chat/...
    app.include_router(build_router(store, _Orch(), None, None, handler))
    return app, chat_store, gate, handler


async def _consume_stream(client, url: str, json_body: dict) -> None:
    """Drive a streaming POST to completion in a background task.

    httpx's ASGITransport BUFFERS the whole response — `client.stream(...)` does not
    return until the ASGI body generator finishes. A turn parked on a gate would never
    finish, so we cannot hold the stream open inline and fire a second request. Instead
    we consume the stream in a background task: the route handler (and its `launch_turn`)
    still runs synchronously when the app is invoked, registering the turn in
    `_active_turns` before the body blocks — which is all the in-flight guard needs."""
    async with client.stream("POST", url, json=json_body) as resp:
        async for _line in resp.aiter_lines():
            pass


@pytest.mark.asyncio
async def test_second_message_while_active_returns_409(_app):
    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        url = f"/v1/chat/threads/{thread.thread_id}/message"
        # First message: consume in the background so the route registers the turn but
        # the gate keeps it in flight (the background consumer blocks on the buffered body).
        bg = asyncio.create_task(_consume_stream(client, url, {"content": "hi"}))
        await asyncio.sleep(0.05)  # let the detached turn register in _active_turns
        assert thread.thread_id in handler._active_turns
        resp2 = await client.post(url, json={"content": "again"})
        assert resp2.status_code == 409
        gate.set()  # release so the first stream completes cleanly
        await bg


@pytest.mark.asyncio
async def test_mode_decision_registers_detached_turn(_app):
    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")

    # Override resolve_mode to block on the same gate so we can observe registration.
    async def _slow_resolve(thread_id, mode, *, channel_id, goal):
        await handler._gate.wait()
        handler._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
    handler.resolve_mode = _slow_resolve  # type: ignore[assignment]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        url = f"/v1/chat/threads/{thread.thread_id}/mode-decision"
        bg = asyncio.create_task(_consume_stream(client, url, {"mode": "implement"}))
        await asyncio.sleep(0.05)
        assert thread.thread_id in handler._active_turns
        gate.set()
        await bg


@pytest.mark.asyncio
async def test_clarify_decision_registers_detached_turn(_app):
    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")

    # Override resolve_clarify to block on the gate so we can observe registration.
    captured: dict[str, object] = {}

    async def _slow_resolve(thread_id, answer, *, channel_id, goal):
        captured["answer"] = answer
        await handler._gate.wait()
        handler._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
    handler.resolve_clarify = _slow_resolve  # type: ignore[assignment]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        url = f"/v1/chat/threads/{thread.thread_id}/clarify-decision"
        bg = asyncio.create_task(_consume_stream(client, url, {"answer": "src/pricing.py"}))
        await asyncio.sleep(0.05)
        assert thread.thread_id in handler._active_turns
        assert captured["answer"] == "src/pricing.py"
        gate.set()
        await bg


@pytest.mark.asyncio
async def test_live_reports_turn_active(_app):
    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        # No turn yet → turn_active False.
        live0 = await client.get(f"/v1/chat/threads/{thread.thread_id}/live")
        assert live0.json()["turn_active"] is False

        url = f"/v1/chat/threads/{thread.thread_id}/message"
        bg = asyncio.create_task(_consume_stream(client, url, {"content": "hi"}))
        await asyncio.sleep(0.05)
        live1 = await client.get(f"/v1/chat/threads/{thread.thread_id}/live")
        assert live1.json()["turn_active"] is True
        gate.set()
        await bg


@pytest.mark.asyncio
async def test_stop_route_cancels_active_turn(_app):
    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        url = f"/v1/chat/threads/{thread.thread_id}/message"
        bg = asyncio.create_task(_consume_stream(client, url, {"content": "hi"}))
        await asyncio.sleep(0.05)
        assert thread.thread_id in handler._active_turns
        resp = await client.post(f"/v1/chat/threads/{thread.thread_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # The cancelled turn's relay closes on the stop's chat_done broadcast.
        await bg
    assert thread.thread_id not in handler._active_turns
    # Idle thread → benign no-op.
    transport2 = ASGITransport(app=app)
    async with AsyncClient(transport=transport2, base_url="http://t") as client:
        resp = await client.post(f"/v1/chat/threads/{thread.thread_id}/stop")
        assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_thread_list_reports_real_detached_turn_and_gate_count(_app):
    from agentd.chat.models import PendingGate

    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async def summary():
            response = await client.get("/v1/chat/threads", params={"workspace": "/ws"})
            assert response.status_code == 200
            return response.json()["threads"][0]

        before = await summary()
        assert before["turn_active"] is False
        assert before["pending_gates"] == 0
        bg = asyncio.create_task(_consume_stream(
            client, f"/v1/chat/threads/{thread.thread_id}/message", {"content": "hi"}))
        try:
            for _ in range(100):
                if thread.thread_id in handler._active_turns:
                    break
                await asyncio.sleep(0)
            assert thread.thread_id in handler._active_turns
            pending = chat_store.add_controller_gate(
                thread.thread_id, PendingGate(kind="command", payload={"command": "pwd"}))
            active = await summary()
            assert active["turn_active"] is True
            assert active["pending_gates"] == 1
            # Reading summaries does not consume the gate.
            assert chat_store.get_thread(thread.thread_id).pending_controller_gates[0].gate_id == pending.gate_id
            chat_store.remove_controller_gate(thread.thread_id, pending.gate_id)
        finally:
            gate.set()
            await asyncio.wait_for(bg, timeout=2)
        ended = await summary()
        assert ended["turn_active"] is False
        assert ended["pending_gates"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["message", "mode-decision", "clarify-decision"])
async def test_actual_subscriber_disconnect_preserves_detached_work(_app, decision):
    """Drive http.disconnect directly; ASGITransport otherwise buffers the body."""
    import json

    app, chat_store, gate, handler = _app
    thread = chat_store.create_thread("/ws")
    completed = asyncio.Event()

    async def held_decision(*args, **kwargs):
        await gate.wait()
        completed.set()
        handler._broadcaster.broadcast(
            f"chat:{thread.thread_id}", {"type": "chat_done", "payload": {}})

    if decision == "message":
        handler.handle_message = held_decision
        payload = {"content": "disposable detach test"}
    elif decision == "mode-decision":
        handler.resolve_mode = held_decision
        payload = {"mode": "implement"}
    else:
        handler.resolve_clarify = held_decision
        payload = {"answer": "yes"}

    started = asyncio.Event()
    disconnect = asyncio.Event()
    supplied = False
    status = None

    async def receive():
        nonlocal supplied
        if not supplied:
            supplied = True
            return {"type": "http.request", "body": json.dumps(payload).encode(),
                    "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            started.set()

    path = f"/v1/chat/threads/{thread.thread_id}/{decision}"
    request = asyncio.create_task(app({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345), "server": ("test", 80),
    }, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert status == 200
        work = handler._active_turns[thread.thread_id]
        disconnect.set()
        await asyncio.wait_for(request, 2)
        assert not work.done()
        assert not work.cancelled()
        gate.set()
        await asyncio.wait_for(work, 2)
        assert completed.is_set()
    finally:
        disconnect.set()
        gate.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
