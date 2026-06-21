from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_edit_phase_promotes_then_submits(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()  # simulate user picked edit
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "bumped x"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm, edit_session=sess)
    out = await loop.run(
        {"goal": "bump x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert (real / "f.py").read_text() == "x = 2\n"  # instant-promoted


def _drain(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_failed_edit_surfaces_thinking_line_and_no_card(tmp_path: Path):
    """A failed edit attempt is invisible today (no card — edit_record_cb only fires on
    success). Surface it: a durable thinking_log entry + a live chat_agent_thinking event,
    so the UI shows "✗ edit failed: <reason>" instead of a silent wait. No diff card."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "NOPE_NOT_PRESENT", "replace": "y", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "n/a"},
    ]
    cards: list = []

    async def _record(diff, decision, reason):
        cards.append(diff)

    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, bc, channel_id="c", phase_sm=sm, edit_session=sess)
    out = await loop.run(
        {"goal": "x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True, edit_record_cb=_record)

    # No diff card for a failed edit.
    assert cards == []
    # Durable: the failed attempt lands in the turn's thinking_log.
    assert any("edit failed" in t for t in (out.thinking_log or [])), out.thinking_log
    # Live: a thinking event was broadcast for the failure.
    msgs = [e["payload"].get("message", "")
            for e in _drain(q) if e["type"] == "chat_agent_thinking"]
    assert any("edit failed" in m for m in msgs), msgs
