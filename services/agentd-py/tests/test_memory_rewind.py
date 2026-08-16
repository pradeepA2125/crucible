"""Memory side of chat rewind: retiring a rewound span's memories and the anchor."""
from datetime import UTC, datetime, timedelta

from agentd.memory.models import Memory
from agentd.memory.store import MemoryStore


def _memory(mid: str, source_ref: str, created: datetime) -> Memory:
    return Memory(
        id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic",
        content=f"fact {mid}", entities=[], importance=5,
        valid_from=created, valid_to=None, superseded_by=None,
        source_kind="consolidation", source_ref=source_ref,
        source_seq_lo=None, source_seq_hi=None, created_at=created,
    )


def test_retire_since_retires_only_this_thread_after_the_cutoff(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    old = datetime.now(UTC) - timedelta(hours=2)
    new = datetime.now(UTC)
    store.insert_memory(_memory("keep-old", "chat-1", old), [])
    store.insert_memory(_memory("retire", "chat-1", new), [])
    store.insert_memory(_memory("other-thread", "chat-2", new), [])

    count = store.retire_since("chat-1", (new - timedelta(minutes=1)).isoformat())

    assert count == 1
    assert store.get_memory("retire").valid_to is not None
    assert store.get_memory("keep-old").valid_to is None
    assert store.get_memory("other-thread").valid_to is None


def test_clear_anchor_removes_the_row(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.upsert_anchor("chat-1", "summary")
    store.clear_anchor("chat-1")
    assert store.get_anchor("chat-1") is None


def test_harness_anchor_round_trip_and_null_clears(tmp_path):
    """A checkpoint taken before any compaction records None. Restoring must DELETE the
    newer anchor — leaving it would reintroduce exactly the leak retirement closes."""
    from agentd.memory.harness import MemoryHarness

    store = MemoryStore(tmp_path / "memory.sqlite3")
    harness = MemoryHarness(enabled=True, compactor=None)
    harness._store = store  # the harness derives _store from its compactor

    assert harness.anchor_markdown("chat-1") is None
    store.upsert_anchor("chat-1", "leaked summary")
    assert harness.anchor_markdown("chat-1") == "leaked summary"

    harness.restore_anchor("chat-1", None)
    assert store.get_anchor("chat-1") is None


def test_harness_without_store_degrades_silently():
    from agentd.memory.harness import NO_OP_HARNESS

    assert NO_OP_HARNESS.anchor_markdown("chat-1") is None
    NO_OP_HARNESS.restore_anchor("chat-1", "x")  # must not raise
    assert NO_OP_HARNESS.retire_since("chat-1", "2026-01-01T00:00:00+00:00") == 0
