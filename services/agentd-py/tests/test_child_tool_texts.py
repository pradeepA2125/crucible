from pathlib import Path

import pytest

from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.mcp.tool_source import McpToolSource
from agentd.prompting.tagged import RenderContext
from agentd.tools.registry import ToolRegistry
from agentd.tools.sources import BuiltinToolSource


def _ctx(permission: str, shell: str = "ask") -> RenderContext:
    return RenderContext(audience="child", permission=permission, shell_policy=shell,
                         base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                         agent_id="a", agent_label="a")


def _run_command_description(reg: ToolRegistry) -> str:
    return next(d.description for d in reg.definitions() if d.name == "run_command")


def test_child_run_command_says_real_shared_workspace() -> None:
    reg = ToolRegistry(Path("/w"), Path("/w"), render_ctx=_ctx("default"))
    text = _run_command_description(reg)
    assert "real, shared workspace" in text and "shadow workspace" not in text
    assert "surfaced to the user" not in text and "approval card" in text


@pytest.mark.parametrize(("shell", "expected"), [
    ("ask", "runs only if a remembered rule allows it"),
    ("allow_all", "Commands run without approval"),
])
def test_dont_ask_run_command_follows_shell_policy(shell: str, expected: str) -> None:
    reg = ToolRegistry(Path("/w"), Path("/w"), render_ctx=_ctx("dontAsk", shell))
    text = _run_command_description(reg)
    assert expected in text


def test_builtin_source_forwards_the_render_context() -> None:
    src = BuiltinToolSource(shadow_root=Path("/w"), real_workspace_path=Path("/w"),
                            render_ctx=_ctx("default"))
    desc = next(d.description for d in src.definitions() if d.name == "run_command")
    assert "real, shared workspace" in desc


def test_child_write_todos_blocks_report_not_submit_changes() -> None:
    desc = TodoToolSource(TodoLedger(), render_ctx=_ctx("default")).definitions()[0].description
    assert "report is BLOCKED" in desc and "submit_changes" not in desc
    assert "from your task" in desc


@pytest.mark.asyncio
async def test_child_mcp_rejection_never_says_ask() -> None:
    async def deny(server: str, tool: str, args: dict[str, object]) -> bool:
        return False
    out = await McpToolSource(object(), deny, render_ctx=_ctx("default")).execute("mcp__gh__x", {})
    assert out.is_error and "or ask" not in out.output
    assert out.output.endswith("or note the blocker in your report.")
