"""The team's rules, pure (spec v2 §8.1–§8.4)."""
from __future__ import annotations

from agentd.teams.state_machine import (
    Adopt,
    Disband,
    End,
    EnterPhase,
    EvaluateRound,
    Kickoff,
    MainAdopt,
    MainPost,
    MemberReported,
    MemberState,
    Milestone,
    Requeue,
    RoundEvaluated,
    SetQuorum,
    StartRound,
    TeamState,
    apply,
)


def _state(*labels: str, round_: int = 1, max_rounds: int = 3,
           phase: str = "DELIBERATING") -> TeamState:
    labels = labels or ("alice", "bob")
    return TeamState(phase=phase, round=round_, max_rounds=max_rounds,
                     members={lb: MemberState(lb) for lb in labels}, round_members=[])


def _round1(state: TeamState) -> TeamState:
    state, _ = apply(state, Kickoff("proposal", ()))
    return state


def test_kickoff_kinds() -> None:
    _, actions = apply(_state(), Kickoff("proposal", ()))
    assert actions == [StartRound(1, ("alice", "bob"))]
    _, actions = apply(_state(), Kickoff("post", ("alice",)))
    assert actions == [StartRound(1, ("alice",))]
    _, actions = apply(_state(), Kickoff("post", ()))          # nobody mentioned: everyone
    assert actions == [StartRound(1, ("alice", "bob"))]


def test_round_ends_when_every_round_member_reported() -> None:
    s = _round1(_state())
    s, actions = apply(s, MemberReported("alice", "completed"))
    assert actions == []
    s, actions = apply(s, MemberReported("alice", "completed"))   # a stray second report
    assert actions == []
    s, actions = apply(s, MemberReported("bob", "awaiting_peer"))
    assert actions == [EvaluateRound(1)]


def test_input_state_is_not_mutated() -> None:
    s = _round1(_state())
    apply(s, MemberReported("alice", "completed"))
    assert s.members["alice"].reported is False


def test_next_round_then_deadlock() -> None:
    s = _round1(_state(max_rounds=2))
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated(None))
    assert actions == [StartRound(2, ("alice", "bob"))] and s.round == 2
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated(None))
    assert s.phase == "DEADLOCKED"
    assert actions == [EnterPhase("DEADLOCKED", 2, "round limit"),
                       Milestone("deadlock", {"round": 2})]


def test_adoption_ends_the_team_for_now() -> None:
    s = _round1(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1"))
    assert actions == [Adopt("P1", "team"), End("DONE", "adopted")]
    assert s.phase == "DONE"


def test_transient_is_requeued_twice_then_final() -> None:
    s = _round1(_state())
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert actions == [Requeue("alice", 1, 30.0)]
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert actions == [Requeue("alice", 2, 120.0)]
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert actions == [] and s.members["alice"].reported and s.members["alice"].in_quorum


def test_deadline_stop_stays_in_quorum_user_stop_leaves() -> None:
    s = _round1(_state("alice", "bob", "carol"))
    s, actions = apply(s, MemberReported("alice", "stopped", "deadline"))
    assert actions == [] and s.members["alice"].in_quorum
    s, actions = apply(s, MemberReported("bob", "stopped", "user"))
    assert actions == [SetQuorum("bob", False),
                       Milestone("member_lost", {"label": "bob", "status": "stopped"})]
    s, actions = apply(s, MemberReported("carol", "completed"))
    assert actions == [EvaluateRound(1)]
    s, actions = apply(s, RoundEvaluated(None))
    assert actions == [StartRound(2, ("alice", "carol"))]


def test_quorum_below_two_fails_the_team() -> None:
    s = _round1(_state())
    s, actions = apply(s, MemberReported("alice", "failed"))
    assert actions == [SetQuorum("alice", False),
                       Milestone("member_lost", {"label": "alice", "status": "failed"}),
                       End("FAILED", "fewer than 2 members left in the quorum")]
    assert s.phase == "FAILED"


def test_deadlock_exits() -> None:
    s = _state(round_=3, phase="DEADLOCKED")
    s2, actions = apply(s, MainPost())
    assert actions == [StartRound(4, ("alice", "bob"))]
    assert (s2.phase, s2.round, s2.max_rounds) == ("DELIBERATING", 4, 4)
    _, actions = apply(s, MainAdopt("P2"))
    assert actions == [Adopt("P2", "main"), End("DONE", "adopted")]
    _, actions = apply(_round1(_state()), MainAdopt("P2"))    # only in DEADLOCKED
    assert actions == []
    _, actions = apply(_round1(_state()), MainPost())          # an ordinary post
    assert actions == []


def test_disband_and_reports_after_the_end() -> None:
    s, actions = apply(_round1(_state()), Disband())
    assert actions == [End("DISBANDED", "disbanded")] and s.phase == "DISBANDED"
    _, actions = apply(s, MemberReported("alice", "stopped", "disband"))
    assert actions == []
