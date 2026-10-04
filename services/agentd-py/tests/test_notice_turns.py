"""Notice turns (spec §5.3)."""
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import NoticeRecord, PendingGate
from agentd.chat.rewind import RewindStore
from tests.test_background_dispatch import _setup

ANSWER = {"type": "answer", "thought": "t", "answer": "Your agent finished."}


def _wake(tid: str, nid: str = "n1") -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id=tid, source_kind="agent", source_id="a1",
                        kind="agent_finished",
                        payload={"label": "migrate", "name": "general-purpose",
                                 "status": "completed", "report": "done", "files_changed": []},
                        delivery="wake", created_at=datetime.now(UTC))


async def _settle(ctrl, tid: str) -> None:  # type: ignore[no-untyped-def]
    for _ in range(400):
        await asyncio.sleep(0.01)
        if tid not in ctrl._wake_timers and tid not in ctrl._active_turns:
            return


@pytest.mark.asyncio
async def test_a_wake_notice_starts_a_notice_turn(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    # The test controller has no rewind store; a notice turn opens a checkpoint when one exists.
    ctrl._rewind = RewindStore(store, Path(ctrl._workspace_path))
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert messages[0].type == "notice" and messages[0].metadata["target"] == {"agent_id": "a1"}
    assert store.get_checkpoint_by_anchor(tid, messages[0].id) is not None
    row = store._conn.execute("SELECT delivered_at FROM agent_notices").fetchone()
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_a_pending_main_gate_blocks_and_suppression_blocks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    store.insert_notice(_wake(tid))
    store.add_controller_gate(tid, PendingGate(gate_id="g", kind="clarify", payload={}))
    ctrl._rearm_notices(tid)
    assert tid not in ctrl._wake_timers
    store.clear_main_gates(tid)
    ctrl._wakes_suppressed.add(tid)
    ctrl._rearm_notices(tid)
    assert tid not in ctrl._wake_timers


@pytest.mark.asyncio
async def test_the_wake_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_WAKE_TURNS", "1")
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER, ANSWER], {})
    store.insert_notice(_wake(tid, "n1"))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    store.insert_notice(_wake(tid, "n2"))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n2"]
    crumbs = [m for m in store.get_thread(tid).messages  # type: ignore[union-attr]
              if "next message" in m.content]
    assert len(crumbs) == 1


@pytest.mark.asyncio
async def test_a_user_message_takes_the_batch(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)          # the real 2-second window
    assert tid in ctrl._wake_timers
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}")
    assert tid not in ctrl._wake_timers
    assert not any(m.type == "notice" for m in store.get_thread(tid).messages)  # type: ignore[union-attr]
    assert store.unclaimed_notices(tid) == []
