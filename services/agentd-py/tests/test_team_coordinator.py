"""The coordinator drives rounds through its host (spec v2 §8.1, §8.3)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.teams.coordinator import TeamCoordinator
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.store import TeamStore
from agentd.teams.trace import CoordinatorTrace


class _Host:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.extras: dict[str, str] = {}
        self.stopped: list[tuple[str, str]] = []
        self.forced: list[str] = []
        self.milestones: list[str] = []
        self.deliveries: list[tuple[str, str]] = []
        self.phases: list[str] = []
        self.active: dict[str, float | None] = {}
        self.woken: list[int] = []
        self.gates: list[str] = []
        self.busy: set[str] = set()
        self.wakes: set[str] = set()
        self.requests = 0

    def start_member(self, team_id: str, label: str, extra: str = "") -> None:
        self.started.append(label)
        self.extras[label] = extra

    async def stop_member(self, team_id: str, label: str, reason: str) -> None:
        self.stopped.append((label, reason))

    def force_final(self, team_id: str, label: str) -> bool:
        self.forced.append(label)
        return True

    def active_seconds(self, team_id: str, label: str) -> float | None:
        return self.active.get(label, 0.0)

    def team_milestone(self, team, kind, headline, body, delivery="wake") -> None:  # type: ignore[no-untyped-def]
        self.milestones.append(kind)
        self.deliveries.append((kind, delivery))

    def team_phase_changed(self, team) -> None:  # type: ignore[no-untyped-def]
        self.phases.append(team.phase)

    def wake_for_post(self, team, post) -> None:  # type: ignore[no-untyped-def]
        self.woken.append(post.seq)

    def raise_plan_gate(self, team, proposal) -> None:  # type: ignore[no-untyped-def]
        self.gates.append(proposal.proposal_id)

    def force_team_final(self, team_id: str) -> list[str]:
        return sorted(self.busy)

    def team_requests(self, team_id: str) -> int:
        return self.requests

    def is_busy(self, team_id: str, label: str) -> bool:
        return label in self.busy

    def has_wakes(self, team_id: str, label: str) -> bool:
        return label in self.wakes


def _setup(tmp_path: Path, *, max_rounds: int = 3, timeout: float = 900.0,
           labels: tuple[str, ...] = ("alice", "bob"), approval_gate: bool = False,
           assignments: list[dict[str, object]] | None = None, shared: list[str] | None = None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=max_rounds, budget=100, created_turn_id="u",
                                 approval_gate=approval_gate, created_at=datetime.now(UTC)))
    for label in labels:
        store.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}", label=label))
    svc = TeamService(store, tmp_path, lambda _a: AgentInfo("gp", "", "idle"))
    host = _Host()
    trace_path = tmp_path / "coordinator.jsonl"
    coord = TeamCoordinator("team-1", store, svc, host, CoordinatorTrace(trace_path),
                            round_timeout_s=timeout, grace_s=0.05)
    store.append_post("team-1", author="main", kind="proposal", text="P1", round=0,
                      payload={"assignments": assignments or [], "shared_files": shared or [],
                               "supersedes": []})
    return store, svc, host, coord, trace_path


PARTS = [{"member": "alice", "part": "api", "files": ["a.py"]},
         {"member": "bob", "part": "tests", "files": ["t.py"]}]


def _adopt_round_one(store, svc, coord, labels=("alice", "bob")) -> None:  # type: ignore[no-untyped-def]
    coord.kickoff("proposal", [])
    for label in labels:
        svc.agree("team-1", label, "P1")
    for label in labels:
        coord.on_report(label, "completed", None)


def _kinds(store: TeamStore) -> list[str]:
    return [e.kind for e in store.activity("team-1")]


@pytest.mark.asyncio
async def test_kickoff_starts_round_one(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    assert host.started == ["alice", "bob"]
    started = next(e for e in store.activity("team-1") if e.kind == "round_started")
    assert started.payload == {"round": 1, "members": [
        {"label": "alice", "handed": [1]}, {"label": "bob", "handed": [1]}]}
    assert coord.delta_cutoff() == 1
    coord.close()


@pytest.mark.asyncio
async def test_round_end_evaluates_and_adopts(tmp_path) -> None:
    store, svc, host, coord, trace = _setup(tmp_path)
    coord.kickoff("proposal", [])
    svc.agree("team-1", "alice", "P1")
    svc.agree("team-1", "bob", "P1")
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    team = store.get_team("team-1")
    assert (team.phase, team.adopted_proposal_id, team.end_reason) == ("DONE", "P1", "adopted")
    assert store.get_post("team-1", 1).closed == "adopted"
    ended = next(e for e in store.activity("team-1") if e.kind == "round_ended")
    assert ended.payload["adopted"] == "P1"
    system = [p for p in store.posts("team-1") if p.kind == "system"]
    assert system[-1].text.startswith("Adopted P1.") and system[-1].payload["adopted"] == "P1"
    assert host.milestones == ["adopted"] and host.phases[-1] == "DONE"
    kinds = [json.loads(line)["kind"] for line in trace.read_text().splitlines()]
    assert "evaluation" in kinds


@pytest.mark.asyncio
async def test_no_adoption_runs_round_two_then_deadlocks(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, max_rounds=2)
    coord.kickoff("proposal", [])
    for _ in range(2):
        coord.on_report("alice", "completed", None)
        coord.on_report("bob", "completed", None)
    assert host.started == ["alice", "bob", "alice", "bob"]
    assert store.get_team("team-1").phase == "DEADLOCKED"
    phases = [e.payload.get("phase") for e in store.activity("team-1") if e.kind == "phase"]
    assert phases == ["DELIBERATING", "DEADLOCKED"]   # round 2's chapter, then the deadlock
    rounds = [e.payload["round"] for e in store.activity("team-1") if e.kind == "round_started"]
    assert rounds == [1, 2]
    assert host.milestones == ["deadlock"]


@pytest.mark.asyncio
async def test_mid_round_posts_are_held(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    post = svc.post("team-1", "alice", "@bob look at this")
    coord.on_post(post)
    dm = svc.message("team-1", "bob", "alice", "which file?")
    coord.on_post(dm)
    board = svc.post("team-1", "bob", "a general note")
    coord.on_post(board)
    held = [e.payload for e in store.activity("team-1") if e.kind == "held"]
    assert held == [
        {"post_seq": post.seq, "for": ["bob"], "everyone": False, "until_round": 2},
        {"post_seq": dm.seq, "for": ["alice"], "everyone": False, "until_round": 2},
        {"post_seq": board.seq, "for": ["alice"], "everyone": True, "until_round": 2}]
    assert host.woken == []


@pytest.mark.asyncio
async def test_main_post_in_deadlock_runs_one_more_round(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, max_rounds=1)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    assert store.get_team("team-1").phase == "DEADLOCKED"
    coord.on_post(svc.post("team-1", "main", "@team please agree on one plan"))
    team = store.get_team("team-1")
    assert (team.phase, team.round, team.max_rounds) == ("DELIBERATING", 2, 2)
    assert host.started[-2:] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_main_adopt_from_deadlock(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, max_rounds=1)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    coord.main_adopt("P1")
    team = store.get_team("team-1")
    assert team.phase == "DONE" and team.adopted_proposal_id == "P1"
    assert "by the main agent" in [p for p in store.posts("team-1") if p.kind == "system"][-1].text


@pytest.mark.asyncio
async def test_transient_requeue_after_backoff(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("agentd.teams.state_machine.RETRY_BACKOFF_S", (0.01, 0.01))
    store, _svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "failed_transient", None)
    assert "requeued" in _kinds(store)
    await asyncio.sleep(0.05)
    assert host.started == ["alice", "bob", "alice"]
    coord.close()


@pytest.mark.asyncio
async def test_deadline_forces_final_then_stops(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, timeout=0.02)
    coord.kickoff("proposal", [])
    host.active["alice"] = 1.0
    coord.on_activation_start("alice")
    await asyncio.sleep(0.15)
    assert host.forced == ["alice"]
    assert "deadline" in _kinds(store)
    assert ("alice", "deadline") in host.stopped
    coord.close()


@pytest.mark.asyncio
async def test_deadline_reschedules_while_paused(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, timeout=0.05)
    coord.kickoff("proposal", [])
    host.active["alice"] = 0.0                 # parked at a gate: no active time passes
    coord.on_activation_start("alice")
    await asyncio.sleep(0.12)
    assert host.forced == []
    coord.on_report("alice", "completed", None)   # reporting cancels its clock
    host.active["alice"] = 10.0
    await asyncio.sleep(0.1)
    assert host.forced == []
    coord.close()


@pytest.mark.asyncio
async def test_disband_stops_members_and_records(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    await coord.disband()
    team = store.get_team("team-1")
    assert team.phase == "DISBANDED"
    assert sorted(host.stopped) == [("alice", "disband"), ("bob", "disband")]
    assert host.milestones == ["ended"] and host.phases[-1] == "DISBANDED"
    assert [p.text for p in store.posts("team-1") if p.kind == "system"] == [
        "The team was disbanded."]


@pytest.mark.asyncio
async def test_adoption_hands_out_assignments(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, shared=["s.py"])
    _adopt_round_one(store, svc, coord)
    team = store.get_team("team-1")
    assert team.phase == "IMPLEMENTING" and store.get_post("team-1", 1).closed == "adopted"
    assert store.member("team-1", "alice").assignment["files"] == ["a.py"]
    assert host.started[-2:] == ["alice", "bob"]
    assert host.extras["alice"] == ("Your assignment: api. Files you own: a.py. "
                                    "Shared files: s.py.")
    assert host.milestones == ["adopted"]
    coord.close()


@pytest.mark.asyncio
async def test_approval_gate_waits_then_approve(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True)
    _adopt_round_one(store, svc, coord)
    team = store.get_team("team-1")
    assert (team.phase, team.adopted_proposal_id) == ("AWAITING_APPROVAL", "P1")
    assert store.get_post("team-1", 1).closed is None
    assert host.gates == ["P1"] and host.milestones == ["approval_needed"]
    coord.approval("approve", None)
    assert store.get_team("team-1").phase == "IMPLEMENTING"
    assert store.get_post("team-1", 1).closed == "adopted"
    assert any(p.text == "The user approved P1." for p in store.posts("team-1"))
    coord.close()


@pytest.mark.asyncio
async def test_feedback_reopens_deliberation(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True,
                                        max_rounds=1)
    _adopt_round_one(store, svc, coord)
    coord.approval("feedback", "Use redis, not memory")
    team = store.get_team("team-1")
    assert (team.phase, team.round, team.max_rounds, team.adopted_proposal_id) == (
        "DELIBERATING", 2, 2, None)
    assert store.get_post("team-1", 1).closed == "feedback"
    feedback = next(p for p in store.posts("team-1") if p.author == "user")
    assert feedback.text == "Use redis, not memory"
    assert coord.delta_cutoff() >= feedback.seq                 # round 2's input includes it
    coord.close()


@pytest.mark.asyncio
async def test_reject_disbands(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True)
    _adopt_round_one(store, svc, coord)
    coord.approval("reject", None)
    team = store.get_team("team-1")
    assert (team.phase, team.end_reason) == ("DISBANDED", "plan rejected")


@pytest.mark.asyncio
async def test_assignments_done_end_implemented(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    coord.on_report("alice", "completed", None, files=("a.py",))
    assert store.member("team-1", "alice").assignment_done
    assert any(p.text == '● alice finished "api" — files: a.py' for p in store.posts("team-1"))
    coord.on_report("bob", "completed", None, files=("t.py",))
    team = store.get_team("team-1")
    assert (team.phase, team.end_reason) == ("DONE", "implemented")
    assert host.milestones[-1] == "done"


@pytest.mark.asyncio
async def test_two_waiting_members_are_stuck(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    host.busy = {"bob"}                       # bob still running when alice reports
    coord.on_report("alice", "awaiting_peer", None)
    assert "stuck" not in host.milestones
    host.busy = {"bob"}                       # bob is the reporter: still inside its task
    coord.on_report("bob", "awaiting_peer", None)
    assert host.milestones[-1] == "stuck"
    assert store.get_team("team-1").stuck_count == 1
    coord.close()


@pytest.mark.asyncio
async def test_a_waking_reporter_is_not_stuck(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    host.busy = {"bob"}
    coord.on_report("alice", "awaiting_peer", None)
    host.busy, host.wakes = {"bob"}, {"bob"}   # a message reached bob while it ran
    coord.on_report("bob", "awaiting_peer", None)
    assert "stuck" not in host.milestones
    coord.close()


@pytest.mark.asyncio
async def test_budget_pause_forces_final_and_resume_restarts_owing(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    host.requests, host.busy = 100, {"alice", "bob"}
    coord.check_budget()
    team = store.get_team("team-1")
    assert (team.phase, team.paused_reason) == ("PAUSED", "budget")
    assert host.milestones[-1] == "paused"
    coord.on_report("alice", "partial", None)                   # forced → interrupted
    coord.on_report("bob", "completed", None, files=("t.py",))  # finished for real
    assert "member_blocked" not in host.milestones
    assert not store.member("team-1", "alice").assignment_done
    assert store.member("team-1", "bob").assignment_done
    host.started.clear()
    coord.resume(50)
    team = store.get_team("team-1")
    assert (team.phase, team.budget, team.paused_reason) == ("IMPLEMENTING", 150, None)
    assert host.started == ["alice"]
    coord.close()


@pytest.mark.asyncio
async def test_transient_burst_pauses_with_a_notify(tmp_path) -> None:
    labels = ("alice", "bob", "carol")
    parts = [{"member": lb, "part": lb, "files": [f"{lb}.py"]} for lb in labels]
    store, svc, host, coord, _ = _setup(tmp_path, assignments=parts, labels=labels)
    _adopt_round_one(store, svc, coord, labels)
    for label in labels:
        for _ in range(3):
            coord.on_report(label, "failed_transient", None)
    team = store.get_team("team-1")
    assert (team.phase, team.paused_reason) == ("PAUSED", "transient_burst")
    assert host.deliveries[-1] == ("paused", "notify")
    coord.user_spoke()
    coord.resume(10)
    assert store.get_team("team-1").phase == "IMPLEMENTING"
    coord.close()


@pytest.mark.asyncio
async def test_posts_wake_only_while_implementing(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True)
    _adopt_round_one(store, svc, coord)
    coord.on_post(store.append_post("team-1", author="main", kind="post", text="@alice hi",
                                    mentions=["alice"]))
    assert host.woken == []                                     # awaiting approval
    coord.approval("approve", None)
    post = store.append_post("team-1", author="main", kind="post", text="@alice hi",
                             mentions=["alice"])
    coord.on_post(post)
    assert host.woken == [post.seq]
    coord.close()
