"""The team's rules (spec v2 §8.1–§8.6) — pure, synchronous, no I/O. The coordinator feeds
events in and executes the actions out. 5B covers approval and implementation; when every
assignment is done the team ends DONE until 5C adds review."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

RETRY_BACKOFF_S: tuple[float, ...] = (30.0, 120.0)
LIVE_PHASES = frozenset({"DELIBERATING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING",
                         "DEADLOCKED", "PAUSED"})
BUDGET_STOP = "budget"          # the stop_reason of a report forced by a budget pause
TRANSIENT_BURST = 3             # consecutive final failed_transient outcomes that pause
STUCK_LIMIT = 3                 # consecutive stuck milestones that pause
_BLOCKED_EXCERPT = 300


@dataclass
class MemberState:
    label: str
    in_quorum: bool = True
    reported: bool = False   # its final outcome for this round is in
    retries: int = 0         # failed_transient re-queues (this round, or this assignment)
    assigned: bool = False   # holds an assignment in the adopted plan
    done: bool = False       # that assignment is done


@dataclass
class TeamState:
    phase: str
    round: int
    max_rounds: int
    members: dict[str, MemberState]
    round_members: list[str] = field(default_factory=list)
    approval_gate: bool = False
    paused_from: str | None = None
    stuck_count: int = 0
    transient_streak: int = 0
    pending_assignees: tuple[str, ...] = ()   # the adopted plan's assignees, awaiting approval

    def quorum(self) -> list[str]:
        return [label for label, m in self.members.items() if m.in_quorum]


# ── events ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Kickoff:
    kind: str                     # "proposal" | "post"
    mentions: tuple[str, ...]


@dataclass(frozen=True)
class MemberReported:
    label: str
    status: str
    stop_reason: str | None = None
    files: tuple[str, ...] = ()
    report: str = ""


@dataclass(frozen=True)
class RoundEvaluated:
    adopted: str | None
    assignees: tuple[str, ...] = ()


@dataclass(frozen=True)
class MainAdopt:
    proposal_id: str
    assignees: tuple[str, ...] = ()


@dataclass(frozen=True)
class MainPost:
    pass


@dataclass(frozen=True)
class Disband:
    pass


@dataclass(frozen=True)
class Approval:
    decision: str                 # "approve" | "feedback" | "reject"


@dataclass(frozen=True)
class BudgetExhausted:
    pass


@dataclass(frozen=True)
class Stuck:
    idle: tuple[tuple[str, str], ...]   # (label, last status) of each idle member


@dataclass(frozen=True)
class MainResume:
    pass


@dataclass(frozen=True)
class Revive:
    """The main agent posted to a FAILED team: reopen it in the phase it failed from."""
    from_phase: str
    proposal_id: str | None = None     # the adopted plan, when it failed awaiting approval


Event = (Kickoff | MemberReported | RoundEvaluated | MainAdopt | MainPost | Disband | Approval
         | BudgetExhausted | Stuck | MainResume | Revive)


# ── actions ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StartRound:
    round: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Requeue:
    label: str
    retry: int
    after_s: float


@dataclass(frozen=True)
class EvaluateRound:
    round: int


@dataclass(frozen=True)
class Adopt:
    proposal_id: str
    by: str                       # "team" | "main"
    close: bool = True            # False while the plan waits for the user's approval


@dataclass(frozen=True)
class Milestone:
    kind: str
    data: dict[str, object]


@dataclass(frozen=True)
class SetQuorum:
    label: str
    in_quorum: bool


@dataclass(frozen=True)
class EnterPhase:
    phase: str
    round: int
    reason: str = ""


@dataclass(frozen=True)
class End:
    phase: str                    # DONE | DISBANDED | FAILED
    reason: str


@dataclass(frozen=True)
class RaisePlanGate:
    proposal_id: str


@dataclass(frozen=True)
class ClosePlan:
    reason: str                   # "adopted" | "feedback"


@dataclass(frozen=True)
class StartImplementation:
    labels: tuple[str, ...]


@dataclass(frozen=True)
class AssignmentDone:
    label: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ResumeMembers:
    labels: tuple[str, ...]


@dataclass(frozen=True)
class CancelTimers:
    pass


@dataclass(frozen=True)
class ForceFinalAll:
    pass


Action = (StartRound | Requeue | EvaluateRound | Adopt | Milestone | SetQuorum | EnterPhase
          | End | RaisePlanGate | ClosePlan | StartImplementation | AssignmentDone
          | ResumeMembers | CancelTimers | ForceFinalAll)


def _start_round(state: TeamState, labels: list[str]) -> list[Action]:
    state.phase = "DELIBERATING"
    state.round_members = labels
    for member in state.members.values():
        member.reported = False
        member.retries = 0
    return [StartRound(state.round, tuple(labels))]


def _implement(state: TeamState, assignees: tuple[str, ...]) -> list[Action]:
    state.phase = "IMPLEMENTING"
    state.pending_assignees = ()
    state.stuck_count = 0
    for label, member in state.members.items():
        member.assigned = label in assignees
        member.done = False
        member.retries = 0
    return [EnterPhase("IMPLEMENTING", state.round), StartImplementation(assignees)]


def _adopt(state: TeamState, proposal_id: str, by: str,
           assignees: tuple[str, ...]) -> list[Action]:
    known = tuple(lb for lb in assignees if lb in state.members)
    if not known:
        # Nothing to implement: the plan is the deliverable (5A's behavior).
        state.phase = "DONE"
        return [Adopt(proposal_id, by), End("DONE", "adopted")]
    if state.approval_gate:
        state.phase = "AWAITING_APPROVAL"
        state.pending_assignees = known
        return [Adopt(proposal_id, by, close=False), EnterPhase("AWAITING_APPROVAL", state.round),
                RaisePlanGate(proposal_id),
                Milestone("approval_needed", {"proposal_id": proposal_id})]
    return [Adopt(proposal_id, by), *_implement(state, known)]


def _pause(state: TeamState, reason: str) -> list[Action]:
    state.paused_from = state.phase
    state.phase = "PAUSED"
    actions: list[Action] = [
        CancelTimers(), EnterPhase("PAUSED", state.round, reason),
        Milestone("paused", {"reason": reason, "paused_from": state.paused_from})]
    if reason == "budget":
        actions.append(ForceFinalAll())
    return actions


def _owing(state: TeamState, phase: str) -> tuple[str, ...]:
    if phase == "DELIBERATING":
        return tuple(lb for lb in state.round_members
                     if state.members[lb].in_quorum and not state.members[lb].reported)
    if phase == "IMPLEMENTING":
        return tuple(lb for lb, m in state.members.items() if m.assigned and not m.done)
    return ()


def _restore_quorum(state: TeamState) -> list[Action]:
    """The user chose to continue: every member is back in the quorum, and a member that
    left mid-round owes that round again."""
    actions: list[Action] = []
    for label, member in state.members.items():
        if not member.in_quorum:
            member.in_quorum = True
            member.reported = False
            actions.append(SetQuorum(label, True))
    return actions


def _resume(state: TeamState) -> list[Action]:
    phase = state.paused_from or "DELIBERATING"
    state.phase = phase
    state.paused_from = None
    state.stuck_count = 0
    state.transient_streak = 0
    restored = _restore_quorum(state)
    owing = _owing(state, phase)
    actions: list[Action] = [*restored, EnterPhase(phase, state.round, "resumed")]
    if phase == "DELIBERATING" and not owing:
        return [*actions, EvaluateRound(state.round)]
    if phase == "IMPLEMENTING" and not owing:
        return [*actions, *_all_done(state, [])]
    return [*actions, ResumeMembers(owing)] if owing else actions


def _all_done(state: TeamState, files: list[str]) -> list[Action]:
    # The coordinator widens `files` to every member's files when it runs the milestone.
    state.phase = "DONE"
    return [Milestone("done", {"files": files}), End("DONE", "implemented")]


def _final_transient(state: TeamState, member: MemberState, event: MemberReported,
                     ) -> tuple[list[Action], bool]:
    """A failed_transient outcome: re-queue it, or count it as final. Returns the actions
    and whether the outcome was final (the caller continues only when it was)."""
    if state.phase == "PAUSED":
        return [], False                     # no re-queue while paused: resume restarts it
    if member.retries < len(RETRY_BACKOFF_S):
        member.retries += 1
        return [Requeue(event.label, member.retries, RETRY_BACKOFF_S[member.retries - 1])], False
    member.retries = 0
    state.transient_streak += 1
    if state.transient_streak >= TRANSIENT_BURST:
        return _pause(state, "transient_burst"), False
    return [], True


def _deliberation_report(state: TeamState, event: MemberReported) -> list[Action]:
    member = state.members.get(event.label)
    if member is None or member.reported or event.label not in state.round_members:
        return []
    actions: list[Action] = []
    if event.status == "failed_transient":
        actions, final = _final_transient(state, member, event)
        if not final:
            return actions
    else:
        state.transient_streak = 0
    paused = state.phase == "PAUSED"
    if event.stop_reason == BUDGET_STOP:
        return actions                       # interrupted: the member still owes its stances
    member.reported = True
    lost = event.status == "failed" or (event.status == "stopped" and event.stop_reason == "user")
    if lost:
        member.in_quorum = False
        actions += [SetQuorum(event.label, False),
                    Milestone("member_lost", {"label": event.label, "status": event.status})]
        if len(state.quorum()) < 2:
            # Paused, not failed: a member often fails on the provider, not on the work,
            # and resume_team brings it back (asked for after the 5B live smoke).
            return [*actions, *_pause(state, "quorum lost")]
    waiting = [lb for lb in state.round_members
               if state.members[lb].in_quorum and not state.members[lb].reported]
    if not waiting and not paused:
        actions.append(EvaluateRound(state.round))
    return actions


def _implementation_report(state: TeamState, event: MemberReported) -> list[Action]:
    member = state.members.get(event.label)
    if member is None or not member.assigned or member.done:
        return []
    actions: list[Action] = []
    if event.status == "failed_transient":
        actions, final = _final_transient(state, member, event)
        if not final:
            return actions
    else:
        state.transient_streak = 0
        member.retries = 0
    if event.stop_reason == BUDGET_STOP:
        return actions                       # interrupted: the assignment stays open
    if event.status == "completed":
        member.done = True
        state.stuck_count = 0
        actions.append(AssignmentDone(event.label, event.files))
        if state.phase == "IMPLEMENTING" and all(m.done for m in state.members.values()
                                                 if m.assigned):
            return [*actions, *_all_done(state, sorted(event.files))]
        return actions
    if event.status in ("partial", "failed", "failed_transient") or (
            event.status == "stopped" and event.stop_reason == "user"):
        actions.append(Milestone("member_blocked", {
            "label": event.label, "status": event.status,
            "report": event.report[:_BLOCKED_EXCERPT]}))
    return actions


def _revive(state: TeamState, event: Revive) -> list[Action]:
    restored = _restore_quorum(state)
    state.stuck_count = 0
    state.transient_streak = 0
    phase = event.from_phase
    if phase == "IMPLEMENTING":
        state.phase = phase
        owing = _owing(state, phase)
        return [*restored, EnterPhase(phase, state.round, "revived"),
                *([ResumeMembers(owing)] if owing else _all_done(state, []))]
    if phase == "AWAITING_APPROVAL" and event.proposal_id is not None:
        state.phase = phase
        return [*restored, EnterPhase(phase, state.round, "revived"),
                RaisePlanGate(event.proposal_id)]
    # Deliberation (or any other phase): one more round, as a main post in a deadlock does.
    state.round += 1
    state.max_rounds += 1
    return [*restored, *_start_round(state, state.quorum())]


def apply(state: TeamState, event: Event) -> tuple[TeamState, list[Action]]:
    s = copy.deepcopy(state)
    if isinstance(event, Revive):
        return (s, _revive(s, event)) if s.phase == "FAILED" else (s, [])
    if s.phase not in LIVE_PHASES:
        return s, []
    if isinstance(event, Disband):
        s.phase = "DISBANDED"
        return s, [End("DISBANDED", "disbanded")]
    if isinstance(event, Kickoff):
        mentioned = [lb for lb in event.mentions if lb in s.members and s.members[lb].in_quorum]
        labels = mentioned if event.kind == "post" and mentioned else s.quorum()
        return s, _start_round(s, labels)
    if isinstance(event, MainAdopt):
        if s.phase != "DEADLOCKED":
            return s, []
        return s, _adopt(s, event.proposal_id, "main", event.assignees)
    if isinstance(event, MainPost):
        if s.phase != "DEADLOCKED":
            return s, []
        s.round += 1
        s.max_rounds += 1
        return s, _start_round(s, s.quorum())
    if isinstance(event, RoundEvaluated):
        if s.phase != "DELIBERATING":
            return s, []
        if event.adopted is not None:
            return s, _adopt(s, event.adopted, "team", event.assignees)
        if s.round >= s.max_rounds:
            s.phase = "DEADLOCKED"
            return s, [EnterPhase("DEADLOCKED", s.round, "round limit"),
                       Milestone("deadlock", {"round": s.round})]
        s.round += 1
        return s, _start_round(s, s.quorum())
    if isinstance(event, Approval):
        if s.phase != "AWAITING_APPROVAL":
            return s, []
        if event.decision == "approve":
            return s, [ClosePlan("adopted"), *_implement(s, s.pending_assignees)]
        if event.decision == "feedback":
            s.pending_assignees = ()
            s.round += 1
            s.max_rounds += 1
            return s, [ClosePlan("feedback"), *_start_round(s, s.quorum())]
        s.phase = "DISBANDED"
        return s, [End("DISBANDED", "plan rejected")]
    if isinstance(event, BudgetExhausted):
        if s.phase == "PAUSED":
            return s, []
        return s, _pause(s, "budget")
    if isinstance(event, Stuck):
        if s.phase != "IMPLEMENTING":
            return s, []
        s.stuck_count += 1
        if s.stuck_count >= STUCK_LIMIT:
            return s, _pause(s, "stuck")
        return s, [Milestone("stuck", {"idle": [list(i) for i in event.idle],
                                       "count": s.stuck_count})]
    if isinstance(event, MainResume):
        return (s, _resume(s)) if s.phase == "PAUSED" else (s, [])
    assert isinstance(event, MemberReported)
    phase = s.paused_from if s.phase == "PAUSED" else s.phase
    if phase == "DELIBERATING":
        return s, _deliberation_report(s, event)
    if phase == "IMPLEMENTING":
        return s, _implementation_report(s, event)
    return s, []
