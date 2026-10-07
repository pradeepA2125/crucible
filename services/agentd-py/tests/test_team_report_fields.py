"""Stances and proposals carried on a member's report (spec v2 §8.3)."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.report_fields import ReportFields
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.store import TeamStore


def _svc(tmp_path: Path) -> tuple[TeamService, TeamStore, str]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    now = datetime.now(UTC)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=3, budget=100, created_turn_id="u",
                                 created_at=now, round=2))
    for label in ("alice", "bob"):
        store.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}", label=label))
    store.append_post("team-1", author="main", kind="proposal", text="P1 plan", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    (tmp_path / "a.py").write_text("x = 1\n")
    svc = TeamService(store, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    return svc, store, "team-1"


def _report(**fields):  # type: ignore[no-untyped-def]
    return {"type": "report", "thought": "t", "summary": "s", "status": "completed", **fields}


def test_expected_stances(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    assert svc.expected_stances(tid, "alice") == ["P1"]
    svc.agree(tid, "alice", "P1")
    assert svc.expected_stances(tid, "alice") == []
    store.update_team(tid, phase="DEADLOCKED")
    assert svc.expected_stances(tid, "bob") == []


def test_missing_stance_redirects_once(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    first = check(_report(), False)
    assert first.message is not None and "P1" in first.message and not first.malformed
    assert '"stances"' in first.message
    assert check(_report(), False).message is None          # accepted the second time
    assert [p.kind for p in store.posts(tid)] == ["proposal"]   # nothing posted


def test_valid_stances_and_proposal_are_posted(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    verdict = check(_report(
        stances=[{"proposal_id": "P1", "stance": "object", "reason": "misses a.py",
                  "evidence": {"files": ["a.py"], "line": 1}}],
        proposal={"text": "P2 plan", "assignments": [
            {"member": "alice", "part": "api", "files": ["a.py"]}]}), False)
    assert verdict.message is None
    kinds = [(p.author, p.kind, p.ref_id) for p in store.posts(tid)]
    assert kinds[1:] == [("alice", "object", "P1"), ("alice", "proposal", None)]
    assert store.posts(tid)[-1].round == 2


def test_invalid_entry_refused_then_identical_is_malformed(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    bad = _report(stances=[{"proposal_id": "P9", "stance": "agree"}])
    first = check(bad, False)
    assert first.message is not None and "P9" in first.message and not first.malformed
    assert check(bad, False).malformed is True
    assert len(store.posts(tid)) == 1


def test_invalid_entries_dropped_on_final(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    dropped: list[list[str]] = []
    check = ReportFields(svc, tid, "alice", ActivationCounters(), on_dropped=dropped.append)
    verdict = check(_report(stances=[{"proposal_id": "P9", "stance": "agree"},
                                     {"proposal_id": "P1", "stance": "agree", "note": "ok"}]),
                    True)
    assert verdict.message is None
    assert [(p.kind, p.ref_id) for p in store.posts(tid)][1:] == [("agree", "P1")]
    assert dropped and "P9" in dropped[0][0]


def test_delta_stops_at_the_round_cutoff(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    store.append_post(tid, author="alice", kind="post", text="late news", round=2)
    text, top, handed = svc.render_delta_posts(tid, "bob", until_seq=1)
    assert top == 1 and [p.seq for p in handed] == [1] and "late news" not in text
    text, top, _ = svc.render_delta_posts(tid, "bob")
    assert top == 2 and "late news" in text


def test_round_headers(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    text, _top = svc.render_delta(tid, "bob")
    assert text.startswith("Round 2 of 3: others have posted their views.")
    store.update_team(tid, round=1)
    text, _top = svc.render_delta(tid, "bob")
    assert text.startswith("Round 1 of 3: the main agent proposed P1.")


def test_a_report_does_not_repeat_a_stance_posted_this_round(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    svc.object_(tid, "alice", "P1", "misses a.py", {"files": ["a.py"], "line": 1})
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    again = {"proposal_id": "P1", "stance": "object", "reason": "misses a.py",
             "evidence": {"files": ["a.py"], "line": 1}}
    verdict = check(_report(stances=[again]), False)
    assert verdict.message is None
    assert [p.kind for p in store.posts(tid)] == ["proposal", "object"]   # no second copy
    # A changed stance still posts.
    check2 = ReportFields(svc, tid, "alice", ActivationCounters())
    changed = _report(stances=[{"proposal_id": "P1", "stance": "agree"}])
    assert check2(changed, False).message is None
    assert [p.kind for p in store.posts(tid)] == ["proposal", "object", "agree"]


def _vote_round(store, tid) -> None:  # type: ignore[no-untyped-def]
    """bob's P2 and P3 tie with main's P1; round 2 is the vote."""
    for _ in range(2):
        store.append_post(tid, author="bob", kind="proposal", text="bob plan", round=1,
                          payload={"assignments": [], "shared_files": [], "supersedes": []})
    store.update_team(tid, vote_between="P1,P2,P3")


def test_report_vote_is_posted(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    assert check(_report(vote={"proposal_id": "P2", "note": "smaller"}), False).message is None
    votes = [p for p in store.posts(tid) if p.kind == "vote"]
    assert [(p.author, p.ref_id, p.text) for p in votes] == [("alice", "P2", "smaller")]


def test_missing_vote_redirects_once(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    first = check(_report(), False)
    assert first.message is not None and '"vote"' in first.message
    assert "P1, P2, P3" in first.message and not first.malformed
    assert check(_report(), False).message is None


def test_a_member_with_no_candidate_is_not_asked_to_vote(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    for _ in range(2):
        store.append_post(tid, author="bob", kind="proposal", text="bob plan", round=1,
                          payload={"assignments": [], "shared_files": [], "supersedes": []})
    store.update_team(tid, vote_between="P2,P3")
    assert ReportFields(svc, tid, "bob", ActivationCounters())(_report(), False).message is None


def test_invalid_vote_refused_then_dropped_on_final(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    dropped: list[list[str]] = []
    check = ReportFields(svc, tid, "bob", ActivationCounters(), on_dropped=dropped.append)
    refused = check(_report(vote={"proposal_id": "P2"}), False)
    assert refused.message is not None and "own proposal" in refused.message
    assert check(_report(vote={"proposal_id": "P2"}), True).message is None
    assert dropped and not [p for p in store.posts(tid) if p.kind == "vote"]


def test_stances_in_a_vote_round_are_refused(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    verdict = ReportFields(svc, tid, "alice", ActivationCounters())(
        _report(stances=[{"proposal_id": "P1", "stance": "agree"}]), False)
    assert verdict.message is not None and "only votes count" in verdict.message


def test_report_vote_repeat_is_not_posted_twice(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    svc.vote(tid, "alice", "P3")
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    assert check(_report(vote={"proposal_id": "P3"}), False).message is None
    assert len([p for p in store.posts(tid) if p.kind == "vote"]) == 1
