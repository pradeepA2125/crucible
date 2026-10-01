"""A read-only child naming a command/MCP tool is corrected, not executed (spec §5.6)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, _permission_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

READONLY = RenderContext(audience="child", permission="plan", tools=frozenset({"read_file"}),
                         base_types=frozenset({"tool_call", "progress", "report"}),
                         agent_id="agent-1", agent_label="survey")
DEFAULT = RenderContext(audience="child", permission="default", tools=frozenset({"run_command"}),
                        base_types=frozenset({"tool_call", "edit", "progress", "report"}))


def test_only_read_only_children_are_corrected() -> None:
    call = {"type": "tool_call", "tool": "run_command", "args": {"command": "ls"}}
    mcp = {"type": "tool_call", "tool": "mcp__gh__create_issue", "args": {}}
    assert _permission_correction(call, "tool_call", READONLY) == (
        "You are read-only for this task: `run_command` isn't available to you. Investigate "
        "with the read tools and put your findings in `report`.")
    assert _permission_correction(mcp, "tool_call", READONLY) is not None
    assert _permission_correction(call, "tool_call", DEFAULT) is None
    assert _permission_correction(call, "tool_call", RenderContext.main()) is None
    read = {"type": "tool_call", "tool": "read_file", "args": {"path": "f.py"}}
    assert _permission_correction(read, "tool_call", READONLY) is None


@pytest.mark.asyncio
async def test_the_correction_is_in_the_chain(tmp_path: Path) -> None:
    steps = [
        {"type": "tool_call", "thought": "t", "tool": "run_command", "args": {"command": "ls"}},
        {"type": "submit_changes", "thought": "d", "summary": "ok"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([BuiltinToolSource(shadow_root=tmp_path,
                                                   real_workspace_path=tmp_path)]),
        EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        render_ctx=READONLY)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    contents = [str(m.get("content", "")) for m in out.history or []]
    assert any("You are read-only for this task: `run_command`" in c for c in contents)
