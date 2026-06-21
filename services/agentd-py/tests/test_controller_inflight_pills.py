"""Finding 5/8: in-flight tool pills are persisted durably DURING the turn, so a
thread switch / panel reopen before the turn completes reconstructs them from the
transcript (not the lossy 50-event replay buffer). At turn end the same in-flight
message is FINALIZED (content + final pills) — no duplicate.
"""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _ctrl(tmp_path, store):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "tool_call", "thought": "look", "tool": "read_file",
                 "args": {"path": "f.py"}},
                {"type": "answer", "thought": "done", "answer": "x is 1"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)


@pytest.mark.asyncio
async def test_pills_persisted_mid_turn(tmp_path: Path):
    # The durable in-flight record must be written AFTER the tool result and BEFORE the
    # turn completes — that's what a switch/reopen reads. Spy on the upsert to prove it
    # fired mid-turn with the partial pills.
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    snapshots: list[list[str]] = []
    orig = store.upsert_inflight_pills

    def spy(thread_id, turn_id, tool_events, thinking_log=None):
        snapshots.append([e.get("tool") for e in tool_events])
        return orig(thread_id, turn_id, tool_events, thinking_log)

    store.upsert_inflight_pills = spy  # type: ignore[method-assign]
    await _ctrl(tmp_path, store).handle_message(thread.thread_id, "what is x", channel_id="c1")
    assert snapshots, "upsert_inflight_pills never fired — no mid-turn durable copy"
    assert any("read_file" in s for s in snapshots), snapshots


@pytest.mark.asyncio
async def test_inflight_pills_finalized_no_duplicate(tmp_path: Path):
    # At turn end the in-flight message is finalized in place: ONE agent message carries
    # the pills + the answer content, the inflight marker is dropped, and there is no
    # second (duplicate) pills message.
    (tmp_path / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    await _ctrl(tmp_path, store).handle_message(thread.thread_id, "what is x", channel_id="c1")

    msgs = store.get_thread(thread.thread_id).messages
    pill_msgs = [m for m in msgs if m.role == "agent" and (m.metadata or {}).get("tool_events")]
    assert len(pill_msgs) == 1, f"expected ONE finalized pills message, got {len(pill_msgs)}"
    assert pill_msgs[0].content == "x is 1"  # finalized with the answer content
    assert any(e.get("tool") == "read_file" for e in pill_msgs[0].metadata["tool_events"])
    # The inflight marker is dropped on finalize → the message is a normal one.
    assert all(not (m.metadata or {}).get("inflight_turn_id") for m in msgs)
