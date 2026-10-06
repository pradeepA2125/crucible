"""Team storage (spec §7.4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.providers.usage import Usage
from agentd.teams.models import TeamMember, TeamRecord, new_team_id


def _store(tmp_path: Path) -> ChatThreadStore:
    return ChatThreadStore(tmp_path / "chat.sqlite3")


def _team(thread_id: str = "t1", **over) -> TeamRecord:
    base = dict(team_id=new_team_id(), thread_id=thread_id, name="auth", goal="Add login",
                max_rounds=3, approval_gate=False, budget=160, created_turn_id="turn1",
                created_at=datetime.now(UTC))
    base.update(over)
    return TeamRecord(**base)


def test_team_and_members_round_trip(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-a", label="alice"))
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-b", label="bob"))
    got = teams.get_team(team.team_id)
    assert got is not None and got.phase == "DELIBERATING" and got.round == 1
    assert [m.label for m in teams.members(team.team_id)] == ["alice", "bob"]
    assert teams.member_for_agent("agent-b").label == "bob"
    assert teams.count_live_teams("t1") == 1
    teams.update_team(team.team_id, phase="DISBANDED", end_reason="user")
    assert teams.count_live_teams("t1") == 0


def test_post_seq_is_per_team_and_monotonic(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    a, b = _team(), _team()
    teams.create_team(a)
    teams.create_team(b)
    p1 = teams.append_post(a.team_id, author="main", kind="proposal", text="kickoff", round=0)
    p2 = teams.append_post(a.team_id, author="alice", kind="post", text="hi")
    q1 = teams.append_post(b.team_id, author="main", kind="post", text="other team")
    assert (p1.seq, p2.seq, q1.seq) == (1, 2, 1)
    assert p1.proposal_id == "P1"


def test_direct_messages_are_private(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.append_post(team.team_id, author="alice", kind="post", text="board")
    teams.append_post(team.team_id, author="alice", kind="post", text="psst", recipient="bob")
    seen = lambda label: [p.text for p in teams.posts(team.team_id, viewer=label)]  # noqa: E731
    assert seen("bob") == ["board", "psst"]
    assert seen("alice") == ["board", "psst"]
    assert seen("carol") == ["board"]
    assert [p.text for p in teams.posts(team.team_id)] == ["board", "psst"]  # UI/main: all


def test_since_seq_and_close(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    p = teams.append_post(team.team_id, author="main", kind="proposal", text="P",
                          payload={"assignments": []})
    teams.append_post(team.team_id, author="bob", kind="agree", text="", ref_id="P1")
    assert [x.seq for x in teams.posts(team.team_id, since_seq=1)] == [2]
    teams.close_proposal(team.team_id, p.seq, "withdrawn")
    assert teams.get_post(team.team_id, 1).closed == "withdrawn"


def test_delivered_seq_never_lowers_and_wakes_count(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-a", label="alice"))
    teams.set_delivered_seq(team.team_id, "alice", 5)
    teams.set_delivered_seq(team.team_id, "alice", 3)
    assert teams.member(team.team_id, "alice").delivered_seq == 5
    assert [teams.bump_wakes(team.team_id, "alice") for _ in range(3)] == [1, 2, 3]


def test_restart_fails_live_teams(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    live, done = _team(), _team(phase="DONE")
    teams.create_team(live)
    teams.create_team(done)
    assert teams.fail_live_teams("backend restarted") == [live.team_id]
    assert teams.get_team(live.team_id).phase == "FAILED"
    assert teams.get_team(done.team_id).phase == "DONE"


def test_flag_default_off_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentd.chat.controller_factory import is_teams_enabled
    monkeypatch.delenv("CRUCIBLE_TEAMS_ENABLED", raising=False)
    assert is_teams_enabled() is False
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    assert is_teams_enabled() is True


def test_set_in_quorum_and_live_team_names(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-a", label="alice"))
    teams.set_in_quorum(team.team_id, "alice", False)
    assert teams.member(team.team_id, "alice").in_quorum is False
    assert teams.live_team_names("t1") == ["auth"]
    teams.update_team(team.team_id, phase="DONE")
    assert teams.live_team_names("t1") == []


def test_assignment_wakes_and_usage(tmp_path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="a1", label="alice"))
    teams.set_assignment(team.team_id, "alice", {"member": "alice", "part": "api",
                                                 "files": ["a.py"]})
    teams.bump_wakes(team.team_id, "alice")
    teams.set_assignment_done(team.team_id, "alice")
    member = teams.member(team.team_id, "alice")
    assert member.assignment["files"] == ["a.py"] and member.assignment_done
    teams.reset_wakes(team.team_id)
    assert teams.member(team.team_id, "alice").wakes_this_phase == 0
    teams.set_assignment(team.team_id, "alice", None)
    assert teams.member(team.team_id, "alice").assignment_done is False
    teams.add_usage(team.team_id, Usage(requests=3, prompt_tokens=10, completion_tokens=2))
    after = teams.get_team(team.team_id)
    assert (after.requests, after.prompt_tokens, after.completion_tokens) == (3, 10, 2)
