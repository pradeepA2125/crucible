from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_qa_turn_persists_answer(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "answer", "thought": "t", "answer": "hello"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(thread.thread_id, "hi", channel_id="c1")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    assert any(m.role == "agent" and "hello" in m.content for m in reloaded.messages)


@pytest.mark.asyncio
async def test_clarify_turn_sets_clarify_gate(tmp_path: Path):
    """A clarify renders as a durable Class-A gate (the question lives in the card),
    not a chat bubble — resolve_clarify writes the combined Q→A breadcrumb later."""
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "clarify", "thought": "t", "question": "which file?"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(thread.thread_id, "change the thing", channel_id="c1")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    gate = reloaded.pending_controller_gate
    assert gate is not None and gate.kind == "clarify"
    assert gate.payload["question"] == "which file?"


@pytest.mark.asyncio
async def test_qa_turn_persists_tool_events_for_reload(tmp_path: Path):
    """Live tool pills die on reload; the durable record is metadata.tool_events on
    the agent message (mirrors ChatAgent). Without it, a reload loses the pills."""
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "tool_call", "thought": "look", "tool": "read_file",
                 "args": {"path": "f.py"}},
                {"type": "answer", "thought": "done", "answer": "x is 1"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(thread.thread_id, "what is x", channel_id="c1")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    persisted = [
        e
        for m in reloaded.messages
        if m.role == "agent" and m.metadata
        for e in (m.metadata.get("tool_events") or [])
    ]
    assert any(e.get("tool") == "read_file" for e in persisted), \
        f"read_file pill not persisted; got {persisted}"


@pytest.mark.asyncio
async def test_first_message_sets_thread_title(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="New Chat")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "answer", "thought": "t", "answer": "ok"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(thread.thread_id, "rename my variables please", channel_id="c1")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None and reloaded.title.startswith("rename my variables")
