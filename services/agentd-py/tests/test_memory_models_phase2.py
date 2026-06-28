from datetime import UTC, datetime

from agentd.memory.config import MemoryConfig
from agentd.memory.models import CandidateMemory, Memory


def test_memory_model_roundtrips_all_fields():
    m = Memory(
        id="m1", scope_kind="workspace", scope_id="/ws", kind="semantic",
        content="patch ops apply in patch/engine.py", entities=["patch/engine.py"],
        importance=8, valid_from=datetime(2026, 6, 28, tzinfo=UTC), valid_to=None,
        superseded_by=None, source_kind="consolidation", source_ref="thread-x",
        source_seq_lo=0, source_seq_hi=8, created_at=datetime(2026, 6, 28, tzinfo=UTC),
    )
    assert m.kind == "semantic" and m.valid_to is None and m.entities == ["patch/engine.py"]


def test_candidate_memory_defaults_contradicts_none():
    c = CandidateMemory(kind="episodic", content="user rejected plan", entities=[], importance=5)
    assert c.contradicts is None


def test_memory_config_phase2_defaults():
    cfg = MemoryConfig.from_env({})
    assert cfg.dedup_threshold == 0.92
    assert cfg.recall_token_budget == 1500
    assert cfg.weights == (0.5, 0.3, 0.2)
    assert cfg.graph_grounding is True
    assert cfg.embedding_model == "BAAI/bge-small-en-v1.5"
