"""Rewind with agents and notices (spec §8.10)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import AgentRecord, NoticeRecord
from tests.test_background_dispatch import _setup


def _agent(tid: str, agent_id: str, seq: int, status: str = "completed",
           files: list[str] | None = None) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id=tid, turn_id="u", depth=1, name="explore",
                       label=agent_id, prompt="p", status=status, dispatcher_id="main",
                       checkpoint_seq=seq, files_changed=files or [])


def _notice(tid: str, nid: str, source: str) -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id=tid, source_kind="agent", source_id=source,
                        kind="agent_finished", payload={"report": "r"}, delivery="notify",
                        created_at=datetime.now(UTC))


def test_live_agent_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    store.insert_agent(_agent(tid, "busy", 0, status="running"))
    store.insert_agent(_agent(tid, "idle", 0))
    assert store.live_agent_labels(tid) == ["busy"]


def test_forget_cleans_notices_and_notes_survivors(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    store.insert_agent(_agent(tid, "old", 0, files=["a.py"]))
    store.insert_agent(_agent(tid, "new", 2))
    store.insert_notice(_notice(tid, "n-old", "old"))
    store.insert_notice(_notice(tid, "n-new", "new"))
    store.claim_notices(["n-old"], "turn-2", 2)
    store.deliver_claimed_notices(tid, "turn-2")
    delivered: list[tuple[str, str]] = []
    ctrl._subagents.deliver = lambda agent_id, item: delivered.append((agent_id, item.text))  # type: ignore[union-attr, method-assign]
    ctrl.forget_rewound_agents(tid, [], 2, restored_files=["a.py"])
    assert store.get_agent("new") is None
    rows = {r["notice_id"]: r for r in store._conn.execute("SELECT * FROM agent_notices")}
    assert "n-new" not in rows
    assert rows["n-old"]["delivered_at"] is None and rows["n-old"]["claimed_turn_id"] is None
    assert store.get_agent("old").report_delivered_at is None  # type: ignore[union-attr]
    assert delivered and delivered[0][0] == "old" and "a.py" in delivered[0][1]
