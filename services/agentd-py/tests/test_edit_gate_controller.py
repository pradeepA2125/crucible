import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _controller(tmp_path, store):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None)


@pytest.mark.asyncio
async def test_edit_cb_sets_gate_then_resolve_clears_and_returns(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(
        ctrl._edit_decision_cb(th.thread_id, f"chat:{th.thread_id}", []))
    await asyncio.sleep(0)  # let the cb set the gate and start awaiting the future
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "edit"

    assert await ctrl.resolve_edit(th.thread_id, {"decision": "accept"}) is True
    result = await cb_task
    assert result["decision"] == "accept"
    # Gate cleared in place on resolution (Class-A).
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_resolve_edit_returns_false_when_no_pending(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl.resolve_edit(th.thread_id, {"decision": "accept"}) is False


@pytest.mark.asyncio
async def test_edit_decision_timeout_rejects(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_EDITOR_CHAT_EDIT_DECISION_TIMEOUT_SEC", "0.05")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    result = await ctrl._edit_decision_cb(th.thread_id, f"chat:{th.thread_id}", [])
    assert result["decision"] == "reject" and "timed out" in result["reason"]
    assert store.get_thread(th.thread_id).pending_controller_gate is None
