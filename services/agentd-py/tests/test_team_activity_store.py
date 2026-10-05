"""Team activity record (spec 2026-10-05 §4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import AgentInfo, TeamService


def _setup(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    now = datetime.now(UTC)
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id="t1", name="auth", goal="Add login", max_rounds=3,
        round_started_at=now, approval_gate=False, budget=160, created_turn_id="turn",
        checkpoint_seq=0, created_at=now))
    for label in ("alice", "bob"):
        store.teams.add_member(TeamMember(team_id="team-1", agent_id=f"a-{label}", label=label))
    seen: list[tuple[str, int]] = []
    svc = TeamService(store.teams, tmp_path, lambda aid: AgentInfo("x", "d", "idle"),
                      on_activity=lambda team, ev: seen.append((ev.kind, ev.aseq)))
    return store, svc, seen


def test_aseq_is_per_team_and_independent_of_post_seq(tmp_path: Path) -> None:
    store, svc, seen = _setup(tmp_path)
    store.teams.append_post("team-1", author="main", kind="proposal", text="P", round=0)
    a = svc.record("team-1", "alice", "woke", activation=1, cause_seq=1,
                   payload={"cause": "kickoff", "post_seq": 1})
    b = svc.record("team-1", "bob", "woke", activation=1, cause_seq=1,
                   payload={"cause": "kickoff", "post_seq": 1})
    assert (a.aseq, b.aseq) == (1, 2)
    assert store.teams.append_post("team-1", author="alice", kind="post", text="x").seq == 2
    assert [e.aseq for e in store.teams.activity("team-1")] == [1, 2]
    assert [e.aseq for e in store.teams.activity("team-1", since_aseq=1)] == [2]
    assert seen == [("woke", 1), ("woke", 2)]
    got = store.teams.activity("team-1")[0]
    assert (got.label, got.activation, got.cause_seq, got.payload["cause"]) == (
        "alice", 1, 1, "kickoff")


def test_latest_activity_for_member(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    svc.record("team-1", "alice", "woke", activation=1, payload={"cause": "kickoff"})
    svc.record("team-1", "alice", "took_up", activation=1, payload={"posts": [1], "from": ["main"]})
    svc.record("team-1", "bob", "woke", activation=1, payload={"cause": "kickoff"})
    assert store.teams.latest_activity_for("team-1", "alice").kind == "took_up"
    assert store.teams.latest_activity_for("team-1", "carol") is None


def test_record_is_best_effort(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store, svc, _ = _setup(tmp_path)
    store.teams._conn.execute("DROP TABLE team_activity")
    assert svc.record("team-1", "alice", "woke", payload={"cause": "kickoff"}) is None
    assert "activity" in caplog.text


def test_unknown_kind_is_refused(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    with pytest.raises(ValueError, match="unknown activity kind"):
        store.teams.append_activity("team-1", label="alice", kind="danced")


def test_render_delta_posts_lists_covered_posts_by_others(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    store.teams.append_post("team-1", author="main", kind="proposal", text="P", round=0)
    store.teams.append_post("team-1", author="alice", kind="post", text="mine")
    store.teams.append_post("team-1", author="bob", kind="post", text="@alice hi",
                            mentions=["alice"])
    text, top, posts = svc.render_delta_posts("team-1", "alice")
    assert top == 3
    assert [p.seq for p in posts] == [1, 3]           # her own post is not "handed" to her
    assert "@alice hi" in text
    assert svc.render_delta("team-1", "alice") == (text, top)


def test_activity_never_reaches_member_input(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    store.teams.append_post("team-1", author="main", kind="proposal", text="P", round=0)
    svc.record("team-1", "bob", "wrapped_up", activation=1,
               payload={"status": "completed", "report": "SECRET-REPORT-TEXT"})
    text, _, _ = svc.render_delta_posts("team-1", "alice")
    assert "SECRET-REPORT-TEXT" not in text
    assert "SECRET-REPORT-TEXT" not in svc.status_text("team-1", "alice")
