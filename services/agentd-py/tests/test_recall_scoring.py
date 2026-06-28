from datetime import UTC, datetime, timedelta

from agentd.memory.models import Memory
from agentd.memory.recall import _fuse, _minmax, _recency


def _mem(mid, imp=5, days_old=0):
    now = datetime(2026, 6, 28, tzinfo=UTC)
    return Memory(
        id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic", content=mid,
        entities=[], importance=imp, valid_from=now - timedelta(days=days_old), valid_to=None,
        superseded_by=None, source_kind="consolidation", source_ref="r", source_seq_lo=None,
        source_seq_hi=None, created_at=now,
    )


def test_minmax_normalizes():
    assert _minmax([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]
    assert _minmax([3.0, 3.0]) == [0.0, 0.0]  # degenerate (all equal) → all 0, no div-by-zero
    assert _minmax([]) == []


def test_recency_decays():
    now = datetime(2026, 6, 28, tzinfo=UTC)
    fresh = _recency(now, now, 14)
    old = _recency(now - timedelta(days=28), now, 14)
    future = _recency(now + timedelta(days=5), now, 14)  # clamps negative age to 0
    assert fresh == 1.0 and old < fresh and future == 1.0


def test_fuse_ranks_strong_semantic_first():
    now = datetime(2026, 6, 28, tzinfo=UTC)
    mems = [_mem("a"), _mem("b")]
    ranked = _fuse(mems, sem={"a": 0.9, "b": 0.1}, lex={}, struct={},
                   weights=(0.5, 0.3, 0.2), now=now)
    assert ranked[0][0].id == "a"


def test_fuse_empty_is_empty():
    assert _fuse([], {}, {}, {}, (0.5, 0.3, 0.2), datetime(2026, 6, 28, tzinfo=UTC)) == []
