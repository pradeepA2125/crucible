"""Adoption is evaluated only at round end (spec v2 §8.3)."""
from __future__ import annotations

from datetime import UTC, datetime

from agentd.teams.adoption import evaluate_round, evaluation_text, tally_votes
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


def test_several_qualifying_go_to_a_vote_and_closed_are_skipped() -> None:
    posts = [_post(1, "main", "proposal", round_=0, closed="superseded"),
             _post(2, "alice", "proposal", round_=0), _post(3, "alice", "proposal", round_=0),
             _post(4, "bob", "agree", ref="P2"), _post(5, "bob", "agree", ref="P3")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted is None and ev.qualifying == ("P2", "P3")
    assert [p.id for p in ev.proposals] == ["P2", "P3"]
    assert {p.reason for p in ev.proposals} == {"tied — vote next round"}
    assert ev.as_payload()["qualifying"] == ["P2", "P3"]


def test_one_qualifying_is_adopted() -> None:
    posts = [_post(2, "alice", "proposal", round_=0), _post(3, "alice", "proposal", round_=0),
             _post(4, "bob", "agree", ref="P2"), _post(5, "bob", "object", ref="P3")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted == "P2" and ev.qualifying == ("P2",)


def _vote(seq, author, ref, round_=2, note="why"):  # type: ignore[no-untyped-def]
    post = _post(seq, author, "vote", ref=ref, round_=round_)
    return post.model_copy(update={"payload": {"note": note}})


def test_unique_top_count_wins() -> None:
    posts = [_vote(10, "alice", "P4"), _vote(11, "bob", "P7"), _vote(12, "carol", "P7")]
    tally = tally_votes(posts, ("P4", "P7"), ["alice", "bob", "carol"], 2)
    assert tally.winner == "P7" and tally.counts == {"P4": 1, "P7": 2}
    assert tally.as_payload()["votes"][0] == {"label": "alice", "proposal_id": "P4", "note": "why"}


def test_latest_vote_counts() -> None:
    posts = [_vote(10, "alice", "P4"), _vote(11, "alice", "P7"), _vote(12, "bob", "P7")]
    tally = tally_votes(posts, ("P4", "P7"), ["alice", "bob"], 2)
    assert tally.counts == {"P4": 0, "P7": 2} and tally.winner == "P7"


def test_a_tie_or_no_votes_has_no_winner() -> None:
    tied = tally_votes([_vote(10, "alice", "P4"), _vote(11, "bob", "P7")],
                       ("P4", "P7"), ["alice", "bob"], 2)
    assert tied.winner is None
    assert tally_votes([], ("P4", "P7"), ["alice", "bob"], 2).winner is None


def test_votes_from_another_round_or_non_voter_are_ignored() -> None:
    posts = [_vote(10, "alice", "P4", round_=1), _vote(11, "dave", "P4"), _vote(12, "bob", "P7")]
    tally = tally_votes(posts, ("P4", "P7"), ["alice", "bob"], 2)
    assert tally.counts == {"P4": 0, "P7": 1} and tally.winner == "P7"


def test_evaluation_text_for_a_vote() -> None:
    won = {"vote": {"counts": {"P4": 1, "P7": 2}, "winner": "P7"}}
    assert evaluation_text(won) == "vote: P4 1, P7 2 → P7 adopted"
    tied = {"vote": {"counts": {"P4": 1, "P7": 1}, "winner": None}}
    assert evaluation_text(tied) == "vote: P4 1, P7 1 → tied, the main agent decides"
