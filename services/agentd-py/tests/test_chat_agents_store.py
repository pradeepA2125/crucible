"""chat_agents rows (spec §11.3)."""
from datetime import UTC, datetime
from pathlib import Path

from agentd.chat.models import AgentRecord, ChatMessage
from agentd.chat.storage import ChatThreadStore


def _record(agent_id: str, turn_id: str = "turn-1", **kw: object) -> AgentRecord:
    base: dict[str, object] = dict(
        agent_id=agent_id, thread_id="t1", turn_id=turn_id, parent_agent_id=None, depth=1,
        name="general-purpose", label=agent_id, prompt="do it", status="queued")
    base.update(kw)
    return AgentRecord(**base)


def test_insert_get_update_and_list(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.insert_agent(_record("agent-a"))
    store.insert_agent(_record("agent-b", parent_agent_id="agent-a", depth=2))
    store.insert_agent(_record("agent-c", turn_id="turn-2"))
    now = datetime.now(UTC)
    store.update_agent("agent-a", status="completed", report="R" * 30_000,
                       files_changed=["a.py"], stale_refusals=2, started_at=now, ended_at=now)
    got = store.get_agent("agent-a")
    assert got is not None
    assert (got.status, got.files_changed, got.stale_refusals) == ("completed", ["a.py"], 2)
    assert got.report == "R" * 30_000  # never truncated
    assert got.started_at == now and got.ended_at == now
    assert [r.agent_id for r in store.list_agents("t1")] == ["agent-a", "agent-b", "agent-c"]
    assert [r.agent_id for r in store.list_agents("t1", "turn-1")] == ["agent-a", "agent-b"]
    assert store.get_agent("nope") is None


def test_the_transcript_round_trips(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.insert_agent(_record("agent-a"))
    store.set_agent_transcript("agent-a", [
        ChatMessage(role="agent", content="note", metadata={"progress": True, "seq": 3})])
    got = store.get_agent("agent-a")
    assert got is not None and got.transcript[0].content == "note"
    assert got.transcript[0].metadata["seq"] == 3


def test_agent_dispatch_is_a_thread_message_type(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.append_message(tid, ChatMessage(
        role="agent", content="", type="agent_dispatch",
        metadata={"agent_ids": ["agent-a"], "turn_id": "turn-1"}))
    thread = store.get_thread(tid)
    assert thread is not None and thread.messages[-1].type == "agent_dispatch"
