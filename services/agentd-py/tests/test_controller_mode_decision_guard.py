import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _ctrl(tmp_path, store):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)


@pytest.mark.asyncio
async def test_mode_decision_rejects_create_task_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_EDITOR_TASK_SUBSYSTEM", "0")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_gate(thread.thread_id, PendingGate(
        kind="mode", payload={"plan_sketch": "x", "options": [
            {"mode": "create_task", "label": "Plan", "description": "d"}]}))
    ctrl = _ctrl(tmp_path, store)
    with pytest.raises(ValueError, match="task subsystem"):
        await ctrl.resolve_mode(thread.thread_id, "create_task", channel_id="c1", goal="g")
