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
    sm = ControllerPhaseSM()  # ACTIVE is the default phase (Task 1)
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
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm,
        edit_session_factory=lambda: sess)
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
async def test_failed_edit_surfaces_live_only_and_no_card(tmp_path: Path):
    """A failed edit attempt is invisible on its own (no card — edit_record_cb only fires
    on success), so it still needs a LIVE chat_agent_thinking event: the UI shows
    "✗ edit failed: <reason>" instead of a silent wait.

    But it must NOT reach the durable thinking_log. An engine/preflight error is not model
    reasoning, and thinking_log is replayed as the turn's permanent reasoning trace — the
    same rule _on_retry follows by broadcasting retry_status on its own channel. Baking
    machine errors in also feeds a weak model its own failures on reload, the repetition
    attractor this branch already avoids by not echoing patch_ops into history."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()  # ACTIVE is the default phase (Task 1)
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
        reg, bc, channel_id="c", phase_sm=sm, edit_session_factory=lambda: sess)
    out = await loop.run(
        {"goal": "x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True, edit_record_cb=_record)

    # No diff card for a failed edit.
    assert cards == []
    # Durable: the failure must NOT be baked into the permanent thinking log.
    assert not any("edit failed" in t for t in (out.thinking_log or [])), out.thinking_log
    # Live: a thinking event was still broadcast for the failure.
    msgs = [e["payload"].get("message", "")
            for e in _drain(q) if e["type"] == "chat_agent_thinking"]
    assert any("edit failed" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_failed_edit_guidance_matches_the_failure_code(tmp_path: Path):
    """The guidance appended after PATCH FAILED used to be one frozen sentence about the
    'file' field — advice for the code-in-'file' malformation, sent for EVERY failure.
    On an anchor miss the 'file' field is correct and 'search' is what broke, so that
    sentence points the model at the wrong field. Guidance now follows the failure code."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
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
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm,
        edit_session_factory=lambda: sess)
    out = await loop.run(
        {"goal": "x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True)

    failed = [str(h.get("content", "")) for h in (out.history or [])
              if "PATCH FAILED" in str(h.get("content", ""))]
    assert failed, out.history
    msg = failed[0]
    # The message itself now names the file (engine-side fix).
    assert "f.py" in msg, msg
    # Anchor-specific guidance, not the code-in-'file' boilerplate.
    assert "anchor" in msg.lower(), msg
    assert "code goes in 'content'" not in msg, msg
