"""Plan cards form a version history, with no exact-duplicate of a single version.

Two requirements that must coexist:
  * "no duplicate" — the SAME plan version (identical content) must never appear
    twice (the original double-writer bug: chat agent + orchestrator both wrote v1).
  * "feedback appends" — a regenerated plan (new content) is appended AFTER the old
    one, preserving the plan's evolution in the transcript.

So `append_plan_card` dedups against the task's CURRENT latest version (skip if
identical) but appends a genuinely new version.
"""
from __future__ import annotations

from pathlib import Path

from agentd.chat.storage import ChatThreadStore


def _plan_cards(store: ChatThreadStore, thread_id: str, task_id: str):
    thread = store.get_thread(thread_id)
    assert thread is not None
    return [m for m in thread.messages if m.type == "plan_card" and m.task_id == task_id]


def test_append_plan_card_dedups_version_but_keeps_history(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(tmp_path))
    tid = thread.thread_id

    # First write appends; the duplicate writer (same content) must NOT add a second.
    first = store.append_plan_card(tid, "task-1", "# Plan v1")
    again = store.append_plan_card(tid, "task-1", "# Plan v1")
    assert first is True
    assert again is False
    assert len(_plan_cards(store, tid, "task-1")) == 1

    # Feedback → a NEW version appends AFTER the old; history is preserved (2 cards).
    appended = store.append_plan_card(tid, "task-1", "# Plan v2")
    assert appended is True
    cards = _plan_cards(store, tid, "task-1")
    assert [m.content for m in cards] == ["# Plan v1", "# Plan v2"]
    assert cards[-1].metadata.get("plan_markdown") == "# Plan v2"

    # A different task keeps its own independent history.
    store.append_plan_card(tid, "task-2", "# Other plan")
    assert len(_plan_cards(store, tid, "task-2")) == 1
    assert len(_plan_cards(store, tid, "task-1")) == 2
