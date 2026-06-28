import pytest

from agentd.memory.embedder import Embedder
from agentd.memory.recall import RecallEngine
from agentd.memory.store import MemoryStore
from tests.test_memory_store_phase2 import _mem  # reuse the 2A fixture builder


def _embedder():
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
async def test_recall_returns_lexical_match(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _embedder()
    store.insert_memory(_mem("auth", content="auth flow", entities=("src/auth.py",)),
                        emb.embed(["auth flow"])[0])
    store.insert_memory(_mem("tax", content="tax compute", entities=("src/tax.py",)),
                        emb.embed(["tax compute"])[0])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    out = await eng.recall("auth", "workspace", "/ws", k=1)
    assert out and out[0].id == "auth"


@pytest.mark.asyncio
async def test_recall_degrades_without_embedder(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    def boom(texts):
        raise RuntimeError("no model")

    emb = Embedder(encoder=boom)
    store.insert_memory(_mem("auth", content="auth flow", entities=("src/auth.py",)), [])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    out = await eng.recall("auth", "workspace", "/ws", k=1)
    assert out and out[0].id == "auth"  # lexical still works


@pytest.mark.asyncio
async def test_recall_floor_drops_weak_matches(tmp_path):
    # FIX #7: nothing relevant → inject nothing (don't pollute every turn).
    store = MemoryStore(tmp_path / "m.sqlite3")
    emb = _embedder()
    store.insert_memory(_mem("auth", content="auth flow", entities=("src/auth.py",)),
                        emb.embed(["auth flow"])[0])
    eng = RecallEngine(store, emb, weights=(0.5, 0.3, 0.2), min_score=0.99)
    out = await eng.recall("completely unrelated zzzzz", "workspace", "/ws", k=5)
    assert out == []
