"""The team's rules (spec v2 §8.1–§8.4) — pure, synchronous, no I/O. The coordinator feeds
events in and executes the actions out. 5A covers deliberation; an adoption ends the team
DONE until 5B adds the approval gate and implementation."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

RETRY_BACKOFF_S: tuple[float, ...] = (30.0, 120.0)
LIVE_PHASES = frozenset({"DELIBERATING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING",
                         "DEADLOCKED", "PAUSED"})


@dataclass
class MemberState:
    label: str
    in_quorum: bool = True
    reported: bool = False   # its final outcome for this round is in
    retries: int = 0         # failed_transient re-queues this round


@dataclass
class TeamState:
    phase: str
    round: int
    max_rounds: int
    members: dict[str, MemberState]
    round_members: list[str] = field(default_factory=list)

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


@dataclass(frozen=True)
class RoundEvaluated:
    adopted: str | None


@dataclass(frozen=True)
class MainAdopt:
    proposal_id: str


@dataclass(frozen=True)
class MainPost:
    pass


@dataclass(frozen=True)
class Disband:
    pass


Event = Kickoff | MemberReported | RoundEvaluated | MainAdopt | MainPost | Disband


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


Action = StartRound | Requeue | EvaluateRound | Adopt | Milestone | SetQuorum | EnterPhase | End


def _start_round(state: TeamState, labels: list[str]) -> list[Action]:
    state.phase = "DELIBERATING"
    state.round_members = labels
    for member in state.members.values():
        member.reported = False
        member.retries = 0
    return [StartRound(state.round, tuple(labels))]


def _adopt(state: TeamState, proposal_id: str, by: str) -> list[Action]:
    # 5A interim (replaced in 5B): adoption ends the team; the plan is the deliverable.
    state.phase = "DONE"
    return [Adopt(proposal_id, by), End("DONE", "adopted")]


def apply(state: TeamState, event: Event) -> tuple[TeamState, list[Action]]:
    s = copy.deepcopy(state)
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
        return (s, _adopt(s, event.proposal_id, "main")) if s.phase == "DEADLOCKED" else (s, [])
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
            return s, _adopt(s, event.adopted, "team")
        if s.round >= s.max_rounds:
            s.phase = "DEADLOCKED"
            return s, [EnterPhase("DEADLOCKED", s.round, "round limit"),
                       Milestone("deadlock", {"round": s.round})]
        s.round += 1
        return s, _start_round(s, s.quorum())
    assert isinstance(event, MemberReported)
    member = s.members.get(event.label)
    if (s.phase != "DELIBERATING" or member is None or member.reported
            or event.label not in s.round_members):
        return s, []
    actions: list[Action] = []
    if event.status == "failed_transient" and member.retries < len(RETRY_BACKOFF_S):
        member.retries += 1
        return s, [Requeue(event.label, member.retries, RETRY_BACKOFF_S[member.retries - 1])]
    member.reported = True
    lost = event.status == "failed" or (event.status == "stopped" and event.stop_reason == "user")
    if lost:
        member.in_quorum = False
        actions += [SetQuorum(event.label, False),
                    Milestone("member_lost", {"label": event.label, "status": event.status})]
        if len(s.quorum()) < 2:
            s.phase = "FAILED"
            return s, [*actions, End("FAILED", "fewer than 2 members left in the quorum")]
    waiting = [lb for lb in s.round_members
               if s.members[lb].in_quorum and not s.members[lb].reported]
    if not waiting:
        actions.append(EvaluateRound(s.round))
    return s, actions
