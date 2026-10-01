"""Child memory modes (spec §5.2): no consolidation, recall-only, per-run recall key."""
import asyncio

import pytest

from agentd.memory.harness import MemoryHarness
from agentd.memory.models import CompactionResult, RecallTrace


class _Compactor:
    _store = None

    async def maybe_compact(self, history, run_id, observed=None):
        return CompactionResult(compacted=True, history=history, evicted_count=1,
                                evicted_seq_lo=1, evicted_seq_hi=2)

    def set_window_tokens(self, window_tokens):  # pragma: no cover — protocol filler
        pass


class _Consolidator:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def consolidate(self, run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi):
        self.runs.append(run_id)


class _SpyRecall:
    def __init__(self) -> None:
        self.calls = 0

    async def recall(self, query, scope_kind, scope_id, k):
        return []

    async def recall_with_trace(self, query, scope_kind, scope_id, k):
        self.calls += 1
        return [], RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id, k=k,
                               floor=0.0, reranked=False, entries=[])


@pytest.mark.asyncio
async def test_children_never_schedule_consolidation() -> None:
    cons = _Consolidator()
    harness = MemoryHarness(enabled=True, compactor=_Compactor(), consolidator=cons,
                            scope_kind="workspace", scope_id="/ws")
    await harness.prepare_turn([], "t1:agent-1", consolidate=False)
    await asyncio.sleep(0)
    assert cons.runs == []
    await harness.prepare_turn([], "t1")
    for _ in range(3):
        await asyncio.sleep(0)
    assert cons.runs == ["t1"]


@pytest.mark.asyncio
async def test_the_recall_key_is_per_run_and_released() -> None:
    spy = _SpyRecall()
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=spy,
                            scope_kind="workspace", scope_id="/ws")
    await harness.prepare_turn([], "run-a", query="q1")
    await harness.prepare_turn([], "run-b", query="q2")
    await harness.prepare_turn([], "run-a", query="q1")  # cached per run — no thrash
    assert spy.calls == 2
    harness.release_run("run-a")
    await harness.prepare_turn([], "run-a", query="q1")
    assert spy.calls == 3


def test_a_recall_only_source_needs_no_consolidator() -> None:
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=_SpyRecall(),
                            scope_kind="workspace", scope_id="/ws")
    assert harness.memory_tool_source("t1") is None  # unchanged default path
    child = harness.memory_tool_source("t1:agent-1", allow_remember=False)
    assert child is not None
    assert [d.name for d in child.definitions()] == ["recall"]
    assert child.owns("recall") and not child.owns("remember")


def test_no_recall_engine_means_no_child_source() -> None:
    harness = MemoryHarness(enabled=True, compactor=None, consolidator=_Consolidator(),
                            scope_kind="workspace", scope_id="/ws")
    assert harness.memory_tool_source("t1:agent-1", allow_remember=False) is None
    assert harness.memory_tool_source("t1").owns("remember")
