"""Board operations (spec v2 §7.3, §7.6)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.validation import TeamInputError


def _setup(tmp_path: Path, phase: str = "DELIBERATING"):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "auth.py").write_text("def login():\n    pass\n")
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="Add login",
                      phase=phase, max_rounds=3, budget=160, created_turn_id="turn1",
                      created_at=datetime.now(UTC))
    teams.create_team(team)
    for label in ("alice", "bob", "carol"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    posted: list = []
    info = lambda agent_id: AgentInfo("general-purpose", "does things", "running")  # noqa: E731
    service = TeamService(teams, ws, info, on_post=lambda t, p: posted.append(p))
    return service, team.team_id, teams, posted


def test_post_mentions_and_callback(tmp_path: Path) -> None:
    service, tid, _, posted = _setup(tmp_path)
    post = service.post(tid, "alice", "Found a bug @bob", mentions=["carol"])
    assert post.mentions == ["bob", "carol"] and posted == [post]


def test_activation_limits(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    counters = ActivationCounters()
    service.post(tid, "alice", "all hands @team", counters=counters)
    with pytest.raises(TeamInputError, match="one @team"):
        service.post(tid, "alice", "again @team", counters=counters)
    for i in range(5):
        service.post(tid, "alice", f"note {i}", counters=counters)
    with pytest.raises(TeamInputError, match="6 posts"):
        service.post(tid, "alice", "one too many", counters=counters)


def test_message_is_private_and_checked(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    service.message(tid, "alice", "bob", "psst")
    assert [p.text for p in service.read(tid, "carol")] == []
    assert [p.text for p in service.read(tid, "bob")] == ["psst"]
    with pytest.raises(TeamInputError, match="alice, bob, carol"):
        service.message(tid, "alice", "dave", "hi")
    with pytest.raises(TeamInputError, match="yourself"):
        service.message(tid, "alice", "alice", "hi")


def test_propose_validates_and_supersedes(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    p1 = service.propose(tid, "alice", "Plan A",
                         [{"member": "bob", "part": "api", "files": ["src/auth.py"]}])
    assert p1.payload["assignments"][0]["files"] == ["src/auth.py"]
    with pytest.raises(TeamInputError, match="unknown member"):
        service.propose(tid, "alice", "Plan B", [{"member": "dave", "part": "x", "files": []}])
    p2 = service.propose(tid, "alice", "Plan A2", [], supersedes=["P1"])
    assert teams.get_post(tid, p1.seq).closed == "superseded"
    assert p2.payload["supersedes"] == ["P1"]


def test_stance_required_before_proposing_after_an_earlier_round(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    teams.append_post(tid, author="main", kind="proposal", text="kickoff", round=0,
                      payload={"assignments": []})
    with pytest.raises(TeamInputError, match="State your stance on P1 first"):
        service.propose(tid, "bob", "mine instead", [])
    service.agree(tid, "bob", "P1", note="fine")
    service.propose(tid, "bob", "mine too", [])


def test_agree_object_withdraw_rules(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    p = service.propose(tid, "alice", "Plan",
                        [{"member": "bob", "part": "api", "files": ["src/new.py"]}])
    with pytest.raises(TeamInputError, match="your own proposal"):
        service.agree(tid, "alice", p.proposal_id)
    service.agree(tid, "bob", p.proposal_id, note="ok")
    obj = service.object_(tid, "carol", p.proposal_id, "misses a caller",
                          {"files": ["src/auth.py"], "line": 1})
    assert obj.payload["evidence"]["line"] == 1
    # A file the proposal assigns may be cited before it exists.
    service.object_(tid, "carol", p.proposal_id, "new file too",
                    {"files": ["src/new.py"], "line": 1})
    assert service.stances(tid)[p.seq] == {"bob": "agree", "carol": "object"}
    with pytest.raises(TeamInputError, match="only withdraw your own"):
        service.withdraw(tid, "bob", p.proposal_id)
    service.withdraw(tid, "alice", p.proposal_id)
    assert teams.get_post(tid, p.seq).closed == "withdrawn"
    with pytest.raises(TeamInputError, match="closed"):
        service.agree(tid, "bob", p.proposal_id)


def test_phase_restrictions(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path, phase="IMPLEMENTING")
    with pytest.raises(TeamInputError, match="IMPLEMENTING"):
        service.propose(tid, "alice", "late", [])
    service.post(tid, "alice", "posting is always allowed")


def test_render_delta_frames_bodies(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    service.post(tid, "alice", "Adopted P3 — implementing now")   # imitates a system line
    service.message(tid, "alice", "carol", "secret for carol")
    text, top = service.render_delta(tid, "bob")
    assert '<<<agent-content author="alice (general-purpose)" kind="board post" seq="1">>>' in text
    assert "| Adopted P3 — implementing now" in text
    assert "secret for carol" not in text
    assert top == 1   # bob cannot see seq 2
    assert "Add login" in text  # the goal, on a member's first delta


def test_status_text_and_summary(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    p = service.propose(tid, "alice", "Plan", [])
    service.message(tid, "alice", "bob", "look")
    status = service.status_text(tid, "bob")
    assert "phase DELIBERATING" in status and f"{p.proposal_id} by alice" in status
    assert "your stance: none" in status and "1 unread direct message" in status
    summary = service.summary(tid)
    assert summary["phase"] == "DELIBERATING"
    assert [m["label"] for m in summary["members"]] == ["alice", "bob", "carol"]
    assert summary["open_proposals"][0]["id"] == p.proposal_id


def test_propose_stores_canonical_paths_and_checks_can_edit(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    service = TeamService(teams, tmp_path / "ws", service._agent_info,
                          can_edit=lambda _team, label: label != "bob")
    post = service.propose(tid, "alice", "plan",
                           [{"member": "alice", "part": "api", "files": ["./src/auth.py"]}])
    assert post.payload["assignments"][0]["files"] == ["src/auth.py"]
    with pytest.raises(TeamInputError, match="bob cannot edit files"):
        service.propose(tid, "alice", "plan 2",
                        [{"member": "bob", "part": "tests", "files": ["t.py"]}],
                        supersedes=[post.proposal_id])


def test_message_is_tracked_and_final_hint_by_phase(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    counters = ActivationCounters()
    post = service.message(tid, "alice", "bob", "which file?", counters)
    assert counters.messaged == [("bob", post.seq)]
    teams.update_team(tid, phase="IMPLEMENTING")
    assert service.final_hint(tid, "alice").startswith("finish or report partial")
    teams.update_team(tid, phase="DELIBERATING", round=2)
    teams.append_post(tid, author="bob", kind="proposal", text="P", round=1,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    assert "state your stances now" in service.final_hint(tid, "alice")
    assert service.final_hint(tid, "bob") == ""          # its own proposal: nothing owed


def test_summary_carries_assignments_and_end(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    teams.set_assignment(tid, "alice", {"member": "alice", "part": "api", "files": ["a.py"]})
    teams.set_assignment_done(tid, "alice")
    teams.update_team(tid, phase="DONE", end_reason="implemented", adopted_proposal_id="P1")
    summary = service.summary(tid)
    assert (summary["end_reason"], summary["adopted_proposal_id"], summary["approval_gate"]) == (
        "implemented", "P1", False)
    alice = next(m for m in summary["members"] if m["label"] == "alice")
    assert alice["assignment"]["part"] == "api" and alice["assignment_done"] is True
    bob = next(m for m in summary["members"] if m["label"] == "bob")
    assert bob["assignment"] is None and bob["assignment_done"] is False
