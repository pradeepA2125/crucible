"""Auto approval notes travel from the approval outcome to the persisted tool pill."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agentd.chat.tool_events import trace_to_tool_events
from agentd.domain.models import AgentToolTrace, ApprovalOutcome, ToolCall, ToolResult
from agentd.tools.registry import ToolRegistry


def test_trace_to_tool_events_carries_approval_note():
    trace = AgentToolTrace(
        step_id="chat",
        calls=[ToolCall(call_id="c1", tool_name="run_command", arguments={"command": "git"})],
        results=[ToolResult(call_id="c1", tool_name="run_command", output="ok",
                            approval_note="Auto: you asked to push this branch")])
    event = trace_to_tool_events(trace, "execution")[0]
    assert event["approvalNote"] == "Auto: you asked to push this branch"


def test_trace_without_note_has_no_key():
    trace = AgentToolTrace(
        step_id="chat",
        calls=[ToolCall(call_id="c1", tool_name="run_command", arguments={})],
        results=[ToolResult(call_id="c1", tool_name="run_command", output="ok")])
    assert "approvalNote" not in trace_to_tool_events(trace, "execution")[0]


@pytest.mark.asyncio
async def test_prepare_command_stamps_note(tmp_path):
    reg = ToolRegistry(tmp_path, tmp_path, command_approval_callback=AsyncMock(
        return_value=ApprovalOutcome.allow(note="Auto: ok")))
    run = await reg.prepare_command({"command": "echo", "args": ["hi"]})
    assert callable(run)
    assert (await run()).approval_note == "Auto: ok"
