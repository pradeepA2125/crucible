"""NEW-I6: handle_message must actually start the turn's SM in PLAN when plan_mode
is true — not silently ignore it (the stray `resume_phase = None` bug the design
review caught: leaving it unwired would force every plain message into ACTIVE
regardless of the toggle)."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_plan_mode_true_starts_turn_in_plan(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    steps = [{"type": "answer", "thought": "ok", "answer": "in plan mode"}]
    reasoning = ScriptedReasoningEngine(None, [], controller_step_responses=steps)
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=reasoning, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster())
    await ctrl.handle_message(thread.thread_id, "hello", "chan", plan_mode=True)
    trace_path = tmp_path / ".crucible" / "state" / "artifacts" / "chat" / thread.thread_id
    # Simpler + more direct than reading the artifact: assert via the turn-trace file
    # written by _write_turn_trace, which records the phase the loop actually ran in.
    import json
    matches = list(trace_path.glob("*/turn-trace.json"))
    assert matches, "expected a turn-trace artifact"
    data = json.loads(matches[0].read_text())
    assert data["phase"] == "PLAN"


@pytest.mark.asyncio
async def test_plan_mode_false_or_omitted_starts_turn_in_active(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    steps = [{"type": "answer", "thought": "ok", "answer": "in active mode"}]
    reasoning = ScriptedReasoningEngine(None, [], controller_step_responses=steps)
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=reasoning, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster())
    await ctrl.handle_message(thread.thread_id, "hello", "chan")  # plan_mode omitted
    import json
    trace_path = tmp_path / ".crucible" / "state" / "artifacts" / "chat" / thread.thread_id
    matches = list(trace_path.glob("*/turn-trace.json"))
    assert matches
    data = json.loads(matches[0].read_text())
    assert data["phase"] == "ACTIVE"
