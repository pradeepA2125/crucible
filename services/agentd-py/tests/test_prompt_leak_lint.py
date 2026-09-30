"""Spec §4.7.5 (source-level lint) + §15 (rendered, per permission) prompt-leak guards."""
import re
from pathlib import Path

import pytest

from agentd.chat import controller_loop as cl
from agentd.chat import controller_prompts as cp
from agentd.chat import todo_source
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.mcp import tool_source as mcp_source
from agentd.prompting.tagged import RenderContext, render_prompt
from agentd.reasoning import react_common
from agentd.skills.models import SkillManifest
from agentd.tools import registry
from agentd.tools.registry import ToolRegistry

TEMPLATES = {
    **{n: getattr(cp, n) for n in (
        "CONTROLLER_SYSTEM_PROMPT", "_PROPOSE_MODE_MODES_ENABLED", "_PROPOSE_MODE_MODES_DISABLED",
        "_MEMORY_BLOCK", "_INSTRUCTIONS_BLOCK_TEMPLATE", "_MCP_BLOCK", "_SESSIONS_BLOCK",
        "_SKILLS_BLOCK_HEADER", "_AGENT_ROLE_BLOCK")},
    "reserved": cl._RESERVED_TOOL_NAME_TEMPLATE,
    "progress_repeat": cl._PROGRESS_REPEAT_TEMPLATE,
    "progress_dedup": cl._PROGRESS_DEDUP_TEMPLATE,
    "empty_edit": cl._EMPTY_EDIT_REDIRECT_TEMPLATE,
    "malformed": react_common._MALFORMED_TEMPLATE,
    "run_command": registry._RUN_COMMAND_DESCRIPTION,
    "write_todos": todo_source._WRITE_TODOS_DESCRIPTION,
    "mcp_rejected": mcp_source._MCP_REJECTED_TEMPLATE,
}
# Main-only markers (spec §4.7.5). `clarify` only in identifier forms.
_MARKERS = [r"submit_changes", r"propose_mode", r"Plan Mode", r"retrieval seed",
            r'"type":"answer"', r"type='answer'", r"ask what they want", r"or ask\b",
            r"type='clarify'", r'"clarify"', r"`clarify`", r"clarify variant", r"/clarify/"]
_NEVER_CHILD = re.compile(r"^(main|type:(answer|clarify|propose_mode|submit_changes))$")
_MARKER_RE = re.compile(r"<<(/?)([a-z_]+(?::[A-Za-z0-9_]+)?)>>")


def _inside_a_marker(template: str, pos: int) -> bool:
    # A tag's own name (e.g. <<type:propose_mode>>) is not prompt wording.
    return any(m.start() <= pos < m.end() for m in _MARKER_RE.finditer(template))


def _enclosing_tags(template: str, pos: int) -> list[str]:
    stack: list[str] = []
    for m in _MARKER_RE.finditer(template[:pos]):
        if m.group(1):
            stack.pop()
        else:
            stack.append(m.group(2))
    return stack


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_main_only_wording_is_inside_a_never_child_region(name: str) -> None:
    template = TEMPLATES[name]
    offenders = []
    for marker in _MARKERS:
        for m in re.finditer(marker, template):
            if _inside_a_marker(template, m.start()):
                continue
            if not any(_NEVER_CHILD.match(tag) for tag in _enclosing_tags(template, m.start())):
                line = template.count("\n", 0, m.start()) + 1
                offenders.append(f"{name}:{line}: {m.group(0)!r}")
    assert not offenders, "untagged main-only wording:\n" + "\n".join(offenders)


_EDIT = frozenset({"tool_call", "edit", "progress", "report"})
_READ = frozenset({"tool_call", "progress", "report"})
_ALL_TOOLS = frozenset({"run_command", "write_todos", "recall", "read_skill", "read_file"})
CHILDREN = {
    "default": RenderContext(audience="child", permission="default", tools=_ALL_TOOLS,
                             base_types=_EDIT, agent_id="a", agent_label="a"),
    "acceptEdits": RenderContext(audience="child", permission="acceptEdits", tools=_ALL_TOOLS,
                                 base_types=_EDIT, agent_id="b", agent_label="b"),
    "dontAsk": RenderContext(audience="child", permission="dontAsk", tools=_ALL_TOOLS,
                             base_types=_EDIT, agent_id="c", agent_label="c"),
    "plan": RenderContext(audience="child", permission="plan",
                          tools=frozenset({"read_file", "recall"}),
                          base_types=_READ, agent_id="d", agent_label="d"),
}
_RENDERED_BANNED = ["submit_changes", '"type":"answer"', "type='answer'", "type='clarify'",
                    "propose_mode", "Plan Mode", "retrieval seed", "ask what they want", "or ask."]
_MCP = {"name": "mcp__gh__x", "description": "d", "parameters": {"type": "object"}}
_CATALOG = [SkillManifest(name="s", description="d", body_path=Path("/x"), dir=Path("/x"))]


@pytest.mark.parametrize("perm", sorted(CHILDREN))
def test_everything_a_child_is_shown_is_leak_free(perm: str) -> None:
    ctx = CHILDREN[perm]
    shown = [
        format_controller_system_prompt([_MCP], task_subsystem_enabled=True, memory_enabled=True,
                                        project_instructions="x", skills_catalog=_CATALOG,
                                        render_ctx=ctx, persona="p"),
        *(d.description
          for d in ToolRegistry(Path("/w"), Path("/w"), render_ctx=ctx).definitions()),
        todo_source._write_todos_def(ctx).description,
        cl._reserved_tool_name_correction({"tool": "edit"}, "tool_call", ctx) or "",
        cl._progress_repeat_correction({"note": "n"}, "progress", True, ctx) or "",
        cl._progress_dedup_correction({"note": "n"}, "progress", {"n"}, ctx) or "",
        cl._empty_edit_redirect(ctx),
        react_common.malformed_correction(ctx, sorted(ctx.base_types)),
        render_prompt(mcp_source._MCP_REJECTED_TEMPLATE, ctx),
    ]
    leaks = [(b, s[:120]) for s in shown for b in _RENDERED_BANNED if b in s]
    assert not leaks, leaks


@pytest.mark.parametrize("perm", sorted(CHILDREN))
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_child_renders_add_no_extra_blank_lines(perm: str, name: str) -> None:
    template = TEMPLATES[name]
    child = render_prompt(template, CHILDREN[perm])
    main = render_prompt(template, RenderContext.main())
    assert child.count("\n\n\n") <= main.count("\n\n\n")
