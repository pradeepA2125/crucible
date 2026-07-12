"""Registry + phase-availability wiring."""
import sys

import pytest

from agentd.chat.controller_loop import _decide_state_change_correction
from agentd.domain.models import CommandDecision
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource
from agentd.tools.sources import AggregatingToolRegistry

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")


def test_registry_aggregation_dispatches_session_tools(tmp_path):
    async def cb(command, args, cwd):
        return CommandDecision(approve=True)

    src = ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)
    reg = AggregatingToolRegistry([src])
    names = [d.name for d in reg.definitions()]
    assert "start_session" in names and "list_sessions" in names


def test_session_tools_allowed_in_decide_phase():
    """Sessions are deliberately available in DECIDE (spec: live smokes are
    conversational). Only run_command is in _STATE_CHANGING_TOOLS — this is
    the regression guard against someone adding session tools to it."""
    for tool in ("start_session", "write_stdin", "kill_session", "list_sessions"):
        resp = {"type": "tool_call", "tool": tool, "args": {}}
        assert _decide_state_change_correction(resp, "DECIDE") is None
    # sanity: run_command IS still barred in DECIDE
    resp = {"type": "tool_call", "tool": "run_command", "args": {}}
    assert _decide_state_change_correction(resp, "DECIDE") is not None
