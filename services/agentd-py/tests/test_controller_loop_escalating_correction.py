"""Fix: repeated malformed/rejected responses get an escalating "retries remaining"
prefix, not just the same static correction text every time.

Root cause (found live 2026-07-17): after a resumed "continue executing the plan"
turn started fresh in PLAN phase, the model tried `run_command` 5 times in a
row — each time getting the identical "run_command is not available in Plan
Mode..." rejection — never adapting to emit `propose_mode`, until the
consecutive-malformed budget (3) was exhausted and the whole turn failed. The
correction text alone wasn't enough signal that it was burning down a shrinking
retry budget.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, ControllerLoopExhausted
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
async def test_repeated_rejection_gets_escalating_retry_budget_warning(tmp_path: Path):
    bad_run_command = {"type": "tool_call", "thought": "run it",
                        "tool": "run_command", "args": {"command": "uv", "args": ["add", "x"]}}
    with pytest.raises(ControllerLoopExhausted):
        await _loop(tmp_path, [bad_run_command] * 5, ControllerPhaseSM(start="PLAN")).run(
            {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=20)


@pytest.mark.asyncio
async def test_second_rejection_history_shows_shrinking_budget(tmp_path: Path):
    bad_run_command = {"type": "tool_call", "thought": "run it",
                        "tool": "run_command", "args": {"command": "uv", "args": ["add", "x"]}}
    steps = [bad_run_command, bad_run_command,
             {"type": "answer", "thought": "give up", "answer": "done"}]
    out = await _loop(tmp_path, steps, ControllerPhaseSM(start="PLAN")).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=20)
    tool_results = [
        m.get("content") for m in out.history if m.get("role") == "tool_result"
    ]
    # First rejection: plain correction, no escalation prefix yet.
    assert "Retry" not in str(tool_results[0])
    # Second rejection: escalating prefix present, naming the shrinking budget.
    assert "Retry 2/3" in str(tool_results[1])
    assert "1 attempt(s) left" in str(tool_results[1])
