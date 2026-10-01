"""Effective permission, the AGENT type set and the child tool set (spec §5.3, §5.6)."""
from pathlib import Path

import pytest

from agentd.subagents.permissions import (
    AGENT_BASE_TYPES,
    child_allowed_types,
    child_tool_names,
    definition_allows_edit,
    effective_permission,
    follows_live_review,
    tool_matches,
)
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

_AVAILABLE = ["search_code", "read_file", "list_directory", "run_command", "write_todos",
              "recall", "remember", "read_skill", "mcp__gh__create_issue", "start_session",
              "write_stdin", "kill_session", "list_sessions", "dispatch_agents"]


@pytest.mark.parametrize("own,dispatcher,expected", [
    ("default", None, "default"), ("acceptEdits", None, "acceptEdits"),
    ("dontAsk", "default", "dontAsk"), ("default", "plan", "plan"),
    ("acceptEdits", "plan", "plan"), ("plan", "default", "plan"), ("plan", None, "plan"),
])
def test_read_only_ness_propagates_nothing_else_does(own, dispatcher, expected) -> None:
    assert effective_permission(own, dispatcher) == expected


def test_type_sets() -> None:
    assert AGENT_BASE_TYPES == ("tool_call", "edit", "progress", "report")
    assert child_allowed_types("default") == AGENT_BASE_TYPES
    assert child_allowed_types("plan") == ("tool_call", "progress", "report")


def test_a_default_child_gets_everything_but_sessions_and_remember() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=None, permission="default",
                             may_dispatch=True)
    assert names == frozenset({"search_code", "read_file", "list_directory", "run_command",
                               "write_todos", "recall", "read_skill", "mcp__gh__create_issue",
                               "dispatch_agents"})


def test_a_read_only_child_loses_commands_and_mcp() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=None, permission="plan",
                             may_dispatch=False)
    assert "run_command" not in names and not any(n.startswith("mcp__") for n in names)
    assert "dispatch_agents" not in names


def test_a_definition_narrows_and_never_widens() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=frozenset({"read_file", "query_graph"}),
                             permission="plan", may_dispatch=True)
    assert names == frozenset({"read_file"})  # query_graph was never available


def test_only_default_follows_the_live_review_toggle() -> None:
    assert follows_live_review("default")
    assert not any(follows_live_review(p) for p in ("acceptEdits", "dontAsk", "plan"))


@pytest.mark.asyncio
async def test_the_registry_allow_list(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x = 1\n")
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)],
        allowed_tools=frozenset({"read_file"}))
    assert [d.name for d in reg.definitions()] == ["read_file"]
    assert not (await reg.execute("read_file", {"path": "f.py"})).is_error
    refused = await reg.execute("run_command", {"command": "ls"})
    assert refused.is_error and refused.output == "Error: unknown tool 'run_command'"


def test_tool_matches_exact_and_server_wildcard() -> None:
    patterns = frozenset({"read_file", "mcp__gh__*"})
    assert tool_matches("read_file", patterns)
    assert tool_matches("mcp__gh__create_issue", patterns)
    assert not tool_matches("mcp__ghx__create_issue", patterns)  # the prefix ends at "__"
    assert not tool_matches("search_code", patterns)


def test_definition_tools_accept_mcp_wildcards() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=frozenset({"read_file", "mcp__gh__*"}),
                             permission="default", may_dispatch=False)
    assert names == frozenset({"read_file", "mcp__gh__create_issue"})


def test_disallowed_is_removed_before_tools() -> None:
    names = child_tool_names(
        _AVAILABLE, definition_tools=None, permission="default", may_dispatch=True,
        definition_disallowed=frozenset({"run_command", "mcp__gh__*"}))
    assert "run_command" not in names and "mcp__gh__create_issue" not in names
    assert {"read_file", "dispatch_agents"} <= names


@pytest.mark.parametrize("tools,disallowed,expected", [
    (None, frozenset(), True),
    (frozenset({"read_file", "edit"}), frozenset(), True),
    (frozenset({"read_file"}), frozenset(), False),     # tools listed without Edit/Write
    (None, frozenset({"edit"}), False),                 # disallowedTools: Write
])
def test_definition_allows_edit(tools, disallowed, expected) -> None:
    assert definition_allows_edit(tools, disallowed) is expected


def test_no_edit_type_when_the_definition_cannot_edit() -> None:
    assert child_allowed_types("default", can_edit=False) == ("tool_call", "progress", "report")
    assert child_allowed_types("default") == AGENT_BASE_TYPES   # unchanged default
