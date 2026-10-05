"""Writing agent definitions from Settings (spec §10.1): Claude Code names in the file,
symlink-safe writes confined to <workspace>/.crucible/agents."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentd.subagents.agent_files import parse_agent_file
from agentd.subagents.agent_writer import (
    AgentConflictError,
    AgentFields,
    AgentInputError,
    AgentNotFoundError,
    delete_agent_file,
    render_agent_markdown,
    validate_fields,
    write_agent_file,
)


def _fields(**over) -> AgentFields:
    base = dict(name="helper", description="Helps.", persona="You help.\n\nCarefully.",
                tools=None, disallowed_tools=(), permission="default", model="inherit",
                max_turns=None, skills=())
    base.update(over)
    return AgentFields(**base)


def test_render_round_trips_through_the_parser(tmp_path: Path) -> None:
    f = _fields(tools=("read_file", "search_code", "edit", "query_graph", "mcp__github__*"),
                disallowed_tools=("run_command",), permission="acceptEdits",
                model="gpt-5", max_turns=30, skills=("tdd",))
    text = render_agent_markdown(f)
    assert "Read" in text and "Grep" in text and "Edit" in text and "mcp__github" in text
    assert "mcp__github__*" not in text
    path = tmp_path / "helper.md"
    path.write_text(text, encoding="utf-8")
    d = parse_agent_file(path)
    assert d is not None
    assert d.tools == frozenset(
        {"read_file", "search_code", "edit", "query_graph", "mcp__github__*"})
    assert d.disallowed_tools == frozenset({"run_command"})
    assert (d.permission, d.model, d.max_turns, d.skills) == ("acceptEdits", "gpt-5", 30, ("tdd",))
    assert d.persona == "You help.\n\nCarefully."
    assert d.warnings == ()


def test_all_tools_omits_the_key_and_empty_list_is_kept(tmp_path: Path) -> None:
    assert "tools:" not in render_agent_markdown(_fields(tools=None))
    path = tmp_path / "none.md"
    path.write_text(render_agent_markdown(_fields(tools=())), encoding="utf-8")
    d = parse_agent_file(path)
    assert d is not None and d.tools == frozenset()


@pytest.mark.parametrize("over", [
    {"name": "bad name"}, {"name": ""}, {"name": "x" * 65}, {"name": "trust"},
    {"description": "  "}, {"description": "d" * 1025}, {"description": "a---b"},
    {"persona": "p" * 20001}, {"permission": "bypassPermissions"},
    {"max_turns": 0}, {"max_turns": 201}, {"model": "a\nb"},
    {"tools": ("read_file\n",)},
])
def test_validation_rejects(over) -> None:
    with pytest.raises(AgentInputError):
        validate_fields(_fields(**over))


def test_write_and_delete(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    path = write_agent_file(ws, "helper", "---\nname: helper\ndescription: d\n---\nx\n")
    assert path == ws / ".crucible" / "agents" / "helper.md"
    assert path.read_text().startswith("---")
    assert [p.name for p in path.parent.iterdir()] == ["helper.md"]  # no temp left behind
    write_agent_file(ws, "helper", "---\nname: helper\ndescription: e\n---\ny\n")
    assert "description: e" in path.read_text()
    assert delete_agent_file(ws, "helper") == path
    assert not path.exists()
    with pytest.raises(AgentNotFoundError):
        delete_agent_file(ws, "helper")


def test_refuses_a_symlinked_agents_dir(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".crucible").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws / ".crucible" / "agents")
    with pytest.raises(AgentConflictError):
        write_agent_file(ws, "helper", "x")
    assert list(elsewhere.iterdir()) == []


def test_refuses_a_symlinked_crucible_dir(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws / ".crucible")
    with pytest.raises(AgentConflictError):
        write_agent_file(ws, "helper", "x")
    assert list(elsewhere.iterdir()) == []


def test_refuses_a_symlinked_target(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    agents = ws / ".crucible" / "agents"
    agents.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    os.symlink(victim, agents / "helper.md")
    with pytest.raises(AgentConflictError):
        write_agent_file(ws, "helper", "x")
    with pytest.raises(AgentConflictError):
        delete_agent_file(ws, "helper")
    assert victim.read_text() == "keep"
