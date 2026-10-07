"""Members see the board as it stood when their round started (spec 2026-10-07 §3)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.validation import TeamInputError
from tests.test_team_coordinator import _setup as coordinator_setup


def _setup(tmp_path: Path):  # type: ignore[no-untyped-def]
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=3, budget=100, created_turn_id="u", created_at=datetime.now(UTC))
    teams.create_team(team)
    for label in ("alice", "bob"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    teams.append_post(team.team_id, author="main", kind="proposal", text="kickoff", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    teams.update_team(team.team_id, round_cutoff_seq=1)   # round 1 started after the kickoff
    service = TeamService(teams, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    service.agree(team.team_id, "alice", "P1")            # seq 2
    proposal = service.propose(team.team_id, "alice", "Plan", [])   # seq 3, round 1
    return service, team.team_id, teams, proposal


def test_new_columns_round_trip(tmp_path: Path) -> None:
    _, tid, teams, _ = _setup(tmp_path)
    teams.update_team(tid, vote_between="P4,P7")
    team = teams.get_team(tid)
    assert team is not None and team.round_cutoff_seq == 1 and team.vote_ids == ["P4", "P7"]
    teams.update_team(tid, vote_between="")
    assert teams.get_team(tid).vote_ids == []  # type: ignore[union-attr]


def test_a_stance_on_a_proposal_from_this_round_is_refused(tmp_path: Path) -> None:
    service, tid, teams, proposal = _setup(tmp_path)
    with pytest.raises(TeamInputError, match="P3 was posted this round; you can respond to it "
                                             "next round"):
        service.agree(tid, "bob", proposal.proposal_id)
    teams.update_team(tid, round=2, round_cutoff_seq=3)
    assert service.agree(tid, "bob", proposal.proposal_id).ref_id == "P3"


def test_read_and_status_stop_at_the_round_cutoff(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    service.post(tid, "alice", "look @bob")                           # seq 4
    assert [p.seq for p in service.read(tid, "bob")] == [1]
    assert [p.seq for p in service.read(tid, "alice")] == [1, 2, 3, 4]   # own posts always
    bob = service.status_text(tid, "bob")
    assert "P3" not in bob and "0 mentions" in bob
    assert "- P3 by alice — your stance: yours" in service.status_text(tid, "alice")
    teams.update_team(tid, phase="IMPLEMENTING")                      # live delivery again
    assert [p.seq for p in service.read(tid, "bob")] == [1, 2, 3, 4]


def test_render_delta_defaults_to_the_persisted_cutoff(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    _, top, handed = service.render_delta_posts(tid, "bob")
    assert top == 1 and [p.seq for p in handed] == [1]


@pytest.mark.asyncio
async def test_the_coordinator_persists_the_cutoff(tmp_path: Path) -> None:
    store, _svc, _host, coord, _ = coordinator_setup(tmp_path)
    coord.kickoff("proposal", [])
    assert store.get_team("team-1").round_cutoff_seq == 1  # type: ignore[union-attr]
