"""Team tool sources (spec v2 §7.1–§7.3)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.tools import (
    MAIN_TOOL_NAMES,
    MEMBER_TOOL_NAMES,
    MainTeamOps,
    MainTeamToolSource,
    TeamToolSource,
    parse_create_team,
)
from agentd.teams.validation import TeamInputError


def _service(tmp_path: Path):
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=3, budget=160, created_turn_id="x", created_at=datetime.now(UTC))
    teams.create_team(team)
    for label in ("alice", "bob"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    svc = TeamService(teams, tmp_path, lambda _a: AgentInfo("explore", "reads", "running"))
    return svc, team.team_id


@pytest.mark.asyncio
async def test_member_tools_post_read_and_errors(tmp_path: Path) -> None:
    svc, tid = _service(tmp_path)
    source = TeamToolSource(svc, tid, "alice", ActivationCounters())
    assert {d.name for d in source.definitions()} == MEMBER_TOOL_NAMES
    out = await source.execute("team_post", {"text": "hello @bob"})
    assert not out.is_error and json.loads(out.output)["seq"] == 1
    bad = await source.execute("team_message", {"member": "dave", "text": "x"})
    assert bad.is_error and "alice, bob" in bad.output
    bob = TeamToolSource(svc, tid, "bob", ActivationCounters())
    read = await bob.execute("team_read", {})
    assert "hello @bob" in read.output and "<<<agent-content" in read.output


@pytest.mark.asyncio
async def test_member_propose_and_stances(tmp_path: Path) -> None:
    svc, tid = _service(tmp_path)
    alice = TeamToolSource(svc, tid, "alice", ActivationCounters())
    out = json.loads((await alice.execute("team_propose", {
        "text": "Plan", "assignments": [{"member": "bob", "part": "api", "files": []}]})).output)
    assert out["proposal_id"] == "P1"
    bob = TeamToolSource(svc, tid, "bob", ActivationCounters())
    assert not (await bob.execute("team_agree", {"proposal_id": "P1", "note": "ok"})).is_error


def _catalog():
    return dict(BUILTIN_AGENTS)


def _args(**over):
    args = {"name": "auth", "goal": "Add login",
            "members": [{"label": "alice", "agent": "explore"},
                        {"label": "bob", "agent": "general-purpose"}],
            "kickoff": {"kind": "post", "text": "Propose a design", "mentions": ["alice"]}}
    args.update(over)
    return args


def test_parse_create_team_defaults() -> None:
    req = parse_create_team(_args(), _catalog())
    assert req.max_rounds == 4 and req.budget == 160 and req.kickoff_mentions == ["alice"]
    proposal = parse_create_team(_args(kickoff={"kind": "proposal", "text": "Do X",
                                               "assignments": []}), _catalog())
    assert proposal.max_rounds == 3


@pytest.mark.parametrize("over,needle", [
    ({"members": [{"label": "alice", "agent": "explore"}]}, "2 to 6 members"),
    ({"members": [{"label": "alice", "agent": "explore"}, {"label": "alice", "agent": "explore"}]},
     "duplicate label"),
    ({"members": [{"label": "alice", "agent": "nope"}, {"label": "bob", "agent": "explore"}]},
     "unknown agent"),
    ({"members": [{"label": "Bad Label", "agent": "explore"},
                  {"label": "bob", "agent": "explore"}]}, "label"),
    ({"kickoff": {"kind": "vote", "text": "x"}}, "kickoff.kind"),
    ({"max_rounds": 7}, "max_rounds"),
    ({"budget": 5000}, "budget"),
    ({"kickoff": {"kind": "post", "text": "x", "mentions": ["dave"]}}, "unknown member"),
])
def test_parse_create_team_refusals(over, needle) -> None:
    with pytest.raises(TeamInputError, match=needle):
        parse_create_team(_args(**over), _catalog())


@pytest.mark.asyncio
async def test_main_tools_phase_refusals_and_status() -> None:
    async def create(req):
        return {"team_id": "team-1"}

    async def disband(team_id):
        return {"team_id": team_id, "phase": "DISBANDED"}

    ops = MainTeamOps(
        create=create, resolve=lambda t: "team-1",
        post=lambda tid, text, mentions: {"seq": 2},
        status=lambda tid: {"team_id": tid, "phase": "DELIBERATING"}, disband=disband,
        adopt=lambda tid, pid: {"team_id": tid, "adopted": pid})
    source = MainTeamToolSource(_catalog(), ops, first_turn_team_ids={"team-1"})
    assert {d.name for d in source.definitions()} == MAIN_TOOL_NAMES
    adopt = await source.execute("adopt_proposal", {"team": "auth", "proposal_id": "P1"})
    assert adopt.is_error and "DEADLOCKED" in adopt.output and "DELIBERATING" in adopt.output
    resume = await source.execute("resume_team", {"team": "auth"})
    assert resume.is_error and "PAUSED" in resume.output
    status = json.loads((await source.execute("team_status", {"team": "auth"})).output)
    assert "runs in the background" in status["note"]  # same turn as create_team
    created = json.loads((await source.execute("create_team", _args())).output)
    assert created["team_id"] == "team-1"


def test_create_team_note_hands_the_todo_list_to_the_team_and_ends_the_turn() -> None:
    # Live 2026-10-07: the main agent kept "reconciling" its own list for the work it had
    # just handed to the team instead of answering. Delegated items are 'blocked' (they do
    # not block the turn's end) until a milestone reports them.
    from agentd.teams.tools import BACKGROUND_NOTE
    assert "'blocked'" in BACKGROUND_NOTE and "delegated to team" in BACKGROUND_NOTE
    assert "Answer the user now" in BACKGROUND_NOTE
