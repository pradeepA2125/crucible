"""The team's rules, pure (spec v2 §8.1–§8.4)."""
from __future__ import annotations

from agentd.teams.state_machine import (
    Adopt,
    Approval,
    AssignmentDone,
    BudgetExhausted,
    CancelTimers,
    ClosePlan,
    Disband,
    End,
    EnterPhase,
    EvaluateRound,
    ForceFinalAll,
    Kickoff,
    MainAdopt,
    MainPost,
    MainResume,
    MemberReported,
    MemberState,
    Milestone,
    RaisePlanGate,
    Requeue,
    ResumeMembers,
    RoundEvaluated,
    SetQuorum,
    StartImplementation,
    StartRound,
    Stuck,
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


def _adopted(state: TeamState, assignees: tuple[str, ...] = ("alice", "bob")) -> TeamState:
    s = _round1(state)
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, _ = apply(s, RoundEvaluated("P1", assignees))
    return s


def test_adoption_without_assignments_still_ends_done() -> None:
    s = _round1(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1", ()))
    assert actions == [Adopt("P1", "team"), End("DONE", "adopted")]


def test_adoption_with_assignments_implements() -> None:
    s = _round1(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1", ("alice",)))
    assert actions == [Adopt("P1", "team"), EnterPhase("IMPLEMENTING", 1),
                       StartImplementation(("alice",))]
    assert s.phase == "IMPLEMENTING"
    assert s.members["alice"].assigned and not s.members["bob"].assigned


def test_approval_gate_waits_then_approve_implements() -> None:
    state = _state()
    state.approval_gate = True
    s = _round1(state)
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1", ("alice", "bob")))
    assert actions == [Adopt("P1", "team", close=False), EnterPhase("AWAITING_APPROVAL", 1),
                       RaisePlanGate("P1"), Milestone("approval_needed", {"proposal_id": "P1"})]
    s, actions = apply(s, Approval("approve"))
    assert actions == [ClosePlan("adopted"), EnterPhase("IMPLEMENTING", 1),
                       StartImplementation(("alice", "bob"))]


def test_feedback_reopens_deliberation() -> None:
    state = _state(max_rounds=1)
    state.approval_gate = True
    s = _adopted(state)
    s, actions = apply(s, Approval("feedback"))
    assert actions[0] == ClosePlan("feedback")
    assert actions[1] == StartRound(2, ("alice", "bob"))
    assert (s.phase, s.round, s.max_rounds) == ("DELIBERATING", 2, 2)


def test_reject_disbands() -> None:
    state = _state()
    state.approval_gate = True
    s, actions = apply(_adopted(state), Approval("reject"))
    assert actions == [End("DISBANDED", "plan rejected")] and s.phase == "DISBANDED"


def test_completed_marks_done_and_last_one_ends_implemented() -> None:
    s = _adopted(_state())
    s, actions = apply(s, MemberReported("alice", "completed", files=("a.py",)))
    assert actions == [AssignmentDone("alice", ("a.py",))]
    s, actions = apply(s, MemberReported("bob", "completed", files=("b.py",)))
    assert actions == [AssignmentDone("bob", ("b.py",)),
                       Milestone("done", {"files": ["b.py"]}),   # widened by the coordinator
                       End("DONE", "implemented")]


def test_partial_is_blocked_and_assignment_stays_open() -> None:
    s = _adopted(_state())
    s, actions = apply(s, MemberReported("alice", "partial", report="x" * 400))
    assert actions == [Milestone("member_blocked",
                                 {"label": "alice", "status": "partial", "report": "x" * 300})]
    assert not s.members["alice"].done and s.members["alice"].in_quorum


def test_awaiting_peer_and_unassigned_reports_change_nothing() -> None:
    s = _adopted(_state(), assignees=("alice",))
    s, actions = apply(s, MemberReported("alice", "awaiting_peer"))
    assert actions == []
    s, actions = apply(s, MemberReported("bob", "completed"))
    assert actions == []


def test_three_final_transients_pause_the_team() -> None:
    s = _adopted(_state("alice", "bob", "carol"), assignees=("alice", "bob", "carol"))
    for label in ("alice", "bob"):
        for _ in range(3):
            s, _ = apply(s, MemberReported(label, "failed_transient"))
    assert s.transient_streak == 2 and s.phase == "IMPLEMENTING"
    for _ in range(2):
        s, actions = apply(s, MemberReported("carol", "failed_transient"))
        assert isinstance(actions[0], Requeue)
    s, actions = apply(s, MemberReported("carol", "failed_transient"))
    assert actions == [CancelTimers(), EnterPhase("PAUSED", 1, "transient_burst"),
                       Milestone("paused", {"reason": "transient_burst",
                                            "paused_from": "IMPLEMENTING"})]
    assert (s.phase, s.paused_from) == ("PAUSED", "IMPLEMENTING")


def test_a_non_transient_outcome_resets_the_streak() -> None:
    s = _adopted(_state())
    for _ in range(3):
        s, _ = apply(s, MemberReported("alice", "failed_transient"))
    s, _ = apply(s, MemberReported("bob", "awaiting_peer"))
    assert s.transient_streak == 0


def test_budget_pause_forces_final_and_resume_restarts_owing() -> None:
    s = _adopted(_state())
    s, actions = apply(s, BudgetExhausted())
    assert actions == [CancelTimers(), EnterPhase("PAUSED", 1, "budget"),
                       Milestone("paused", {"reason": "budget", "paused_from": "IMPLEMENTING"}),
                       ForceFinalAll()]
    s, actions = apply(s, MemberReported("alice", "partial", stop_reason="budget"))
    assert actions == [] and not s.members["alice"].done        # interrupted: still owes
    s, actions = apply(s, MemberReported("bob", "completed", files=("b.py",)))
    assert actions == [AssignmentDone("bob", ("b.py",))]          # real work counts
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("IMPLEMENTING", 1, "resumed"), ResumeMembers(("alice",))]


def test_resume_in_deliberation_evaluates_a_complete_round() -> None:
    s = _round1(_state())
    s, _ = apply(s, BudgetExhausted())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, actions = apply(s, MemberReported("bob", "completed"))
    assert actions == []                                          # deferred while paused
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("DELIBERATING", 1, "resumed"), EvaluateRound(1)]


def test_resume_in_deliberation_restarts_who_owes() -> None:
    s = _round1(_state())
    s, _ = apply(s, BudgetExhausted())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "partial", stop_reason="budget"))
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("DELIBERATING", 1, "resumed"), ResumeMembers(("bob",))]


def test_stuck_twice_is_a_milestone_third_pauses() -> None:
    s = _adopted(_state())
    idle = (("alice", "awaiting_peer"), ("bob", "awaiting_peer"))
    for count in (1, 2):
        s, actions = apply(s, Stuck(idle))
        assert actions == [Milestone("stuck", {"idle": [list(i) for i in idle],
                                               "count": count})]
    s, actions = apply(s, Stuck(idle))
    assert actions[1] == EnterPhase("PAUSED", 1, "stuck")
    s, _ = apply(s, MainResume())
    assert s.stuck_count == 0


def test_a_completed_assignment_resets_stuck() -> None:
    s = _adopted(_state())
    s, _ = apply(s, Stuck((("alice", "awaiting_peer"),)))
    s, _ = apply(s, MemberReported("alice", "completed"))
    assert s.stuck_count == 0


def test_main_adopt_from_deadlock_implements() -> None:
    s = _state(phase="DEADLOCKED")
    s, actions = apply(s, MainAdopt("P2", ("bob",)))
    assert actions == [Adopt("P2", "main"), EnterPhase("IMPLEMENTING", 1),
                       StartImplementation(("bob",))]
