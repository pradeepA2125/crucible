"""One lead proposes; the others review (spec 2026-10-07 lead proposer)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.report_fields import ReportFields
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
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


def _kickoff(teams, tid) -> None:  # type: ignore[no-untyped-def]
    teams.append_post(tid, author="main", kind="proposal", text="main plan", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})


def test_only_the_lead_proposes(tmp_path: Path) -> None:
    service, tid, _ = _team(tmp_path)
    with pytest.raises(TeamInputError, match=(
            r"Only alice \(the team's lead\) proposes\. Suggest your change with a note on "
            r"your stance, an objection with evidence, or a post\.")):
        service.propose(tid, "bob", "my plan", [])


def test_the_lead_replaces_every_open_proposal_without_a_stance_first(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    _kickoff(teams, tid)                                    # P1, no stance from alice
    p2 = service.propose(tid, "alice", "draft", [])
    assert teams.get_post(tid, 1).closed == "superseded"   # type: ignore[union-attr]
    p3 = service.propose(tid, "alice", "revised", [])
    assert teams.get_post(tid, p2.seq).closed == "superseded"  # type: ignore[union-attr]
    assert [p.proposal_id for p in service.open_proposals(tid)] == [p3.proposal_id]
    assert p3.payload["supersedes"] == [p2.proposal_id]


def test_lead_report_proposal_closes_the_open_one(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    _kickoff(teams, tid)
    check = ReportFields(service, tid, "alice", ActivationCounters())
    verdict = check({"type": "report", "thought": "t", "summary": "s", "status": "completed",
                     "proposal": {"text": "revised", "assignments": [], "supersedes": ["P9"]}},
                    False)
    assert verdict.message is None
    assert [p.author for p in service.open_proposals(tid)] == ["alice"]


def test_a_non_lead_report_proposal_is_refused(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    check = ReportFields(service, tid, "bob", ActivationCounters())
    verdict = check({"type": "report", "thought": "t", "summary": "s", "status": "completed",
                     "proposal": {"text": "mine", "assignments": []}}, False)
    assert verdict.message is not None and "Only alice" in verdict.message


def test_a_team_without_a_lead_keeps_the_old_rules(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path, lead=None)
    _kickoff(teams, tid)
    with pytest.raises(TeamInputError, match="State your stance on P1 first"):
        service.propose(tid, "bob", "mine", [])
    service.agree(tid, "bob", "P1")
    service.propose(tid, "bob", "mine", [])
    assert len(service.open_proposals(tid)) == 2           # nothing closed implicitly
