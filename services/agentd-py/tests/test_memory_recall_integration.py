import pytest

from agentd.memory.consolidator import Consolidator, make_engine_consolidator
from agentd.memory.embedder import Embedder
from agentd.memory.recall import RecallEngine
from agentd.memory.store import MemoryStore


class _Engine:
    async def generate_json(self, *, model, schema_name, schema, system_instructions,
                            user_payload, on_thinking=None):
        return {"memories": [
            {"kind": "semantic", "content": "the patch engine supports 7 op types",
             "entities": ["patch/engine.py"], "importance": 9, "contradicts": None}]}


@pytest.mark.asyncio
async def test_write_in_run_one_recall_in_run_two(tmp_path):
    # shared db = same workspace across two "sessions"
    db = tmp_path / "m.sqlite3"
    emb = Embedder(encoder=lambda ts: [[1.0] + [0.0] * 383 for _ in ts])

    # Run 1: consolidate a memory
    store1 = MemoryStore(db)
    con = Consolidator(store1, emb, make_engine_consolidator(_Engine(), "m1"))
    await con.consolidate("thread-1", "workspace", "/ws", "we explored patch/engine.py", 0, 5)

    # Run 2: a fresh store over the SAME db recalls it
    store2 = MemoryStore(db)
    eng = RecallEngine(store2, emb, weights=(0.5, 0.3, 0.2), min_score=0.0)
    out = await eng.recall("patch engine op types", "workspace", "/ws", k=3)
    assert any("7 op types" in m.content for m in out)
