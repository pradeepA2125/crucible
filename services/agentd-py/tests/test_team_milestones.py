"""Milestone bodies are compact (spec v2 §8.8); the trace is append-only (§8.11)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from agentd.chat.models import NoticeRecord
from agentd.subagents.notices import notice_author, notice_body
from agentd.teams.milestones import milestone_text
from agentd.teams.models import TeamRecord
from agentd.teams.trace import CoordinatorTrace


def _team(**over) -> TeamRecord:  # type: ignore[no-untyped-def]
    base = dict(team_id="team-1", thread_id="t", name="auth", goal="g", max_rounds=3,
                budget=160, requests=42, created_turn_id="u", created_at=datetime.now(UTC),
                phase="DEADLOCKED", round=3)
    base.update(over)
    return TeamRecord(**base)


def test_deadlock_body_lists_counts_and_next_steps() -> None:
    headline, body = milestone_text(_team(), "deadlock", {
        "round": 3, "proposals": [{"id": "P1", "stances": {"alice": "agree", "bob": "none"}}]})
    assert headline == "Team 'auth' deadlocked after 3 rounds"
    assert "P1: 1 agree, 0 object, 1 no stance" in body
    assert "42 / 160 requests" in body
    assert "adopt_proposal" in body and "post_board" in body


def test_adopted_body_summarizes_assignments() -> None:
    headline, body = milestone_text(_team(phase="DONE"), "adopted", {
        "proposal_id": "P2", "by": "team",
        "assignments": [{"member": "alice", "part": "api", "files": ["a.py", "b.py"]}]})
    assert headline == "Team 'auth' adopted P2"
    assert "alice → api (2 files)" in body


def test_member_lost_and_ended() -> None:
    assert milestone_text(_team(), "member_lost", {"label": "bob", "status": "failed"})[0] == (
        "Team 'auth' lost bob (failed)")
    assert milestone_text(_team(phase="FAILED"), "ended", {"reason": "x"})[0] == (
        "Team 'auth' ended — x")


def test_team_notice_author_and_body() -> None:
    notice = NoticeRecord(notice_id="n", thread_id="t", source_kind="team", source_id="team-1",
                          kind="deadlock", payload={"team_name": "auth", "body": "BODY"},
                          delivery="wake", created_at=datetime.now(UTC))
    assert notice_author(notice) == "team auth"
    assert notice_body(notice) == "BODY"


def test_trace_appends_lines(tmp_path) -> None:
    trace = CoordinatorTrace(tmp_path / "x" / "coordinator.jsonl")
    trace.write("event", name="Kickoff")
    trace.write("evaluation", round=1)
    lines = (tmp_path / "x" / "coordinator.jsonl").read_text().splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["event", "evaluation"]


def test_adopted_body_names_what_happens_next() -> None:
    data = {"proposal_id": "P2", "by": "team",
            "assignments": [{"member": "alice", "part": "api", "files": ["a.py"]}]}
    _, implementing = milestone_text(_team(phase="IMPLEMENTING"), "adopted",
                                     {**data, "next": "implementing"})
    assert "The members are implementing it" in implementing
    _, ended = milestone_text(_team(phase="DONE"), "adopted", {**data, "next": "ended"})
    assert "no assignments" in ended


def test_approval_needed_and_paused_and_stuck() -> None:
    headline, body = milestone_text(_team(phase="AWAITING_APPROVAL"), "approval_needed",
                                    {"proposal_id": "P4"})
    assert headline == "Team 'auth' needs your approval for P4"
    assert "a card is waiting for the user" in body
    headline, body = milestone_text(_team(phase="PAUSED", paused_reason="budget"), "paused",
                                    {"reason": "budget", "paused_from": "IMPLEMENTING",
                                     "done": 1, "total": 3, "used": 161})
    assert headline == "Team 'auth' paused — its request budget ran out"
    assert "1 of 3 assignments done" in body and "resume_team" in body
    assert "161 / 160 requests" in body
    _, deliberating = milestone_text(_team(phase="PAUSED"), "paused",
                                     {"reason": "budget", "paused_from": "DELIBERATING",
                                      "done": 0, "total": 0})
    assert "Paused from DELIBERATING." in deliberating and "assignments" not in deliberating
    _, body = milestone_text(_team(phase="IMPLEMENTING"), "stuck",
                             {"idle": [["alice", "awaiting_peer"], ["bob", "partial"]],
                              "count": 2})
    assert "alice: awaiting_peer" in body and "bob: partial" in body and "2 of 3" in body


def test_member_blocked_quotes_300_chars_and_points_to_the_rest() -> None:
    _, body = milestone_text(_team(phase="IMPLEMENTING"), "member_blocked",
                             {"label": "bob", "status": "partial", "report": "y" * 300})
    assert "bob reported partial" in body and "y" * 300 in body
    assert "post_board" in body


def test_done_body_names_the_review() -> None:
    headline, body = milestone_text(_team(phase="DONE"), "done", {
        "reason": "reviewed", "files": ["a.py", "b.py"], "adopted": "P3", "closing": "P9",
        "cycle": 2, "abstained": ["review"], "unresolved": []})
    assert headline == "Team 'auth' finished — the review agreed"
    assert "P3 implemented; closing proposal P9 (review cycle 2)." in body
    assert "Abstained: review" in body and "a.py, b.py" in body
    headline, body = milestone_text(_team(phase="DONE"), "done", {
        "reason": "unresolved objections", "files": [], "adopted": "P3", "closing": "P9",
        "cycle": 1, "abstained": [],
        "unresolved": [{"seq": 12, "label": "tests", "reason": "refresh is unlimited"}]})
    assert headline == "Team 'auth' finished with unresolved objections"
    assert "- #12 tests: refresh is unlimited" in body
    assert "what is still open" in body


def test_stuck_body_names_waits_and_how_to_restart() -> None:
    _, body = milestone_text(_team(phase="IMPLEMENTING"), "stuck", {
        "idle": [["tests", "awaiting_peer"], ["api", "completed"]], "count": 1,
        "waits": {"tests": ["api"]}})
    assert "- tests: awaiting_peer (waiting on api)" in body
    assert 'post_board mentioning the member, e.g. "@tests' in body
    assert "leaves its assignment open" in body


def test_deadlock_points_at_an_unobjected_proposal() -> None:
    _, body = milestone_text(_team(), "deadlock", {
        "round": 3, "proposals": [{"id": "P7", "stances": {"alice": "agree", "bob": "agree",
                                                            "carol": "none"}}]})
    assert ("P7 has no objection: adopt_proposal adopts it now; post_board only runs "
            "another round.") in body
    _, objected = milestone_text(_team(), "deadlock", {
        "round": 3, "proposals": [{"id": "P7", "stances": {"alice": "agree", "bob": "object"}}]})
    assert "has no objection" not in objected

