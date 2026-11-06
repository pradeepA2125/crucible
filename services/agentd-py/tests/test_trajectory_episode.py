"""Request episodes built from turn traces (observability spec §6.3)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.eval.trajectory.episode import Episode, TurnRecord, build_episode, render_for_judge
from agentd.runtime.artifacts import chat_turn_artifacts_root
from tests.test_background_dispatch import DONE, _setup, _tool
from tests.test_telemetry_turn_spans import _controller, _scripted

T0 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _trace(ws: Path, tid: str, turn_id: str, *, kind: str, started: datetime | None,
           request_id: str | None, text: str, events: int = 1) -> Path:
    out = chat_turn_artifacts_root(tid, turn_id, ws)
    out.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "thread_id": tid, "turn_id": turn_id, "goal": "g", "phase": "ACTIVE",
        "outcome_kind": "answer", "outcome_text": text, "history": [],
        "tool_events": [{"tool": "read_file", "args": {"path": f"f{i}.py"},
                         "output": f"out {i}"} for i in range(events)],
        "thinking_log": []}
    if started is not None:
        data.update({"started_at": started.isoformat(), "ended_at": started.isoformat(),
                     "turn_kind": kind, "request_id": request_id, "trace_id": "t" * 32,
                     "span_id": "s" * 16})
    path = out / "turn-trace.json"
    path.write_text(json.dumps(data))
    return path


def _message(store: ChatThreadStore, tid: str, role: str, content: str, at: datetime) -> str:
    message_id = store.append_message(tid, ChatMessage(role=role, content=content, timestamp=at))
    return str(message_id)


def test_episode_spans_user_and_notice_turns_until_next_user_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    tid = store.create_thread(str(tmp_path), "t").thread_id
    first = _message(store, tid, "user", "build it", T0)
    _message(store, tid, "agent", "agents started", T0 + timedelta(seconds=5))
    _message(store, tid, "agent", "built it", T0 + timedelta(seconds=50))
    second = _message(store, tid, "user", "thanks", T0 + timedelta(minutes=5))
    _trace(tmp_path, tid, "u1", kind="user", started=T0 + timedelta(seconds=1),
           request_id=first, text="agents started")
    _trace(tmp_path, tid, "n1", kind="notice", started=T0 + timedelta(seconds=40),
           request_id=first, text="built it")
    _trace(tmp_path, tid, "u2", kind="user", started=T0 + timedelta(minutes=5, seconds=1),
           request_id=second, text="you're welcome")
    episode = build_episode(store, tmp_path, tid, request_id=first)
    assert [t.turn_id for t in episode.turns] == ["u1", "n1"]
    assert [t.turn_kind for t in episode.turns] == ["user", "notice"]
    assert episode.user_message == "build it" and episode.final_answer == "built it"
    assert episode.request_id == first
    latest = build_episode(store, tmp_path, tid)
    assert [t.turn_id for t in latest.turns] == ["u2"] and latest.request_id == second


def test_old_trace_without_started_at_orders_by_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    tid = store.create_thread(str(tmp_path), "t").thread_id
    _message(store, tid, "user", "hi", datetime.now(UTC) - timedelta(hours=1))
    late = _trace(tmp_path, tid, "b", kind="user", started=None, request_id=None, text="b")
    early = _trace(tmp_path, tid, "a", kind="user", started=None, request_id=None, text="a")
    now = datetime.now(UTC).timestamp()
    os.utime(early, (now - 60, now - 60))
    os.utime(late, (now - 30, now - 30))
    episode = build_episode(store, tmp_path, tid)
    assert [t.turn_id for t in episode.turns] == ["a", "b"]
    assert episode.turns[0].started_at is None


def test_render_drops_middle_tool_events_first() -> None:
    events: list[dict[str, object]] = [
        {"tool": "read_file", "args": {"path": f"file{i}.py"}, "output": "x" * 100}
        for i in range(200)]
    episode = Episode(
        thread_id="t", request_id="m1", user_message="read everything",
        turns=[TurnRecord(turn_id="u1", turn_kind="user", goal="read everything",
                          outcome_kind="answer", outcome_text="done", tool_events=events,
                          started_at=None, trace_id=None, span_id=None)],
        agents=[], final_answer="done", diff="")
    text = render_for_judge(episode, max_chars=5000)
    assert len(text) <= 5000
    assert "file0.py" in text and "file199.py" in text
    assert "tool events omitted]" in text
    assert "Request:\nread everything" in text and "Final answer:\ndone" in text


def test_render_keeps_everything_under_budget() -> None:
    episode = Episode(thread_id="t", request_id=None, user_message="q", turns=[],
                      agents=[], final_answer="a", diff="+line")
    text = render_for_judge(episode)
    assert "omitted" not in text and "Diff:\n+line" in text


@pytest.mark.asyncio
async def test_episode_settled_true_after_the_turn_with_no_background_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, _store, tid = _controller(
        tmp_path, _scripted({"type": "answer", "thought": "t", "answer": "done"}))
    await ctrl.handle_message(tid, "hi", channel_id="c1")
    assert ctrl.episode_settled(tid) is True


@pytest.mark.asyncio
async def test_episode_settled_false_while_an_agent_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "general-purpose", "label": "slow",
                                          "prompt": "run"}]),
        DONE], {"slow": [_tool("run_command", command="sleep", args=["30"])]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if store.list_agents(tid) and store.list_agents(tid)[0].status == "waiting":
            break
    assert ctrl.episode_settled(tid) is False
    await ctrl.stop_all_agents(tid)
