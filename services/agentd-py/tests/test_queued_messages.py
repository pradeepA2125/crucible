"""A message sent during a notice turn is queued and always answered (spec §5.3)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.rewind import RewindStore
from tests.test_background_dispatch import _setup, _tool
from tests.test_notice_turns import _settle, _wake


@pytest.mark.asyncio
async def test_queued_message_is_drained_into_the_notice_turn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    gate = asyncio.Event()
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("list_directory", path="."),
        {"type": "answer", "thought": "t", "answer": "done"}], {})
    original = ctrl._drain_main

    def drain(thread_id: str, turn_id: str):  # type: ignore[no-untyped-def]
        if not gate.is_set():
            mid = ctrl.queue_message(thread_id, "also do X", message_id="m-q", step_review=None,
                                     plan_mode=None, mentioned_files=None, forced_skills=None)
            assert mid == "m-q"
            gate.set()
        return original(thread_id, turn_id)

    monkeypatch.setattr(ctrl, "_drain_main", drain)
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert {"role": "user", "content": "also do X"} in history
    queued = next(m for m in store.get_thread(tid).messages if m.id == "m-q")  # type: ignore[union-attr]
    marker = next(m for m in store.get_thread(tid).messages if m.type == "notice")  # type: ignore[union-attr]
    assert queued.metadata["checkpoint_anchor"] == marker.id


@pytest.mark.asyncio
async def test_an_undrained_queued_message_gets_its_own_turn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        {"type": "answer", "thought": "t", "answer": "notice reply"},
        {"type": "answer", "thought": "t", "answer": "queued reply"}], {})

    queued: list[str] = []

    async def finish_then_queue(*args, **kwargs):  # type: ignore[no-untyped-def]
        # Once, at the notice turn's end: after its last drain, before _end_turn.
        if not queued:
            queued.append(ctrl.queue_message(
                tid, "later", message_id="m-late", step_review=None, plan_mode=None,
                mentioned_files=None, forced_skills=None))
        return await real_finish(*args, **kwargs)

    real_finish = ctrl._finish
    monkeypatch.setattr(ctrl, "_finish", finish_then_queue)
    ctrl._rewind = RewindStore(store, Path(ctrl._workspace_path))
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    await _settle(ctrl, tid)
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    ids = [m.id for m in messages]
    assert ids.index("m-late") > ids.index(next(m.id for m in messages if m.type == "notice"))
    assert store.get_checkpoint_by_anchor(tid, "m-late") is not None
    assert messages[-1].content == "queued reply"


def test_a_user_turn_does_not_accept_queued(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _store, tid = _setup(tmp_path, monkeypatch, [], {})
    ctrl._turn_kinds[tid] = "user"
    assert not ctrl.accepts_queued(tid)


def test_rewind_anchor_follows_checkpoint_anchor(tmp_path: Path) -> None:
    from agentd.chat.models import ChatMessage
    from agentd.chat.rewind import resolve_rewind_anchor
    from agentd.chat.storage import ChatThreadStore

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.append_message(tid, ChatMessage(role="user", content="q", id="m1",
                                          metadata={"checkpoint_anchor": "marker"}))
    assert resolve_rewind_anchor(store, tid, "m1") == "marker"
    assert resolve_rewind_anchor(store, tid, "other") == "other"
