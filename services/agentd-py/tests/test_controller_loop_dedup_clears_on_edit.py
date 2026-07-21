"""Fix: the tool_call dedup cache (`seen`) must clear when an edit is accepted.

Root cause (found live 2026-07-17, alpha-forge phase-1 execution): the model ran
`run_command uv add ...`, hit a real build failure, correctly diagnosed and fixed
the underlying pyproject.toml, then needed to retry the EXACT SAME `uv add`
command — the objectively correct next step. But `seen` (the (tool, args) dedup
guard) is populated once per `run()` call and never cleared, so the legitimate
retry was rejected as "DUPLICATE BLOCKED" every single iteration, forever — the
model had no way to re-run a command whose args happen to match an earlier call,
even though the underlying workspace state had genuinely changed via an accepted
edit in between. Observed live: 35+ consecutive wasted iterations (~30 minutes)
with zero progress before the turn was manually stopped.

Fix: clear `seen` whenever an edit is accepted — mirrors the existing
`emit_patch dedup clears on every transition` pattern in verify_phase_sm.py.
"""
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
async def test_repeated_tool_call_after_edit_is_not_blocked_as_duplicate(tmp_path: Path):
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
    same_call = {"type": "tool_call", "thought": "list it",
                 "tool": "list_directory", "args": {"path": "."}}
    steps = [
        same_call,
        {"type": "edit", "thought": "fix it", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        same_call,
        {"type": "submit_changes", "thought": "done", "summary": "done"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm, edit_session=sess)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=8,
        auto_accept_edits=True)
    assert out.kind == "submit_changes"
    # The second identical tool_call must have actually dispatched (real listing
    # output), not been rejected as a duplicate.
    contents = [
        m.get("content") for m in out.history if m.get("role") == "tool_result"
    ]
    assert not any("DUPLICATE BLOCKED" in str(c) for c in contents), (
        "a tool_call repeated after an accepted edit must not be treated as a "
        "stale duplicate — the workspace state changed in between")


@pytest.mark.asyncio
async def test_identical_tool_call_within_same_state_is_still_blocked(tmp_path: Path):
    # Guard against over-correcting: back-to-back identical calls with NO edit
    # in between are still a mindless repeat and must stay blocked.
    real = tmp_path / "ws"
    real.mkdir()
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    same_call = {"type": "tool_call", "thought": "list it",
                 "tool": "list_directory", "args": {"path": "."}}
    steps = [same_call, same_call, {"type": "submit_changes", "thought": "d", "summary": "d"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=6)
    contents = [
        m.get("content") for m in out.history if m.get("role") == "tool_result"
    ]
    assert any("DUPLICATE BLOCKED" in str(c) for c in contents)
