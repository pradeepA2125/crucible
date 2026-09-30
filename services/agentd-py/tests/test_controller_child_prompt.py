"""The rendered child system prompt (spec §4.2, §4.6.1, §4.6.2, §4.6.6)."""
from pathlib import Path

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext
from agentd.skills.models import SkillManifest

EDIT_TYPES = frozenset({"tool_call", "edit", "progress", "report"})
CHILD = RenderContext(
    audience="child", permission="default",
    tools=frozenset({"read_file", "search_code", "run_command", "write_todos", "recall",
                     "read_skill"}),
    base_types=EDIT_TYPES, agent_id="agent-1", agent_label="limiter impl")
READONLY = RenderContext(
    audience="child", permission="plan", tools=frozenset({"read_file", "search_code"}),
    base_types=frozenset({"tool_call", "progress", "report"}), agent_id="agent-2",
    agent_label="survey")
DONT_ASK = RenderContext(
    audience="child", permission="dontAsk", tools=frozenset({"run_command"}),
    base_types=EDIT_TYPES, agent_id="agent-3", agent_label="fmt")
_MCP = {"name": "mcp__gh__create_issue", "description": "d", "parameters": {"type": "object"}}
_CATALOG = [SkillManifest(name="brainstorming", description="Use before creative work.",
                          body_path=Path("/x/SKILL.md"), dir=Path("/x"))]


def _child(ctx: RenderContext, **kw) -> str:
    return format_controller_system_prompt(
        kw.pop("tools", []), task_subsystem_enabled=False, render_ctx=ctx, **kw)


def test_main_is_unchanged_by_the_new_parameters() -> None:
    kw = dict(task_subsystem_enabled=True, memory_enabled=True, project_instructions="Be terse.",
              skills_catalog=_CATALOG)
    assert (format_controller_system_prompt([_MCP], **kw)
            == format_controller_system_prompt([_MCP], render_ctx=RenderContext.main(), **kw))


def test_child_identity_and_role_block_with_label() -> None:
    out = _child(CHILD, memory_enabled=False)
    assert out.startswith("You are a sub-agent carrying out one task for another agent")
    assert 'SUB-AGENT RULES — you are sub-agent "limiter impl"' in out
    assert "{label}" not in out


def test_child_append_order_role_persona_memory_instructions_skills_mcp() -> None:
    out = _child(CHILD, memory_enabled=True, project_instructions="Use tabs.",
                 skills_catalog=_CATALOG, tools=[_MCP], persona="You review code.")
    idx = [out.index(marker) for marker in (
        "SUB-AGENT RULES", "AGENT INSTRUCTIONS", "MEMORY (durable", "PROJECT INSTRUCTIONS",
        "AVAILABLE SKILLS", "EXTERNAL MCP TOOLS")]
    assert idx == sorted(idx)


def test_persona_and_instructions_render_verbatim() -> None:
    out = _child(CHILD, memory_enabled=False, persona="Keep <<main>>x<</main>> {y}",
                 project_instructions="AGENTS <<child>>z<</child>> {w}")
    assert "Keep <<main>>x<</main>> {y}" in out
    assert "AGENTS <<child>>z<</child>> {w}" in out


def test_child_memory_teaches_only_the_memory_tools_it_has() -> None:
    out = _child(CHILD, memory_enabled=True)
    assert "- recall(query)" in out and "- remember(" not in out


def test_readonly_child_cannot_be_taught_to_edit() -> None:
    out = _child(READONLY, memory_enabled=False)
    assert "YOU ARE READ-ONLY" in out
    assert "Variant — edit" not in out and "After an edit" not in out
    assert '"tool":"edit"' not in out
    assert "(progress/report)" in out


def test_dont_ask_child_is_told_commands_need_a_remembered_rule() -> None:
    out = _child(DONT_ASK, memory_enabled=False, tools=[_MCP])
    assert "runs only if a remembered rule allows it" in out
    assert "A call runs only if a remembered rule already approves it" in out
    assert "approval card" not in out.split("EXTERNAL MCP TOOLS")[1]


def test_child_skills_block_drops_the_first_action_framing() -> None:
    out = _child(CHILD, memory_enabled=False, skills_catalog=_CATALOG)
    assert "load it with read_skill before you start" in out
    assert "UNCONDITIONAL" not in out and "FIRST action" not in out
    assert "run bundled" not in out  # sanity: the scripts sentence is the run_command one
    assert "run them with\nrun_command" in out


def test_child_instructions_are_subordinate_to_sub_agent_rules() -> None:
    out = _child(CHILD, memory_enabled=False, project_instructions="Ask me first.")
    assert "They apply to you BELOW the sub-agent rules above" in out
    assert "always-on guidance from the user" not in out
