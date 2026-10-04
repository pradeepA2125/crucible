"""Folding undelivered notices into the next main turn (spec §5.2 path 3)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import NoticeRecord
from agentd.subagents.notices import build_fold
from tests.test_background_dispatch import _setup

ANSWER = {"type": "answer", "thought": "t", "answer": "ok"}


def _n(nid: str, report: str, label: str = "scout") -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id="t", source_kind="agent", source_id=f"a-{nid}",
                        kind="agent_finished",
                        payload={"label": label, "name": "explore", "status": "completed",
                                 "report": report, "files_changed": []},
                        delivery="notify", created_at=datetime.now(UTC))


def test_fold_respects_the_budget_without_truncating() -> None:
    fold = build_fold([_n("1", "short"), _n("2", "x" * 40_000, "big")], max_tokens=8000)
    assert fold.folded == ["1"] and [n.notice_id for n in fold.overflow] == ["2"]
    assert "While you were away:" in fold.text and "Also finished: big (explore)" in fold.text
    assert "x" * 100 not in fold.text


def test_empty_fold() -> None:
    assert build_fold([], max_tokens=8000).text == ""


@pytest.mark.asyncio
async def test_first_turn_seed_holds_the_user_message(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    await ctrl.handle_message(tid, "hello", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    assert history[0] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_next_turn_folds_and_delivers(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    n = _n("1", "the cache is cold")
    store.insert_notice(n.model_copy(update={"thread_id": tid}))
    await ctrl.handle_message(tid, "what happened?", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    first = str(history[0]["content"])
    assert first.startswith("While you were away:") and first.endswith("what happened?")
    assert store.unclaimed_notices(tid) == []
    row = store._conn.execute("SELECT delivered_at FROM agent_notices").fetchone()
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_overflow_reports_hold_the_turn_open_at_most_five_times(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_NOTICE_FOLD_MAX_TOKENS", "10")
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER] * 8, {})
    for i in range(7):
        store.insert_notice(_n(str(i), f"report {i} " + "y" * 200).model_copy(
            update={"thread_id": tid}))
    await ctrl.handle_message(tid, "status?", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    redirects = [m for m in history if "More agent reports are arriving" in str(m.get("content"))]
    assert len(redirects) == 5
    # Every report the turn did not read is offered again.
    delivered = store._conn.execute(
        "SELECT COUNT(*) AS c FROM agent_notices WHERE delivered_at IS NOT NULL").fetchone()["c"]
    assert delivered + len(store.unclaimed_notices(tid)) == 7
