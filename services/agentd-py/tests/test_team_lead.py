"""One lead proposes; the others review (spec 2026-10-07 lead proposer)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.tools import parse_create_team
from agentd.teams.validation import TeamInputError


def _args(**over):  # type: ignore[no-untyped-def]
    args = {"name": "auth", "goal": "Add login", "lead": "alice",
            "members": [{"label": "alice", "agent": "general-purpose"},
                        {"label": "bob", "agent": "explore"},
                        {"label": "carol", "agent": "explore"}],
            "kickoff": {"kind": "post", "text": "Plan it", "mentions": ["bob"]}}
    args.update(over)
    return args


def test_create_team_requires_a_known_lead() -> None:
    assert parse_create_team(_args(), dict(BUILTIN_AGENTS)).lead == "alice"
    for bad in (None, "dave"):
        args = _args(lead=bad)
        with pytest.raises(TeamInputError,
                           match="lead must be one of the members: alice, bob, carol"):
            parse_create_team(args, dict(BUILTIN_AGENTS))


def test_post_kickoff_mentions_gain_the_lead() -> None:
    assert parse_create_team(_args(), dict(BUILTIN_AGENTS)).kickoff_mentions == ["bob", "alice"]
    everyone = _args(kickoff={"kind": "post", "text": "Plan it", "mentions": []})
    assert parse_create_team(everyone, dict(BUILTIN_AGENTS)).kickoff_mentions == []


def _team(tmp_path: Path, lead: str | None = "alice"):  # type: ignore[no-untyped-def]
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=3, budget=100, created_turn_id="u",
                      created_at=datetime.now(UTC), lead=lead)
    teams.create_team(team)
    for label in ("alice", "bob", "carol"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    service = TeamService(teams, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    return service, team.team_id, teams


def test_the_lead_round_trips_and_shows_in_the_summary(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    assert teams.get_team(tid).lead == "alice"  # type: ignore[union-attr]
    assert service.summary(tid)["lead"] == "alice"
