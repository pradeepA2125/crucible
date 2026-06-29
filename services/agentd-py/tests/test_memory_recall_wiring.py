import pytest

from agentd.memory.harness import MemoryHarness


class _SpyRecall:
    def __init__(self, mems=None):
        self.calls = 0
        self.last_query = None
        self._mems = mems or []

    async def recall(self, query, scope_kind, scope_id, k):  # async (FIX #3)
        self.calls += 1
        self.last_query = query
        return self._mems

    async def recall_with_trace(self, query, scope_kind, scope_id, k):  # Phase 3 trace
        from agentd.memory.models import RecallTrace
        self.calls += 1
        self.last_query = query
        return self._mems, RecallTrace(query=query, scope_kind=scope_kind, scope_id=scope_id,
                                       k=k, floor=0.0, reranked=False, entries=[])


@pytest.mark.asyncio
async def test_recall_runs_once_per_query(tmp_path):
    spy = _SpyRecall()
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=spy,
                            scope_kind="workspace", scope_id="/ws")
    hist = [{"role": "user", "content": "explain the patch engine"}]
    await harness.prepare_turn(hist, "thread-x")
    await harness.prepare_turn(hist, "thread-x")  # same query → cached
    assert spy.calls == 1


@pytest.mark.asyncio
async def test_prepare_turn_exposes_recall_trace(tmp_path):
    spy = _SpyRecall(mems=[])
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=spy,
                            scope_kind="workspace", scope_id="/ws")
    prep = await harness.prepare_turn([], "thread-z", query="what does X do")
    assert prep.recall_trace is not None and prep.recall_trace.query == "what does X do"


@pytest.mark.asyncio
async def test_recall_uses_query_param_when_history_has_no_user_msg(tmp_path):
    # Live-smoke bug: on a first turn the current message is in plan_context['goal'], NOT in
    # history. Recall must use the explicit query, else it gets an empty query and returns nothing.
    spy = _SpyRecall(mems=[])
    harness = MemoryHarness(enabled=True, compactor=None, recall_engine=spy,
                            scope_kind="workspace", scope_id="/ws")
    empty_history = []  # first turn: history empty, query lives in goal
    await harness.prepare_turn(empty_history, "thread-y", query="what does the orchestrator do")
    assert spy.calls == 1  # recall fired despite empty history
    assert spy.last_query == "what does the orchestrator do"


@pytest.mark.asyncio
async def test_disabled_harness_skips_recall():
    spy = _SpyRecall()
    harness = MemoryHarness(enabled=False, compactor=None, recall_engine=spy)
    hist = [{"role": "user", "content": "hi"}]
    prep = await harness.prepare_turn(hist, "r1")
    assert prep.history is hist and spy.calls == 0  # passthrough untouched


def test_memory_tool_source_none_without_consolidator():
    from agentd.memory.harness import NO_OP_HARNESS
    assert NO_OP_HARNESS.memory_tool_source() is None


def test_memory_tool_source_present_with_consolidator():
    class _C:
        pass
    harness = MemoryHarness(enabled=True, compactor=None, consolidator=_C(),
                            scope_kind="workspace", scope_id="/ws")
    src = harness.memory_tool_source()
    assert src is not None and src.owns("remember")
