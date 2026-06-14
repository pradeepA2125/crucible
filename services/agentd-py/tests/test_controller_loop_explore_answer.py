from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


@pytest.mark.asyncio
async def test_loop_explores_then_answers(tmp_path: Path):
    (tmp_path / "f.py").write_text("def foo():\n    return 1\n")
    eng = ScriptedReasoningEngine(None, [], controller_step_responses=[
        {"type": "tool_call", "thought": "look", "tool": "read_file", "args": {"path": "f.py"}},
        {"type": "answer", "thought": "done", "answer": "foo returns 1"},
    ])
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    loop = ControllerLoop(
        eng, reg, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM())
    outcome = await loop.run(
        {"goal": "what does foo do", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"
    assert "foo returns 1" in outcome.text
