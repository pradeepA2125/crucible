"""agent_notices: claim, deliver on persist, release (spec §5.1, §5.2)."""
from datetime import UTC, datetime
from pathlib import Path

from agentd.chat.models import AgentRecord, ChatMessage, NoticeRecord
from agentd.chat.storage import ChatThreadStore


def _store(tmp_path: Path) -> tuple[ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    return store, store.create_thread(str(tmp_path), title="t").thread_id


def _notice(nid: str, tid: str, source: str = "a1") -> NoticeRecord:
    return NoticeRecord(notice_id=nid, thread_id=tid, source_kind="agent", source_id=source,
                        kind="agent_finished", payload={"report": "r"}, delivery="notify",
                        created_at=datetime.now(UTC))


def test_claim_deliver_release(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_agent(AgentRecord(agent_id="a1", thread_id=tid, turn_id="u", depth=1,
                                   name="explore", label="a", prompt="p", status="completed"))
    for nid in ("n1", "n2"):
        store.insert_notice(_notice(nid, tid))
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n1", "n2"]
    store.claim_notices(["n1"], "turn-1", 3)
    store.claim_notices(["n2"], "turn-2", 3)
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == []
    assert store.claimed_agent_sources(tid) == {"a1"}
    delivered = store.deliver_claimed_notices(tid, "turn-1")
    assert [n.notice_id for n in delivered] == ["n1"]
    assert store.get_agent("a1").report_delivered_at is not None  # type: ignore[union-attr]
    assert store.release_claimed_notices(tid, "turn-1") == 0      # delivered stays delivered
    assert store.release_claimed_notices(tid, "turn-2") == 1
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n2"]


def test_startup_release(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_notice(_notice("n1", tid))
    store.claim_notices(["n1"], "turn-1", 0)
    assert store.release_all_unpersisted_notices() == 1
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n1"]


def test_message_metadata_and_move(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    first = store.append_message(tid, ChatMessage(role="user", content="queued"))
    store.append_message(tid, ChatMessage(role="agent", content="reply"))
    assert first is not None
    assert store.set_message_metadata(tid, first, {"checkpoint_anchor": "m0"})
    assert store.move_message_to_end(tid, first)
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert [m.content for m in messages] == ["reply", "queued"]
    assert messages[-1].id == first and messages[-1].metadata == {"checkpoint_anchor": "m0"}
    assert not store.move_message_to_end(tid, "missing")


def test_notice_message_type_round_trips(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.append_message(tid, ChatMessage(role="agent", content="🔔", type="notice",
                                          metadata={"target": {"agent_id": "a1"}}))
    assert store.get_thread(tid).messages[-1].type == "notice"  # type: ignore[union-attr]
