"""The MCP teaching block appends iff MCP tools are present in tool_definitions —
detected from the mcp__ prefix, so the engine needs no new loader parameter."""
from agentd.chat.controller_prompts import format_controller_system_prompt

_BASE = [{"name": "read_file", "description": "d", "parameters": {}}]
_MCP = [{"name": "mcp__github__create_issue", "description": "d", "parameters": {}}]


def _prompt(defs):
    return format_controller_system_prompt(
        defs, task_subsystem_enabled=False, memory_enabled=False)


def test_block_absent_without_mcp_tools():
    assert "EXTERNAL MCP TOOLS" not in _prompt(_BASE)


def test_block_present_with_mcp_tools():
    text = _prompt(_BASE + _MCP)
    assert "EXTERNAL MCP TOOLS" in text
    assert "approval" in text  # teaches the gate pause is expected
    # No superiority framing: the block must not rank tools against each other.
    assert "instead of" not in text.split("EXTERNAL MCP TOOLS")[1].lower()
