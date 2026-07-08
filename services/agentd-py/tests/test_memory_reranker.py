from datetime import UTC, datetime

from agentd.memory.config import MemoryConfig
from agentd.memory.models import Memory
from agentd.memory.reranker import Reranker


def _m(mid, content):
    now = datetime(2026, 6, 29, tzinfo=UTC)
    return Memory(id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic",
                  content=content, entities=[], importance=5, valid_from=now, valid_to=None,
                  superseded_by=None, source_kind="consolidation", source_ref="r",
                  source_seq_lo=None, source_seq_hi=None, created_at=now)


def test_rerank_reorders_by_scorer():
    cands = [_m("a", "auth flow"), _m("b", "tax compute")]
    rr = Reranker(scorer=lambda pairs: [0.1, 0.9])  # 2nd pair higher → b first
    out = rr.rerank("anything", cands)
    assert [m.id for m, _ in out] == ["b", "a"] and out[0][1] == 0.9


def test_rerank_degrades_to_input_order():
    def boom(pairs):
        raise RuntimeError("no model")
    rr = Reranker(scorer=boom)
    cands = [_m("a", "x"), _m("b", "y")]
    out = rr.rerank("q", cands)
    assert [m.id for m, _ in out] == ["a", "b"]  # input order preserved
    assert rr.available is False


def test_config_reranker_defaults():
    c = MemoryConfig.from_env({})
    assert c.reranker_enabled is True
    assert c.reranker_model == "BAAI/bge-reranker-base"
    assert c.rerank_min_candidates == 8


def test_config_reranker_explicit_disable_still_works():
    c = MemoryConfig.from_env({"AI_EDITOR_MEMORY_RERANKER": "0"})
    assert c.reranker_enabled is False
