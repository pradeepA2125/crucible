"""Several gates pending at once (spec §4.5): each decision future is keyed by gate_id,
routes address a gate by gate_id (404 unknown / 409 ambiguous when omitted)."""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import GateAmbiguousError, GateNotFoundError, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, McpToolDecision, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _controller(tmp_path: Path, store: ChatThreadStore) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK)


async def _two_command_gates(ctrl: ChatController, store: ChatThreadStore, tid: str):
    """Raise two concurrent command gates on one thread; return (tasks, gates)."""
    first = asyncio.create_task(ctrl._command_approval_cb(tid, "c1", "ls", ["a"], ""))
    second = asyncio.create_task(ctrl._command_approval_cb(tid, "c1", "ls", ["b"], ""))
    for _ in range(100):
        await asyncio.sleep(0)
        thread = store.get_thread(tid)
        if thread is not None and len(thread.pending_controller_gates) == 2:
            break
    thread = store.get_thread(tid)
    assert thread is not None and len(thread.pending_controller_gates) == 2
    return (first, second), thread.pending_controller_gates


@pytest.mark.asyncio
async def test_two_command_gates_resolve_independently_by_id(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    (first, second), gates = await _two_command_gates(ctrl, store, tid)
    assert gates[0].payload["args"] == ["a"] and gates[1].payload["args"] == ["b"]

    assert await ctrl.resolve_command(tid, CommandDecision(approve=False),
                                      gate_id=gates[1].gate_id) is True
    assert (await second).approved is False
    assert not first.done()
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [gates[0].gate_id]

    assert await ctrl.resolve_command(tid, CommandDecision(approve=True)) is True
    assert (await first).approved is True
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []


@pytest.mark.asyncio
async def test_omitted_gate_id_with_two_of_a_kind_is_ambiguous(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    (first, second), _gates = await _two_command_gates(ctrl, store, tid)
    with pytest.raises(GateAmbiguousError):
        await ctrl.resolve_command(tid, CommandDecision(approve=True))
    with pytest.raises(GateNotFoundError):
        await ctrl.resolve_command(tid, CommandDecision(approve=True), gate_id="nope")
    first.cancel()
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_review_pref_on_accepts_every_pending_edit_gate(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    ctrl._review_control.auto_accept_edits = False
    loop = asyncio.get_running_loop()
    futures = []
    for _ in range(2):
        gate = store.add_controller_gate(tid, PendingGate.new("edit", {}))
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        ctrl._pending_edit[gate.gate_id] = fut
        ctrl._pending_edit_gates[gate.gate_id] = (tid, gate)
        futures.append(fut)
    assert ctrl.set_review_pref(auto_accept=True).auto_resolved == 2
    assert all(f.done() and f.result()["decision"] == "accept" for f in futures)


@pytest.mark.asyncio
async def test_each_restart_orphan_clears_only_itself(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    a = store.add_controller_gate(tid, PendingGate.new("mcp_tool", {"server": "s", "tool": "t"}))
    b = store.add_controller_gate(tid, PendingGate.new("mcp_tool", {"server": "s", "tool": "u"}))
    assert await ctrl.resolve_mcp(tid, McpToolDecision(approve=True), gate_id=a.gate_id) is False
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [b.gate_id]


def _app(tmp_path: Path, ctrl: ChatController) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    return app


@pytest.mark.asyncio
async def test_routes_map_unknown_gate_to_404_and_ambiguous_to_409(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    (first, second), gates = await _two_command_gates(ctrl, store, tid)
    async with AsyncClient(transport=ASGITransport(app=_app(tmp_path, ctrl)),
                           base_url="http://t") as client:
        base = f"/v1/chat/threads/{tid}"
        ambiguous = await client.post(f"{base}/command-decision", json={"approve": True})
        unknown = await client.post(f"{base}/command-decision",
                                    json={"approve": True, "gate_id": "nope"})
        unknown_mcp = await client.post(f"{base}/mcp-decision",
                                        json={"approve": True, "gate_id": "nope"})
        unknown_edit = await client.post(f"{base}/edit-decision",
                                         json={"decision": "accept", "gate_id": "nope"})
        chosen = await client.post(f"{base}/command-decision",
                                   json={"approve": True, "gate_id": gates[0].gate_id})
    assert ambiguous.status_code == 409
    assert unknown.status_code == 404
    assert unknown_mcp.status_code == 404
    assert unknown_edit.status_code == 404
    assert chosen.status_code == 200 and chosen.json() == {"ok": True}
    outcome = await first
    assert outcome.approved is True and type(outcome.decision) is CommandDecision
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)


@pytest.mark.asyncio
async def test_edit_route_does_not_forward_gate_id_into_the_decision(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    gate = store.add_controller_gate(tid, PendingGate.new("edit", {}))
    fut: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    ctrl._pending_edit[gate.gate_id] = fut
    async with AsyncClient(transport=ASGITransport(app=_app(tmp_path, ctrl)),
                           base_url="http://t") as client:
        resp = await client.post(f"/v1/chat/threads/{tid}/edit-decision",
                                 json={"decision": "accept", "gate_id": gate.gate_id})
    assert resp.json() == {"ok": True}
    assert fut.result() == {"decision": "accept"}
