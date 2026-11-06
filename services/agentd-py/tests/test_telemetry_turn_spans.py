"""One trace per chat turn, linked continuations, request ids (observability spec §6.1)."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import NoticeRecord
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.telemetry.context import EVAL_TAGS

ANSWER: dict[str, object] = {"type": "answer", "thought": "t", "answer": "done"}


class _Blocking:
    """A reasoning engine that never answers, so the turn can be stopped mid-call."""

    async def create_controller_step(self, **_kwargs: Any) -> dict[str, object]:
        await asyncio.Event().wait()
        return ANSWER


def _controller(tmp_path: Path, engine: Any) -> tuple[ChatController, ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=engine, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster(), retrieval_client=None)
    return ctrl, store, thread.thread_id


def _scripted(*steps: dict[str, object]) -> ScriptedReasoningEngine:
    return ScriptedReasoningEngine(None, [], controller_step_responses=list(steps))


def _turns(exporter: Any) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == "chat.turn"]


def _last_user_id(store: ChatThreadStore, thread_id: str) -> str:
    thread = store.get_thread(thread_id)
    assert thread is not None
    ids = [m.id for m in thread.messages if m.role == "user"]
    assert ids and ids[-1]
    return str(ids[-1])


@pytest.mark.asyncio
async def test_user_turn_is_one_agent_span_in_its_session(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _controller(tmp_path, _scripted(ANSWER))
    await ctrl.handle_message(tid, "hi", channel_id="c1")
    (turn,) = _turns(test_exporter)
    a = turn.attributes
    assert a["openinference.span.kind"] == "AGENT" and a["session.id"] == tid
    assert a["crucible.turn_kind"] == "user"
    assert a["crucible.request_id"] == _last_user_id(store, tid)
    assert a["input.value"] == "hi" and a["output.value"] == "done"
    assert ctrl.latest_user_message_id(tid) == _last_user_id(store, tid)


@pytest.mark.asyncio
async def test_clarify_continuation_links_to_the_user_turn(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    clarify: dict[str, object] = {"type": "clarify", "thought": "t", "question": "which file?",
               "options": ["a.py", "b.py"]}
    ctrl, store, tid = _controller(tmp_path, _scripted(clarify, ANSWER))
    await ctrl.handle_message(tid, "fix it", channel_id="c1")
    await ctrl.resolve_clarify(tid, "a.py", channel_id="c1", goal="fix it")
    first, second = _turns(test_exporter)
    assert first.context.trace_id != second.context.trace_id
    assert second.attributes["crucible.turn_kind"] == "continuation"
    assert [link.context.span_id for link in second.links] == [first.context.span_id]
    assert first.attributes["session.id"] == second.attributes["session.id"] == tid
    assert first.attributes["crucible.request_id"] == second.attributes["crucible.request_id"]


@pytest.mark.asyncio
async def test_notice_turn_keeps_the_episode_request_id(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _controller(tmp_path, _scripted(ANSWER, ANSWER))
    await ctrl.handle_message(tid, "start agents", channel_id="c1")
    store.insert_notice(NoticeRecord(
        notice_id="n1", thread_id=tid, source_kind="agent", source_id="a1",
        kind="agent_finished",
        payload={"label": "x", "name": "explore", "status": "completed", "report": "ok",
                 "files_changed": []},
        delivery="wake", created_at=datetime.now(UTC)))
    await ctrl.handle_notices(tid)
    user, notice = _turns(test_exporter)
    assert notice.attributes["crucible.turn_kind"] == "notice"
    assert notice.attributes["crucible.request_id"] == user.attributes["crucible.request_id"]


@pytest.mark.asyncio
async def test_stopped_turn_ends_with_error_status(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, _store, tid = _controller(tmp_path, _Blocking())
    task = ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id="c1"),
                            channel_id="c1")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if ctrl._active_loops.get(tid) is not None:  # noqa: SLF001
            break
    assert await ctrl.stop_turn(tid) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    (turn,) = _turns(test_exporter)
    assert turn.status.status_code.name == "ERROR"
    assert turn.status.description == "cancelled"


@pytest.mark.asyncio
async def test_turn_trace_json_records_ids_and_times(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _controller(tmp_path, _scripted(ANSWER))
    await ctrl.handle_message(tid, "hi", channel_id="c1")
    (trace_file,) = (tmp_path / ".crucible/state/artifacts/chat" / tid).glob("*/turn-trace.json")
    data = json.loads(trace_file.read_text())
    (turn,) = _turns(test_exporter)
    assert data["trace_id"] == format(turn.context.trace_id, "032x")
    assert data["span_id"] == format(turn.context.span_id, "016x")
    assert data["started_at"] <= data["ended_at"]
    assert data["turn_kind"] == "user"
    assert data["request_id"] == _last_user_id(store, tid)


@pytest.mark.asyncio
async def test_turn_trace_json_without_tracing_has_null_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, _store, tid = _controller(tmp_path, _scripted(ANSWER))
    await ctrl.handle_message(tid, "hi", channel_id="c1")
    (trace_file,) = (tmp_path / ".crucible/state/artifacts/chat" / tid).glob("*/turn-trace.json")
    data = json.loads(trace_file.read_text())
    assert data["trace_id"] is None and data["span_id"] is None
    assert data["turn_kind"] == "user" and data["started_at"]


@pytest.mark.asyncio
async def test_eval_tags_land_on_the_turn_span(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, _store, tid = _controller(tmp_path, _scripted(ANSWER))
    token = EVAL_TAGS.set({"crucible.eval_run_id": "r1", "crucible.eval_case_id": "c1"})
    try:
        await ctrl.handle_message(tid, "hi", channel_id="c1")
    finally:
        EVAL_TAGS.reset(token)
    (turn,) = _turns(test_exporter)
    assert turn.attributes["crucible.eval_run_id"] == "r1"
    assert turn.attributes["crucible.eval_case_id"] == "c1"
