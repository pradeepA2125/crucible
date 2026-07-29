"""Coverage for the durable half of the `progress` action (Task 3): ChatController must
wire a real `progress_note_cb` into ControllerLoop so a mid-turn note is persisted as a
transcript message tagged `metadata.progress=true` — surviving a reload. The loop
(Task 2) already broadcasts the live `chat_progress` SSE event and applies the
cap/dedup guardrails; this test drives a REAL ChatController.handle_message turn (not
just the loop) so the wiring itself — not just the loop's own behavior — is covered.
"""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_progress_note_persisted_as_durable_message(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "progress", "thought": "t", "note": "Implementing task 1."},
                {"type": "answer", "thought": "t", "answer": "Done."},
            ]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(thread.thread_id, "do the thing", channel_id="c1")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    progress_msgs = [m for m in reloaded.messages if m.metadata.get("progress") is True]
    assert len(progress_msgs) == 1
    assert progress_msgs[0].content == "Implementing task 1."
    assert progress_msgs[0].role == "agent"
    assert progress_msgs[0].type == "text"
