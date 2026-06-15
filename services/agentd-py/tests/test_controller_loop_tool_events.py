"""The controller loop must stream live progress events (smoke-found gap, 2026-06-15):
thinking status before each model call + tool_call/tool_result around each tool
execution, so the chat UI renders the thinking pane and tool pills during a turn
(the frontend already maps these SSE events; the loop just never emitted them).
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _drain(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_loop_broadcasts_thinking_and_tool_pills(tmp_path: Path):
    (tmp_path / "f.py").write_text("def foo():\n    return 1\n")
    eng = ScriptedReasoningEngine(None, [], controller_step_responses=[
        {"type": "tool_call", "thought": "look at f", "tool": "read_file",
         "args": {"path": "f.py"}},
        {"type": "answer", "thought": "done", "answer": "foo returns 1"},
    ])
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    bc = EventBroadcaster()
    q = bc.subscribe("c1")
    loop = ControllerLoop(
        eng, reg, bc, channel_id="c1", phase_sm=ControllerPhaseSM())
    await loop.run({"goal": "what does foo do", "workspace_path": str(tmp_path)}, max_iters=8)

    events = _drain(q)
    by_type: dict[str, list[dict]] = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e.get("payload", {}))

    # Live thinking status emitted at least once (so the UI isn't blank during the call).
    assert "chat_agent_thinking" in by_type
    # The tool call rendered as a pill with tool name + thought, then its result.
    assert "tool_call" in by_type, f"no tool_call broadcast; got {list(by_type)}"
    assert "tool_result" in by_type, f"no tool_result broadcast; got {list(by_type)}"
    call = by_type["tool_call"][0]
    assert call["tool"] == "read_file"
    assert call["thought"] == "look at f"
    assert call["args"] == {"path": "f.py"}
    result = by_type["tool_result"][0]
    assert "return 1" in result["output"]
    assert result["is_error"] is False
