"""Agent definition files: fields, parse and Claude Code mapping (spec §5.4, §5.6, §9)."""
import logging
from pathlib import Path

import pytest

from agentd.skills.tool_source import cap_skill_body
from agentd.subagents.agent_files import parse_agent_file
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition


def test_new_fields_default_so_existing_definitions_are_unchanged() -> None:
    d = AgentDefinition(name="x", description="d", permission="default", tools=None, persona="")
    assert (d.disallowed_tools, d.skills, d.source) == (frozenset(), (), "built-in")
    assert all(b.source == "built-in" for b in BUILTIN_AGENTS.values())


def test_cap_skill_body() -> None:
    assert cap_skill_body("s", "short", cap=10) == "short"
    assert cap_skill_body("s", "x" * 12, cap=10) == (
        "x" * 10 + "\n\n[... skill 's' truncated at 10 chars ...]")


def _write(tmp_path: Path, text: str, name: str = "a.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_file_inherits_everything(tmp_path: Path) -> None:
    d = parse_agent_file(_write(
        tmp_path, "---\nname: reviewer\ndescription: Reviews diffs\n---\nBe strict.\n"))
    assert d is not None
    assert (d.name, d.description, d.persona) == ("reviewer", "Reviews diffs", "Be strict.")
    assert (d.permission, d.tools, d.disallowed_tools, d.model, d.max_turns, d.skills) == (
        "default", None, frozenset(), "inherit", None, ())
    assert d.source.endswith("a.md")


def test_claude_code_tools_map_to_crucible_names(tmp_path: Path) -> None:
    d = parse_agent_file(_write(tmp_path, (
        "---\nname: cc\ndescription: d\n"
        "tools: Read, Grep, Glob, LS, Bash, Edit, Write, MultiEdit, NotebookEdit, Agent, Task, "
        "TodoWrite, Skill, query_graph, mcp__github, mcp__jira__*, mcp__gh__create_issue\n---\n")))
    assert d is not None and d.tools == frozenset({
        "read_file", "search_code", "list_directory", "run_command", "edit", "dispatch_agents",
        "write_todos", "read_skill", "query_graph", "mcp__github__*", "mcp__jira__*",
        "mcp__gh__create_issue"})


def test_yaml_list_fields_and_disallowed(tmp_path: Path) -> None:
    d = parse_agent_file(_write(tmp_path, (
        "---\nname: l\ndescription: d\ntools: [Read, Bash]\ndisallowedTools: Write\n"
        "skills:\n  - tdd\n  - debugging\nmaxTurns: 12\n---\n")))
    assert d is not None
    assert d.tools == frozenset({"read_file", "run_command"})
    assert d.disallowed_tools == frozenset({"edit"})
    assert (d.skills, d.max_turns) == (("tdd", "debugging"), 12)


def test_web_tools_have_no_mapping(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        d = parse_agent_file(_write(
            tmp_path, "---\nname: w\ndescription: d\ntools: Read, WebFetch, WebSearch\n---\n"))
    assert d is not None and d.tools == frozenset({"read_file"})
    assert "WebFetch" in caplog.text and "WebSearch" in caplog.text


@pytest.mark.parametrize("raw,expected", [
    ("default", "default"), ("acceptEdits", "acceptEdits"), ("dontAsk", "dontAsk"),
    ("plan", "plan"), ("bypassPermissions", "default"), ("yolo", "default")])
def test_permission_mode(tmp_path: Path, raw: str, expected: str) -> None:
    d = parse_agent_file(_write(
        tmp_path, f"---\nname: p\ndescription: d\npermissionMode: {raw}\n---\n"))
    assert d is not None and d.permission == expected


@pytest.mark.parametrize("raw,expected", [
    ("inherit", "inherit"), ("sonnet", "inherit"), ("opus", "inherit"), ("haiku", "inherit"),
    ("fable", "inherit"), ("gpt-5.1", "gpt-5.1"), ("nvidia/nemotron-3-ultra-550b-a55b",
                                                   "nvidia/nemotron-3-ultra-550b-a55b")])
def test_model_aliases_inherit(tmp_path: Path, raw: str, expected: str) -> None:
    d = parse_agent_file(_write(tmp_path, f"---\nname: m\ndescription: d\nmodel: {raw}\n---\n"))
    assert d is not None and d.model == expected


def test_description_is_one_capped_line(tmp_path: Path) -> None:
    long = "word " * 400
    d = parse_agent_file(_write(
        tmp_path, f"---\nname: x\ndescription: |\n  first\n  second\n  {long}\n---\n"))
    assert d is not None
    assert d.description.startswith("first second word") and "\n" not in d.description
    assert len(d.description) == 1024


@pytest.mark.parametrize("text", [
    "no frontmatter at all",
    "---\nname: x\n",                                    # unterminated
    "---\n: [broken\n---\n",                             # YAML error
    "---\n- a list\n---\n",                              # not a mapping
    "---\ndescription: d\n---\n",                        # no name
    "---\nname: x\n---\n",                               # no description
    "---\nname: has space\ndescription: d\n---\n",       # unsafe name
    "---\nname: x\ndescription: d\ntools: 7\n---\n",     # wrong field type is skipped, not fatal
])
def test_malformed_files_are_skipped_or_degraded(tmp_path: Path, text: str) -> None:
    d = parse_agent_file(_write(tmp_path, text))
    if "tools: 7" in text:
        assert d is not None and d.tools is None  # the field is ignored with a warning
    else:
        assert d is None


def test_bad_max_turns_is_ignored(tmp_path: Path) -> None:
    for raw in ("0", "-3", "lots"):
        d = parse_agent_file(_write(
            tmp_path, f"---\nname: t\ndescription: d\nmaxTurns: {raw}\n---\n"))
        assert d is not None and d.max_turns is None


def test_unreadable_file_returns_none(tmp_path: Path) -> None:
    assert parse_agent_file(tmp_path / "missing.md") is None
