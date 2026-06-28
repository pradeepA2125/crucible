import asyncio

import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.harness import MemoryHarness
from agentd.memory.store import MemoryStore


class _SpyConsolidator:
    def __init__(self):
        self.calls = []

    async def consolidate(self, run_id, scope_kind, scope_id, transcript, seq_lo, seq_hi):
        self.calls.append((run_id, scope_kind, scope_id, seq_lo, seq_hi))
        return 0


@pytest.mark.asyncio
async def test_compaction_schedules_consolidation(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    comp = Compactor(store, summ, window_tokens=100, trigger_frac=0.1,
                     hot_token_frac=0.4, hot_turns=2)
    spy = _SpyConsolidator()
    harness = MemoryHarness(enabled=True, compactor=comp, consolidator=spy,
                            scope_kind="workspace", scope_id="/ws")
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    await harness.prepare_turn(history, "thread-x")
    await asyncio.sleep(0)  # let the fire-and-forget task run
    assert spy.calls and spy.calls[0][0] == "thread-x"
    assert spy.calls[0][1] == "workspace"  # scope passed through
