"""Finding 6 fix: state-changing tools (run_command) are barred in the DECIDE phase.

DECIDE is read-only exploration before mode selection — letting the model `run_command`
there lets it write source files to the real workspace via `cat >`/`tee`/`touch`,
bypassing the EditGate entirely. The restriction is enforced at the dispatch guard
(cache-safe: the advertised tool list / system prompt is unchanged; only the runtime
rejects + appends an append-only correction). EDIT phase keeps run_command.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _loop(tmp_path, steps, sm):
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)]
    )
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=sm)


@pytest.mark.asyncio
async def test_run_command_in_decide_is_rejected_not_executed(tmp_path: Path):
    # DECIDE phase. The model tries to create a file via run_command (the Finding 6 bypass).
    # It must be rejected (no side effect on disk) and corrected, then recover.
    steps = [
        {"type": "tool_call", "thought": "write the file",
         "tool": "run_command", "args": {"command": "touch", "args": ["sentinel.txt"]}},
        {"type": "answer", "thought": "ok", "answer": "done"},
    ]
    out = await _loop(tmp_path, steps, ControllerPhaseSM()).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and out.text == "done"
    assert not (tmp_path / "sentinel.txt").exists(), "run_command must NOT run in DECIDE"


@pytest.mark.asyncio
async def test_run_command_in_edit_is_dispatched(tmp_path: Path):
    # EDIT phase: the user has chosen edit mode, so run_command is allowed (it executes).
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    steps = [
        {"type": "tool_call", "thought": "run it",
         "tool": "run_command", "args": {"command": "touch", "args": ["sentinel.txt"]}},
        {"type": "submit_changes", "thought": "done", "summary": "ran"},
    ]
    out = await _loop(tmp_path, steps, sm).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "submit_changes"
    assert (tmp_path / "sentinel.txt").exists(), "run_command must run in EDIT"
