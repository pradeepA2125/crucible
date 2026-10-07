"""Adoption at round end (spec v2 §8.3) — pure, so it is testable without a team."""
from __future__ import annotations

from dataclasses import dataclass

from agentd.teams.models import TeamPost


@dataclass(frozen=True)
class ProposalEvaluation:
    id: str
    round: int
    stances: dict[str, str]   # quorum label -> agree | object | none (the author agrees)
    eligible: bool            # posted before the round that just ended started
    adopted: bool
    reason: str

    def as_payload(self) -> dict[str, object]:
        return {"id": self.id, "round": self.round, "stances": dict(self.stances),
                "eligible": self.eligible, "adopted": self.adopted, "reason": self.reason}


@dataclass(frozen=True)
class Evaluation:
    round: int
    proposals: tuple[ProposalEvaluation, ...]
    adopted: str | None

    def as_payload(self) -> dict[str, object]:
        return {"round": self.round, "adopted": self.adopted,
                "proposals": [p.as_payload() for p in self.proposals]}

    def summary(self) -> str:
        return evaluation_text(self.as_payload())


def evaluation_text(payload: dict[str, object]) -> str:
    """One line for a stored round_ended payload — team_status and the trace read it back."""
    raw = payload.get("proposals")
    proposals = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    if not proposals:
        return "no open proposals"
    return "; ".join(f"{p['id']} adopted" if p.get("adopted") else
                     f"{p['id']} not adopted: {p.get('reason', '')}" for p in proposals)


def evaluate_round(posts: list[TeamPost], quorum: list[str], ended_round: int) -> Evaluation:
    """A proposal is adopted when it is open, was posted before the ended round started,
    and every quorum member's latest stance on it is agree. The lowest seq wins."""
    ordered = sorted(posts, key=lambda p: p.seq)
    latest: dict[str, dict[str, str]] = {}
    for post in ordered:
        if post.kind in ("agree", "object") and post.ref_id:
            latest.setdefault(post.ref_id, {})[post.author] = post.kind
    out: list[ProposalEvaluation] = []
    adopted: str | None = None
    for post in ordered:
        if post.kind != "proposal" or post.closed is not None:
            continue
        pid = post.proposal_id
        stances = {label: "agree" if label == post.author
                   else latest.get(pid, {}).get(label, "none") for label in quorum}
        eligible = (post.round or 0) < ended_round
        blocking = next(((lb, st) for lb, st in stances.items() if st != "agree"), None)
        qualifies = eligible and blocking is None
        wins = qualifies and adopted is None
        if wins:
            adopted = pid
        if wins:
            reason = "adopted"
        elif qualifies:
            reason = "an earlier proposal was adopted"
        elif not eligible:
            reason = "posted this round"
        else:
            assert blocking is not None
            reason = (f"{blocking[0]} objects" if blocking[1] == "object"
                      else f"{blocking[0]} has no stance")
        out.append(ProposalEvaluation(id=pid, round=post.round or 0, stances=stances,
                                      eligible=eligible, adopted=wins, reason=reason))
    return Evaluation(round=ended_round, proposals=tuple(out), adopted=adopted)
