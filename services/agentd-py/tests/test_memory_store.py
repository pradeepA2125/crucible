from datetime import datetime, timezone

from agentd.memory.models import CompactionSegment
from agentd.memory.store import MemoryStore


def _seg(run_id: str, seq: int, content: str) -> CompactionSegment:
    return CompactionSegment(
        id=f"{run_id}-{seq}",
        run_id=run_id,
        seq=seq,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


def test_segments_round_trip_ordered(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.add_segments([_seg("r1", 1, "first"), _seg("r1", 0, "zeroth")])
    got = store.get_segments("r1")
    assert [s.seq for s in got] == [0, 1]
    assert got[0].content == "zeroth"


def test_segments_scoped_by_run(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.add_segments([_seg("r1", 0, "a"), _seg("r2", 0, "b")])
    assert [s.content for s in store.get_segments("r1")] == ["a"]


def test_next_seq_monotonic_across_batches(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    assert store.next_seq("r1") == 0
    store.add_segments([_seg("r1", store.next_seq("r1"), "a")])
    assert store.next_seq("r1") == 1
    store.add_segments([_seg("r1", 1, "b"), _seg("r1", 2, "c")])
    assert store.next_seq("r1") == 3
    assert store.next_seq("r2") == 0  # scoped per run


def test_anchor_insert_then_bump_version(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    a1 = store.upsert_anchor("r1", "summary v1")
    assert a1.version == 1 and a1.summary_md == "summary v1"
    a2 = store.upsert_anchor("r1", "summary v2")
    assert a2.version == 2 and a2.summary_md == "summary v2"
    assert store.get_anchor("r1").summary_md == "summary v2"


def test_get_anchor_missing_returns_none(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    assert store.get_anchor("nope") is None
