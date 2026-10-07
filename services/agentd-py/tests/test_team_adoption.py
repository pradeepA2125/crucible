"""Adoption is evaluated only at round end (spec v2 §8.3)."""
from __future__ import annotations

from datetime import UTC, datetime

from agentd.teams.adoption import evaluate_round
from agentd.teams.models import TeamPost


def _post(seq, author, kind, *, ref=None, round_=1, closed=None) -> TeamPost:  # type: ignore[no-untyped-def]
    return TeamPost(team_id="t", seq=seq, author=author, kind=kind, text="x", ref_id=ref,
                    round=round_, closed=closed, created_at=datetime.now(UTC))


def test_kickoff_proposal_adopted_when_every_member_agrees() -> None:
    posts = [_post(1, "main", "proposal", round_=0), _post(2, "alice", "agree", ref="P1"),
             _post(3, "bob", "agree", ref="P1")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted == "P1"
    assert ev.proposals[0].stances == {"alice": "agree", "bob": "agree"}


def test_no_stance_is_not_agreement_and_latest_stance_wins() -> None:
    posts = [_post(1, "main", "proposal", round_=0), _post(2, "alice", "object", ref="P1"),
             _post(3, "alice", "agree", ref="P1")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted is None
    assert ev.proposals[0].stances == {"alice": "agree", "bob": "none"}
    assert ev.summary() == "P1 not adopted: bob has no stance"


def test_proposal_from_this_round_is_not_eligible() -> None:
    posts = [_post(2, "alice", "proposal", round_=1), _post(3, "bob", "agree", ref="P2")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted is None and ev.proposals[0].reason == "posted this round"
    assert evaluate_round(posts, ["alice", "bob"], 2).adopted == "P2"   # author counts as agree


def test_lowest_seq_wins_and_closed_proposals_are_skipped() -> None:
    posts = [_post(1, "main", "proposal", round_=0, closed="superseded"),
             _post(2, "alice", "proposal", round_=0), _post(3, "alice", "proposal", round_=0),
             _post(4, "bob", "agree", ref="P2"), _post(5, "bob", "agree", ref="P3")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted == "P2"
    assert [p.id for p in ev.proposals] == ["P2", "P3"]
    assert ev.as_payload()["adopted"] == "P2"
