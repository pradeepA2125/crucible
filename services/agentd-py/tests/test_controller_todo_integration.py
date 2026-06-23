from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.chat.todo_ledger import TodoLedger
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _ctrl(tmp_path, store, responses):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=responses),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)


@pytest.mark.asyncio
async def test_write_todos_persists_on_nonterminal_turn(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = _ctrl(tmp_path, store, [
        {"type": "tool_call", "thought": "plan", "tool": "write_todos",
         "args": {"items": [{"title": "Enemies", "status": "pending"},
                            {"title": "Jump", "status": "pending"}]}},
        {"type": "propose_mode", "thought": "big", "plan_sketch": "1. Enemies 2. Jump",
         "recommended": "edit", "reason": "multi-part",
         "options": [{"mode": "edit", "label": "Edit inline now", "description": "do it"},
                     {"mode": "explain", "label": "Just explain", "description": "describe"}]},
    ])
    await ctrl.handle_message(thread.thread_id, "add enemies and jump", channel_id="c1")
    led = TodoLedger.from_json(store.get_controller_todos(thread.thread_id))
    assert [i.title for i in led.items] == ["Enemies", "Jump"]


@pytest.mark.asyncio
async def test_terminal_answer_clears_persisted_ledger(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_todos(thread.thread_id, '[{"title": "stale", "status": "done", "note": ""}]')
    ctrl = _ctrl(tmp_path, store, [{"type": "answer", "thought": "t", "answer": "done"}])
    await ctrl.handle_message(thread.thread_id, "what does this do", channel_id="c1")
    assert store.get_controller_todos(thread.thread_id) is None
