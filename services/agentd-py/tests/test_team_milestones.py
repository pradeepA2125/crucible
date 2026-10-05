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
