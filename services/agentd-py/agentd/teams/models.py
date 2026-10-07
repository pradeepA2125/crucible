"""Team records (spec v2 §7.4)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

TEAM_PHASES: tuple[str, ...] = (
    "DELIBERATING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING", "DEADLOCKED", "PAUSED",
    "DONE", "DISBANDED", "FAILED")
LIVE_TEAM_PHASES = frozenset(TEAM_PHASES[:6])
POST_KINDS = frozenset({"post", "proposal", "agree", "object", "withdraw", "system"})


def new_team_id() -> str:
    return f"team-{uuid4().hex[:12]}"


class TeamRecord(BaseModel):
    team_id: str
    thread_id: str
    name: str
    goal: str
    phase: str = "DELIBERATING"
    paused_reason: str | None = None
    round: int = 1
    max_rounds: int
    round_started_at: datetime | None = None
    approval_gate: bool = False
    adopted_proposal_id: str | None = None
    closing_proposal_id: str | None = None
    review_cycles: int = 0
    stuck_count: int = 0
    budget: int
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    created_turn_id: str
    checkpoint_seq: int = -1
    created_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    round_cutoff_seq: int | None = None   # the board's top seq when the current round started
    lead: str | None = None               # the member who alone proposes (None: anyone may)
    vote_between: str = ""                # a vote round's candidates, comma-separated ("P4,P7")

    @property
    def vote_ids(self) -> list[str]:
        return [p for p in self.vote_between.split(",") if p]


class TeamMember(BaseModel):
    team_id: str
    agent_id: str
    label: str
    in_quorum: bool = True
    delivered_seq: int = 0
    assignment: dict[str, Any] | None = None
    assignment_done: bool = False
    reprompted: bool = False
    wakes_this_phase: int = 0


class TeamPost(BaseModel):
    team_id: str
    seq: int
    author: str          # member label | "main" | "user" | "system"
    kind: str            # post | proposal | agree | object | withdraw | system
    recipient: str | None = None   # None = the board; a label = a direct message
    text: str
    mentions: list[str] = Field(default_factory=list)
    ref_id: str | None = None      # the proposal an agree/object/withdraw targets
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    closed: str | None = None      # proposals: withdrawn | superseded | feedback | adopted
    created_at: datetime

    @property
    def proposal_id(self) -> str:
        return f"P{self.seq}"


# Spec 2026-10-05 §4.2 and §9 — the lifecycle facts the UI renders; never member input.
ACTIVITY_KINDS = frozenset({
    "phase", "woke", "notified", "took_up", "picked_up", "wrapped_up", "capped",
    "round_started", "round_ended", "held", "requeued", "deadline"})
WAKE_CAUSES = frozenset({
    "kickoff", "mention", "team_mention", "message", "main_post", "leftover"})


class TeamActivity(BaseModel):
    team_id: str
    aseq: int
    at: datetime
    label: str          # member label, or "main" / "team" for team-level events
    kind: str
    activation: int | None = None
    cause_seq: int | None = None   # the board post that caused it, when one did
    payload: dict[str, Any] = Field(default_factory=dict)
