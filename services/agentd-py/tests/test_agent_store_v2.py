"""v2 chat_agents columns, helpers and the v1-row backfill (spec §3.1)."""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agentd.chat.models import AgentRecord, Checkpoint
from agentd.chat.storage import ChatThreadStore


def _record(agent_id: str, tid: str, parent: str | None = None, **extra: object) -> AgentRecord:
    return AgentRecord(
        agent_id=agent_id, thread_id=tid, turn_id="t1", parent_agent_id=parent,
        depth=1 if parent is None else 2, name="general-purpose", label=agent_id,
        prompt="p", status="queued", **extra)


def _store(tmp_path: Path) -> tuple[ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    return store, store.create_thread(str(tmp_path), title="t").thread_id


def test_v2_fields_round_trip(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_agent(_record(
        "a1", tid, definition={"name": "general-purpose"}, dispatcher_id="main",
        checkpoint_seq=3, inherited={"read_only": True}, on_finish="notify"))
    store.set_agent_history("a1", [{"role": "user", "content": "do it"}])
    started = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
    store.update_agent("a1", activation_count=2, last_seq=7, stop_reason="user",
                       activation_started_at=started)
    rec = store.get_agent("a1")
    assert rec is not None
    assert rec.history == [{"role": "user", "content": "do it"}]
    assert (rec.definition, rec.dispatcher_id, rec.checkpoint_seq) == (
        {"name": "general-purpose"}, "main", 3)
    assert (rec.inherited, rec.on_finish, rec.team_id) == ({"read_only": True}, "notify", None)
    assert (rec.activation_count, rec.last_seq, rec.stop_reason) == (2, 7, "user")
    assert rec.activation_started_at == started


def test_files_changed_merge_is_a_union(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_agent(_record("a1", tid))
    assert store.merge_agent_files("a1", ["b.py", "a.py"]) == ["a.py", "b.py"]
    assert store.merge_agent_files("a1", ["c.py", "a.py"]) == ["a.py", "b.py", "c.py"]
    rec = store.get_agent("a1")
    assert rec is not None and rec.files_changed == ["a.py", "b.py", "c.py"]


def test_report_delivery_is_set_and_cleared(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_agent(_record("a1", tid))
    store.update_agent("a1", report_delivered_at=datetime.now(UTC))
    assert store.get_agent("a1").report_delivered_at is not None  # type: ignore[union-attr]
    store.clear_report_delivered("a1")
    assert store.get_agent("a1").report_delivered_at is None  # type: ignore[union-attr]


def test_subtree_and_children_come_from_the_database(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.insert_agent(_record("a1", tid))
    store.insert_agent(_record("a2", tid, parent="a1"))
    store.insert_agent(_record("a3", tid, parent="a2"))
    store.insert_agent(_record("b1", tid))
    assert store.child_agent_ids("a1") == ["a2"]
    assert store.subtree_agent_ids("a1") == {"a1", "a2", "a3"}
    assert store.subtree_agent_ids("b1") == {"b1"}


def test_current_checkpoint_seq(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    assert store.current_checkpoint_seq(tid) == -1
    for seq in (0, 1):
        store.insert_checkpoint(Checkpoint(
            thread_id=tid, seq=seq, anchor_message_id=f"m{seq}", turn_id=f"t{seq}",
            created_at=datetime.now(UTC)))
    assert store.current_checkpoint_seq(tid) == 1


_V1_AGENTS_DDL = """
CREATE TABLE chat_agents (
    agent_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
    parent_agent_id TEXT, depth INTEGER NOT NULL, name TEXT NOT NULL, label TEXT NOT NULL,
    prompt TEXT NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL DEFAULT '',
    files_changed_json TEXT NOT NULL DEFAULT '[]', stale_refusals INTEGER NOT NULL DEFAULT 0,
    transcript_json TEXT NOT NULL DEFAULT '[]', started_at TEXT, ended_at TEXT)
"""


def test_v1_rows_are_backfilled(tmp_path: Path) -> None:
    db = tmp_path / "c.sqlite3"
    store = ChatThreadStore(db)
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    t0, t2 = "2026-10-01T10:00:00+00:00", "2026-10-01T12:00:00+00:00"
    for seq, at in ((0, t0), (1, t2)):
        store.insert_checkpoint(Checkpoint(
            thread_id=tid, seq=seq, anchor_message_id=f"m{seq}", turn_id=f"t{seq}",
            created_at=datetime.fromisoformat(at)))
    store._conn.close()
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE chat_agents")
    conn.execute(_V1_AGENTS_DDL)
    rows = [  # agent_id, turn_id, parent, started_at
        ("early", "tx", None, "2026-10-01T09:00:00+00:00"),   # before every checkpoint
        ("m1", "t0", None, "2026-10-01T11:00:00+00:00"),       # after cp0
        ("m2", "t1", None, "2026-10-01T13:00:00+00:00"),       # after cp1
        ("m3", "t1", None, None),                              # stopped while queued
        ("c1", "t1", "m2", "2026-10-01T13:05:00+00:00"),       # child of m2
    ]
    for agent_id, turn_id, parent, started in rows:
        conn.execute(
            "INSERT INTO chat_agents (agent_id, thread_id, turn_id, parent_agent_id, depth, "
            "name, label, prompt, status, started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_id, tid, turn_id, parent, 1 if parent is None else 2, "general-purpose",
             agent_id, "p", "completed", started))
    conn.commit()
    conn.close()

    store = ChatThreadStore(db)  # migration adds the columns and backfills
    got = {r.agent_id: (r.dispatcher_id, r.checkpoint_seq) for r in store.list_agents(tid)}
    assert got == {"early": ("main", -1), "m1": ("main", 0), "m2": ("main", 1),
                   "m3": ("main", 1), "c1": ("m2", 1)}


def test_count_live_agents(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    for agent_id, status in (("a", "running"), ("b", "waiting"), ("c", "completed"), ("d", "queued")):
        store.insert_agent(_record(agent_id, tid).model_copy(update={"status": status}))
    assert store.count_live_agents(tid) == 3
