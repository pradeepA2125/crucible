from datetime import UTC, datetime

import pytest

from agentd.memory.consolidator import Consolidator
from agentd.memory.embedder import Embedder
from agentd.memory.models import CandidateMemory, Memory
from agentd.memory.store import MemoryStore


def _live_mem(mid: str, importance: int) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic", content=f"fact {mid}",
        entities=[], importance=importance, valid_from=now, valid_to=None, superseded_by=None,
        source_kind="consolidation", source_ref="r", source_seq_lo=None, source_seq_hi=None,
        created_at=now,
    )


def _store(tmp_path):
    return MemoryStore(tmp_path / "m.sqlite3")


def _embedder():
    # deterministic: distinct unit vectors per distinct content
    table: dict[str, list[float]] = {}

    def enc(texts):
        out = []
        for t in texts:
            if t not in table:
                v = [0.0] * 384
                v[len(table) % 384] = 1.0
                table[t] = v
            out.append(table[t])
        return out

    return Embedder(encoder=enc)


def _distill_returning(cands):
    async def d(transcript, existing):
        return cands
    return d


@pytest.mark.asyncio
async def test_consolidate_inserts_new(tmp_path):
    store = _store(tmp_path)
    cands = [CandidateMemory(kind="semantic", content="uses bge-small",
                             entities=["agentd/memory/embedder.py"], importance=7)]
    con = Consolidator(store, _embedder(), _distill_returning(cands))
    n = await con.consolidate("thread-x", "workspace", "/ws", "transcript", 0, 8)
    assert n == 1
    live = store.get_live_memories("workspace", "/ws")
    assert len(live) == 1 and live[0].source_kind == "consolidation"
    assert live[0].source_seq_lo == 0 and live[0].source_seq_hi == 8


@pytest.mark.asyncio
async def test_consolidate_dedupes_near_identical(tmp_path):
    store = _store(tmp_path)
    c = CandidateMemory(kind="semantic", content="uses bge-small", entities=[], importance=7)
    con = Consolidator(store, _embedder(), _distill_returning([c]))
    await con.consolidate("t", "workspace", "/ws", "tx", 0, 1)
    await con.consolidate("t", "workspace", "/ws", "tx", 2, 3)  # same content again
    assert len(store.get_live_memories("workspace", "/ws")) == 1  # deduped


@pytest.mark.asyncio
async def test_consolidate_supersedes_on_contradicts(tmp_path):
    store = _store(tmp_path)
    emb = _embedder()  # SHARED so distinct content → distinct vectors (no false dedup)
    first = CandidateMemory(kind="semantic", content="uses openai embeddings",
                            entities=[], importance=6)
    await Consolidator(store, emb, _distill_returning([first])).consolidate(
        "t", "workspace", "/ws", "tx", 0, 1)
    old_id = store.get_live_memories("workspace", "/ws")[0].id
    second = CandidateMemory(kind="semantic", content="uses bge-small embeddings",
                             entities=[], importance=7, contradicts=old_id)
    await Consolidator(store, emb, _distill_returning([second])).consolidate(
        "t", "workspace", "/ws", "tx", 2, 3)
    live = store.get_live_memories("workspace", "/ws")
    assert len(live) == 1 and live[0].content == "uses bge-small embeddings"
    assert store.get_memory(old_id).superseded_by == live[0].id


@pytest.mark.asyncio
async def test_episodic_never_supersedes(tmp_path):
    store = _store(tmp_path)
    emb = _embedder()  # SHARED
    e1 = CandidateMemory(kind="episodic", content="user asked X", entities=[], importance=5)
    await Consolidator(store, emb, _distill_returning([e1])).consolidate(
        "t", "workspace", "/ws", "tx", 0, 1)
    old_id = store.get_live_memories("workspace", "/ws")[0].id
    e2 = CandidateMemory(kind="episodic", content="user asked Y", entities=[], importance=5,
                         contradicts=old_id)  # should be IGNORED for episodic
    await Consolidator(store, emb, _distill_returning([e2])).consolidate(
        "t", "workspace", "/ws", "tx", 2, 3)
    assert len(store.get_live_memories("workspace", "/ws")) == 2  # both kept


@pytest.mark.asyncio
async def test_write_explicit_returns_id(tmp_path):
    store = _store(tmp_path)
    con = Consolidator(store, _embedder(), _distill_returning([]))
    mid = await con.write_explicit("always quote --workspace", "procedural",
                                   ["scripts/stress/start-backend.sh"], "workspace", "/ws")
    assert store.get_memory(mid) is not None
    assert store.get_memory(mid).source_kind == "agent_tool"


@pytest.mark.asyncio
async def test_bounded_existing_caps_context(tmp_path):
    # FIX #4: distill must receive a BOUNDED existing-set, never all live memories.
    store = _store(tmp_path)
    for i in range(30):
        store.insert_memory(_live_mem(f"m{i}", (i % 10) + 1), [0.1] * 384)
    con = Consolidator(store, _embedder(), _distill_returning([]))
    bounded = con._bounded_existing("workspace", "/ws")
    assert len(bounded) <= 20
    assert bounded[0].importance == 10  # most-important first
