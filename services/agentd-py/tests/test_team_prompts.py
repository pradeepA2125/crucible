"""Team prompt text (spec v2 §7.5) and the status tail (§7.6)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentd.chat.controller_loop import _tool_name_as_type_correction
from agentd.chat.controller_prompts import (
    TEAM_FRAMING,
    build_controller_step_payload,
    format_controller_system_prompt,
)
from agentd.chat.storage import ChatThreadStore
from agentd.prompting.tagged import PromptTemplateError, RenderContext, render_prompt, tagged
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.tools import MainTeamOps, MainTeamToolSource


def _ctx(team_brief: str = "") -> RenderContext:
    agent = AgentContext(
        agent_id="agent-1", name="general-purpose", label="alice", depth=1,
        parent_agent_id=None, permission="default",
        allowed_types=("tool_call", "edit", "progress", "report"), persona="", max_iters=10)
    return RenderContext.for_agent(
        agent, tools=frozenset({"read_file", "team_post", "team_agree"}),
        shell_policy="ask", team_brief=team_brief)


def test_team_tag_renders_only_for_members() -> None:
    template = tagged("t", "a<<team>>B<</team>>c")
    assert render_prompt(template, _ctx("goal: x")) == "aBc"
    assert render_prompt(template, _ctx()) == "ac"
    assert render_prompt(template, RenderContext.main()) == "ac"


def test_team_tag_takes_no_argument() -> None:
    with pytest.raises(PromptTemplateError, match="unknown tag"):
        tagged("t", "<<team:x>>a<</team:x>>")


def test_member_prompt_has_framing_brief_and_examples_after_persona() -> None:
    text = format_controller_system_prompt(
        [{"name": "team_post"}], task_subsystem_enabled=False, memory_enabled=False,
        render_ctx=_ctx("Team 'auth'. Goal: add login.\nRoster:\n- alice (you)\n- bob"),
        persona="You review code.")
    assert TEAM_FRAMING in text
    assert "Goal: add login." in text
    assert text.index("You review code.") < text.index(TEAM_FRAMING)
    assert '"tool":"team_agree"' in text and '"tool":"team_object"' in text
    assert "awaiting_peer" in text


def test_non_team_child_and_main_never_see_member_text() -> None:
    child = format_controller_system_prompt(
        [{"name": "read_file"}], task_subsystem_enabled=False, memory_enabled=False,
        render_ctx=_ctx())
    main = format_controller_system_prompt(
        [{"name": "read_file"}], task_subsystem_enabled=False, memory_enabled=False)
    for text in (child, main):
        assert TEAM_FRAMING not in text
        assert "team_agree" not in text and "TEAM" not in text


async def _never(*a, **k):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


def test_main_teams_block_keyed_off_create_team() -> None:
    tools = [d.model_dump() for d in MainTeamToolSource(BUILTIN_AGENTS, MainTeamOps(
        create=_never, resolve=lambda r: r, post=lambda *a: {}, status=lambda t: {},
        disband=_never), first_turn_team_ids=set()).definitions()]
    text = format_controller_system_prompt(tools, task_subsystem_enabled=False,
                                           memory_enabled=False)
    assert "TEAMS (create_team" in text
    assert '"kind":"proposal"' in text and '"kind":"post"' in text
    assert "60–90 requests per member" in text
    assert TEAM_FRAMING not in text


def test_status_tail_rides_the_payload_tail() -> None:
    payload = build_controller_step_payload(
        {"goal": "g", "team_status": "team 'auth': phase DELIBERATING"},
        [{"role": "user", "content": "x"}], [], phase="AGENT")
    keys = list(payload)
    assert payload["team_status"] == "team 'auth': phase DELIBERATING"
    assert keys.index("team_status") > keys.index("conversation_history")
    bare = build_controller_step_payload({"goal": "g", "team_status": ""}, [], [], phase="AGENT")
    assert "team_status" not in bare


def test_tool_name_as_type_gets_the_wrapper() -> None:
    text = _tool_name_as_type_correction("team_agree", {"team_agree", "read_file"})
    assert text is not None
    assert '"type":"tool_call"' in text and '"tool":"team_agree"' in text
    assert _tool_name_as_type_correction("team_agree", {"read_file"}) is None
    assert _tool_name_as_type_correction("read_file", {"read_file"}) is None  # not a team tool


def test_brief_names_goal_and_roster(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    from datetime import UTC, datetime
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id="t1", name="auth", goal="Add login", max_rounds=3,
        round_started_at=datetime.now(UTC), approval_gate=False, budget=160,
        created_turn_id="turn", checkpoint_seq=0, created_at=datetime.now(UTC)))
    for label in ("alice", "bob"):
        store.teams.add_member(TeamMember(team_id="team-1", agent_id=f"a-{label}", label=label))
    svc = TeamService(store.teams, tmp_path,
                      lambda aid: AgentInfo("reviewer", "Reviews diffs", "idle"),
                      on_post=lambda team, post: None)
    brief = svc.brief("team-1", "alice")
    assert "Team 'auth'" in brief and "Goal: Add login" in brief
    assert "- alice (you): reviewer — Reviews diffs" in brief
    assert "- bob: reviewer — Reviews diffs" in brief
    assert "[idle]" not in brief   # status changes; it belongs in the tail, not here


@pytest.mark.parametrize("variant", [{}, {"tight": True}, {"anyof": True}])
def test_member_report_schema_allows_awaiting_peer(variant: dict[str, bool]) -> None:
    from agentd.chat.controller_prompts import controller_response_schema
    lone = json.dumps(controller_response_schema(
        phase="AGENT", allowed_types=["tool_call", "report"], **variant))
    member = json.dumps(controller_response_schema(
        phase="AGENT", allowed_types=["tool_call", "report"], team_member=True, **variant))
    assert "awaiting_peer" not in lone
    assert "awaiting_peer" in member
