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
    created_turn_id: str
    checkpoint_seq: int = -1
    created_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None


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
