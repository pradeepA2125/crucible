import pytest

from agentd.memory.embedder import Embedder
from agentd.memory.models import RecallTrace
from agentd.memory.recall import RecallEngine
from agentd.memory.reranker import Reranker
from agentd.memory.store import MemoryStore
from tests.test_memory_store_phase2 import _mem


def _emb():
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


@pytest.mark.asyncio
async def test_recall_with_trace_captures_signals(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    store.insert_memory(_mem("a", content="auth flow", entities=("src/auth.py",)),
                        emb.embed(["auth flow"])[0])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    mems, trace = await eng.recall_with_trace("auth", "workspace", "/ws", k=5)
    assert isinstance(trace, RecallTrace)
    assert trace.entries and set(trace.entries[0].signals) == {
        "semantic", "lexical", "structural", "importance", "recency"}
    assert trace.entries[0].injected is True and trace.reranked is False
    assert [m.id for m in mems] == [trace.entries[0].memory_id]


@pytest.mark.asyncio
async def test_recall_unchanged_returns_memories(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    store.insert_memory(_mem("a", content="auth flow", entities=()), emb.embed(["auth flow"])[0])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    out = await eng.recall("auth", "workspace", "/ws", k=5)  # same signature as before
    assert [m.id for m in out] == ["a"]


@pytest.mark.asyncio
async def test_reranker_gated_and_reorders(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    for i in range(10):
        store.insert_memory(_mem(f"m{i}", content=f"fact {i}", entities=()),
                            emb.embed([f"fact {i}"])[0])
    rr = Reranker(scorer=lambda pairs: list(range(len(pairs))))  # last candidate highest
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0,
                       reranker=rr, rerank_min_candidates=8)
    _mems, trace = await eng.recall_with_trace("fact", "workspace", "/ws", k=10)
    assert trace.reranked is True
    assert trace.entries[0].rerank_score is not None


@pytest.mark.asyncio
async def test_reranker_skipped_below_gate(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _emb()
    store.insert_memory(_mem("a", content="x", entities=()), emb.embed(["x"])[0])
    called = []
    rr = Reranker(scorer=lambda pairs: called.append(1) or [0.0] * len(pairs))
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0,
                       reranker=rr, rerank_min_candidates=8)
    _mems, trace = await eng.recall_with_trace("x", "workspace", "/ws", k=5)
    assert trace.reranked is False and called == []  # 1 candidate ≤ gate → no model call
