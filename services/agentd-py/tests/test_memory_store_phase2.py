import json
from datetime import UTC, datetime

import sqlite_vec  # noqa: F401  — ensures the dep is installed

from agentd.memory.models import Memory
from agentd.memory.store import MemoryStore


def _mem(mid="m1", content="patch ops in patch/engine.py", entities=("patch/engine.py",)):
    now = datetime(2026, 6, 28, tzinfo=UTC)
    return Memory(
        id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic", content=content,
        entities=list(entities), importance=7, valid_from=now, valid_to=None, superseded_by=None,
        source_kind="consolidation", source_ref="thread-x", source_seq_lo=0, source_seq_hi=8,
        created_at=now,
    )


def test_phase2_tables_created(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    names = {
        r["name"]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    assert {"memories", "memories_fts"} <= names  # always
    if store._vec_enabled:
        assert "vec_memories" in names


def test_vec_enabled_is_bool(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    assert isinstance(store._vec_enabled, bool)
    if store._vec_enabled:
        assert store._conn.execute("SELECT vec_version() AS v").fetchone()["v"]


def test_store_survives_without_vec(tmp_path, monkeypatch):
    # FIX #1: a sqlite-vec/extension failure must NOT crash the store (Phase 1 uses it too).
    import agentd.memory.store as store_mod

    monkeypatch.setattr(
        store_mod.sqlite_vec, "load",
        lambda c: (_ for _ in ()).throw(RuntimeError("no ext")),
    )
    store = MemoryStore(tmp_path / "m.sqlite3")  # must NOT raise
    assert store._vec_enabled is False


def test_insert_and_get_memory_roundtrip(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem(), [0.1] * 384)
    got = store.get_memory("m1")
    assert got is not None and got.content == "patch ops in patch/engine.py"
    assert got.entities == ["patch/engine.py"] and got.importance == 7
    fts = store._conn.execute("SELECT count(*) c FROM memories_fts WHERE memory_id='m1'").fetchone()
    assert fts["c"] == 1
    if store._vec_enabled:
        vec = store._conn.execute(
            "SELECT count(*) c FROM vec_memories WHERE memory_id='m1'").fetchone()
        assert vec["c"] == 1


def test_insert_with_empty_embedding_skips_vec(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("m2"), [])
    if store._vec_enabled:
        vec = store._conn.execute(
            "SELECT count(*) c FROM vec_memories WHERE memory_id='m2'").fetchone()
        assert vec["c"] == 0
    assert store.get_memory("m2") is not None


def test_get_live_memories_filters_scope_and_validity(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("live"), [0.1] * 384)
    retired = _mem("dead").model_copy(update={"valid_to": _mem("dead").valid_from})
    store.insert_memory(retired, [0.1] * 384)
    other = _mem("other").model_copy(update={"scope_id": "/elsewhere"})
    store.insert_memory(other, [0.1] * 384)
    live = store.get_live_memories("workspace", "/ws")
    assert {m.id for m in live} == {"live"}


def test_search_semantic_orders_by_distance(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    if not store._vec_enabled:
        import pytest
        pytest.skip("sqlite-vec unavailable")
    near = [1.0] + [0.0] * 383
    far = [0.0, 1.0] + [0.0] * 382
    store.insert_memory(_mem("near"), near)
    store.insert_memory(_mem("far"), far)
    hits = store.search_semantic(near, k=2, scope_kind="workspace", scope_id="/ws")
    assert hits[0][0] == "near"  # closest first


def test_search_lexical_matches_entities(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("a", content="auth flow lives here", entities=("src/auth.py",)),
                        [0.1] * 384)
    store.insert_memory(_mem("b", content="tax compute", entities=("src/tax.py",)), [0.1] * 384)
    hits = store.search_lexical("auth", k=5, scope_kind="workspace", scope_id="/ws")
    assert hits and hits[0][0] == "a"


def test_search_lexical_handles_fts5_special_chars(tmp_path):
    # Live-smoke bug: a raw user query with paths/dots/colons/operators broke FTS5 MATCH
    # (syntax error) and nuked the whole recall. Must NOT throw, and should still match tokens.
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("a", content="auth flow in src/auth.py", entities=("src/auth.py",)),
                        [0.1] * 384)
    for q in ["Read src/auth.py and walk through", "auth.py:login", "auth AND flow",
              "what (does) this do?", "  ", "/"]:
        hits = store.search_lexical(q, k=5, scope_kind="workspace", scope_id="/ws")  # no raise
        assert isinstance(hits, list)
    assert store.search_lexical("src/auth.py", k=5, scope_kind="workspace", scope_id="/ws")


def test_similar_memories_same_kind_scope(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    if not store._vec_enabled:
        import pytest
        pytest.skip("sqlite-vec unavailable")
    store.insert_memory(_mem("s1"), [1.0] + [0.0] * 383)
    out = store.similar_memories([1.0] + [0.0] * 383, kind="semantic",
                                 scope_kind="workspace", scope_id="/ws", k=3)
    assert out and out[0][0].id == "s1"


def test_supersede_retires_old_and_inserts_new_atomically(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("old", content="uses openai embeddings"), [0.1] * 384)
    new = _mem("new", content="uses bge-small embeddings")
    store.supersede("old", new, [0.2] * 384)
    old = store.get_memory("old")
    assert old is not None and old.valid_to is not None and old.superseded_by == "new"
    live = store.get_live_memories("workspace", "/ws")
    assert {m.id for m in live} == {"new"}


def test_list_memories_filters(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("live"), [0.1] * 384)
    retired = _mem("dead").model_copy(update={"valid_to": _mem("dead").valid_from})
    store.insert_memory(retired, [0.1] * 384)
    epi = _mem("epi").model_copy(update={"kind": "episodic"})
    store.insert_memory(epi, [0.1] * 384)
    assert {m.id for m in store.list_memories("workspace", "/ws")} == {"live", "epi"}
    assert {m.id for m in store.list_memories("workspace", "/ws", include_retired=True)} == {
        "live", "epi", "dead"}
    assert {m.id for m in store.list_memories("workspace", "/ws", kind="episodic")} == {"epi"}


def test_supersede_chain(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("old", content="v1"), [0.1] * 384)
    store.supersede("old", _mem("new", content="v2"), [0.2] * 384)
    chain = store.get_supersede_chain("new")
    assert [m.id for m in chain] == ["old", "new"]  # oldest → newest


def test_supersede_rolls_back_when_insert_fails(tmp_path):
    # FIX #2: a failing insert must roll back the retire-UPDATE — old stays LIVE, no data loss.
    import pytest
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.insert_memory(_mem("old"), [0.1] * 384)
    dup = _mem("old")  # duplicate PK 'old' -> the INSERT inside supersede raises
    with pytest.raises(Exception):
        store.supersede("old", dup, [0.2] * 384)
    assert store.get_memory("old").valid_to is None  # UPDATE rolled back; old still live
