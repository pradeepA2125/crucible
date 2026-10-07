"""Votes on the board (spec 2026-10-07 §4.3, §5)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService, ids_text
from agentd.teams.tools import MEMBER_TOOL_NAMES, TeamToolSource
from agentd.teams.validation import TeamInputError

_EMPTY = {"assignments": [], "shared_files": [], "supersedes": []}


def _voting(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Round 2 is a vote between alice's P1 and P2 and bob's P3."""
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=4, budget=100, created_turn_id="u", created_at=datetime.now(UTC),
                      round=2)
    teams.create_team(team)
    for label in ("alice", "bob", "carol"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    for author in ("alice", "alice", "bob"):
        teams.append_post(team.team_id, author=author, kind="proposal", text=f"plan by {author}",
                          round=1, payload={**_EMPTY, "assignments": [
                              {"member": "carol", "part": "ui", "files": ["ui.js"]}]})
    teams.update_team(team.team_id, vote_between="P1,P2,P3", round_cutoff_seq=3)
    service = TeamService(teams, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    return service, team.team_id, teams


def test_ids_text() -> None:
    assert ids_text(["P4"]) == "P4"
    assert ids_text(["P4", "P7"]) == "P4 and P7"
    assert ids_text(["P4", "P5", "P7"]) == "P4, P5 and P7"


def test_a_vote_is_posted_with_its_note(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    post = service.vote(tid, "carol", "P3", note="cleaner split")
    assert (post.kind, post.ref_id, post.round, post.text) == ("vote", "P3", 2, "cleaner split")
    assert service.current_vote(tid, "carol") == "P3"
    assert service.current_vote(tid, "bob") is None


def test_vote_refusals(tmp_path: Path) -> None:
    service, tid, teams = _voting(tmp_path)
    with pytest.raises(TeamInputError, match="can't vote for your own proposal; pick one of P3"):
        service.vote(tid, "alice", "P1")
    with pytest.raises(TeamInputError, match="P9 is not in this vote; pick one of P1 and P2"):
        service.vote(tid, "bob", "P9")
    teams.update_team(tid, vote_between="")
    with pytest.raises(TeamInputError, match="only for a vote round"):
        service.vote(tid, "carol", "P1")


def test_vote_candidates_exclude_own_proposals(tmp_path: Path) -> None:
    service, tid, teams = _voting(tmp_path)
    assert service.vote_candidates(tid, "alice") == ["P3"]
    assert service.vote_candidates(tid, "carol") == ["P1", "P2", "P3"]
    teams.update_team(tid, vote_between="")
    assert service.vote_candidates(tid, "carol") == []


def test_a_vote_round_is_vote_only(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    only = "This round is a vote between P1, P2 and P3; only votes count"
    with pytest.raises(TeamInputError, match=only):
        service.agree(tid, "carol", "P1")
    with pytest.raises(TeamInputError, match=only):
        service.object_(tid, "carol", "P1", "no", {"quote_seq": 1})
    with pytest.raises(TeamInputError, match=only):
        service.propose(tid, "carol", "new", [])
    with pytest.raises(TeamInputError, match=only):
        service.withdraw(tid, "alice", "P1")
    service.post(tid, "carol", "posting still works")
    assert service.expected_stances(tid, "carol") == []


def test_header_and_status_describe_the_vote(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    text, _top, _handed = service.render_delta_posts(tid, "carol")
    assert "Round 2 (vote) of 4: P1, P2 and P3 each have everyone's agreement" in text
    assert "candidate P3" in text and "carol → ui (ui.js)" in text
    status = service.status_text(tid, "carol")
    assert "vote: P1, P2, P3 — your vote: none" in status
    service.vote(tid, "carol", "P2")
    assert "your vote: P2" in service.status_text(tid, "carol")


@pytest.mark.asyncio
async def test_team_vote_tool(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    assert "team_vote" in MEMBER_TOOL_NAMES
    carol = TeamToolSource(service, tid, "carol", ActivationCounters())
    assert {d.name for d in carol.definitions()} == MEMBER_TOOL_NAMES
    out = await carol.execute("team_vote", {"proposal_id": "P1", "note": "simplest"})
    assert not out.is_error and json.loads(out.output) == {"seq": 4, "voted": "P1"}
    bad = await carol.execute("team_vote", {"proposal_id": "P9"})
    assert bad.is_error and "not in this vote" in bad.output
