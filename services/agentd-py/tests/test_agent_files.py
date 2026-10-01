"""Agent definition files: fields, parse and Claude Code mapping (spec §5.4, §5.6, §9)."""
from agentd.skills.tool_source import cap_skill_body
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition


def test_new_fields_default_so_existing_definitions_are_unchanged() -> None:
    d = AgentDefinition(name="x", description="d", permission="default", tools=None, persona="")
    assert (d.disallowed_tools, d.skills, d.source) == (frozenset(), (), "built-in")
    assert all(b.source == "built-in" for b in BUILTIN_AGENTS.values())


def test_cap_skill_body() -> None:
    assert cap_skill_body("s", "short", cap=10) == "short"
    assert cap_skill_body("s", "x" * 12, cap=10) == (
        "x" * 10 + "\n\n[... skill 's' truncated at 10 chars ...]")
