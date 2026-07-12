"""The exec-sessions teaching block appends iff start_session is present in
tool_definitions — detected from the tool name, so the engine needs no new
loader/flag parameter (the _MCP_BLOCK/_DOC_WRITE_BLOCK pattern)."""
from agentd.chat.controller_prompts import format_controller_system_prompt

_BASE = [{"name": "read_file", "description": "d", "parameters": {}}]
_SESSIONS = [{"name": "start_session", "description": "d", "parameters": {}}]


def _prompt(defs):
    return format_controller_system_prompt(
        defs, task_subsystem_enabled=False, memory_enabled=False)


def test_sessions_block_absent_without_tools():
    assert "BACKGROUND PROCESS SESSIONS" not in _prompt(_BASE)


def test_sessions_block_present_with_tools():
    text = _prompt(_BASE + _SESSIONS)
    assert "BACKGROUND PROCESS SESSIONS" in text
    block = text.split("BACKGROUND PROCESS SESSIONS")[1]
    assert "kill_session" in block
    assert "approval" in block  # teaches the gate pause is expected
    assert "list_sessions" in block  # cross-turn resume teaching
