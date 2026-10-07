"""Adoption at round end (spec v2 §8.3) and the vote round's tally (spec 2026-10-07 §4) —
pure, so both are testable without a team."""
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
    qualifying: tuple[str, ...] = ()   # every unanimous, eligible, open proposal

    def as_payload(self) -> dict[str, object]:
        return {"round": self.round, "adopted": self.adopted,
                "qualifying": list(self.qualifying),
                "proposals": [p.as_payload() for p in self.proposals]}

    def summary(self) -> str:
        return evaluation_text(self.as_payload())


def evaluation_text(payload: dict[str, object]) -> str:
    """One line for a stored round_ended payload — team_status and the trace read it back."""
    vote = payload.get("vote")
    if isinstance(vote, dict):
        raw_counts = vote.get("counts")
        counts = raw_counts if isinstance(raw_counts, dict) else {}
        tally = ", ".join(f"{pid} {n}" for pid, n in counts.items())
        winner = vote.get("winner")
        return (f"vote: {tally} → {winner} adopted" if winner
                else f"vote: {tally} → tied, the main agent decides")
    raw = payload.get("proposals")
    proposals = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    if not proposals:
        return "no open proposals"
    return "; ".join(f"{p['id']} adopted" if p.get("adopted") else
                     f"{p['id']} not adopted: {p.get('reason', '')}" for p in proposals)


def evaluate_round(posts: list[TeamPost], quorum: list[str], ended_round: int) -> Evaluation:
    """A proposal qualifies when it is open, was posted before the ended round started,
    and every quorum member's latest stance on it is agree. Exactly one qualifying proposal
    is adopted; two or more go to a vote (spec 2026-10-07 §4)."""
    ordered = sorted(posts, key=lambda p: p.seq)
    latest: dict[str, dict[str, str]] = {}
    for post in ordered:
        if post.kind in ("agree", "object") and post.ref_id:
            latest.setdefault(post.ref_id, {})[post.author] = post.kind
    rows: list[tuple[TeamPost, dict[str, str], bool, tuple[str, str] | None]] = []
    for post in ordered:
        if post.kind != "proposal" or post.closed is not None:
            continue
        pid = post.proposal_id
        stances = {label: "agree" if label == post.author
                   else latest.get(pid, {}).get(label, "none") for label in quorum}
        eligible = (post.round or 0) < ended_round
        blocking = next(((lb, st) for lb, st in stances.items() if st != "agree"), None)
        rows.append((post, stances, eligible, blocking))
    qualifying = tuple(post.proposal_id for post, _s, eligible, blocking in rows
                       if eligible and blocking is None)
    adopted = qualifying[0] if len(qualifying) == 1 else None
    out: list[ProposalEvaluation] = []
    for post, stances, eligible, blocking in rows:
        pid = post.proposal_id
        if pid == adopted:
            reason = "adopted"
        elif pid in qualifying:
            reason = "tied — vote next round"
        elif not eligible:
            reason = "posted this round"
        else:
            assert blocking is not None
            reason = (f"{blocking[0]} objects" if blocking[1] == "object"
                      else f"{blocking[0]} has no stance")
        out.append(ProposalEvaluation(id=pid, round=post.round or 0, stances=stances,
                                      eligible=eligible, adopted=pid == adopted, reason=reason))
    return Evaluation(round=ended_round, proposals=tuple(out), adopted=adopted,
                      qualifying=qualifying)


@dataclass(frozen=True)
class VoteTally:
    round: int
    between: tuple[str, ...]
    counts: dict[str, int]
    votes: tuple[tuple[str, str, str], ...]   # (voter, proposal id, note), by voter
    winner: str | None

    def as_payload(self) -> dict[str, object]:
        return {"between": list(self.between), "counts": dict(self.counts),
                "votes": [{"label": label, "proposal_id": pid, "note": note}
                          for label, pid, note in self.votes],
                "winner": self.winner}


def tally_votes(posts: list[TeamPost], between: tuple[str, ...],
                voters: list[str] | tuple[str, ...], vote_round: int) -> VoteTally:
    """Each voter's latest vote in the vote round counts. A unique top count wins; a tie or
    no votes at all is the main agent's call (spec 2026-10-07 §2)."""
    latest: dict[str, TeamPost] = {}
    for post in sorted(posts, key=lambda p: p.seq):
        if (post.kind == "vote" and post.round == vote_round and post.author in voters
                and post.ref_id in between):
            latest[post.author] = post
    counts = {pid: 0 for pid in between}
    for post in latest.values():
        counts[str(post.ref_id)] += 1
    top = max(counts.values(), default=0)
    leaders = [pid for pid, n in counts.items() if n == top]
    winner = leaders[0] if top > 0 and len(leaders) == 1 else None
    votes = tuple((label, str(p.ref_id), str(p.payload.get("note", "")))
                  for label, p in sorted(latest.items()))
    return VoteTally(vote_round, tuple(between), counts, votes, winner)
