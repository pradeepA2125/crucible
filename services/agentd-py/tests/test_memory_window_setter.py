import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.harness import NO_OP_HARNESS, MemoryHarness
from agentd.memory.store import MemoryStore


async def _never(old: str, new: str) -> str:
    raise AssertionError("summarize called below threshold")


async def _merge(old: str, new: str) -> str:
    return "merged summary"


@pytest.mark.asyncio
async def test_shrinking_the_window_makes_the_same_history_trip_the_trigger(tmp_path):
    """The window is read per call, so a mid-process change takes effect immediately."""
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(
        store, _never, window_tokens=100_000, trigger_frac=0.65,
        hot_token_frac=0.4, hot_turns=10,
    )
    history = [{"role": "user", "content": "q" * 300} for _ in range(6)]  # ~600 est tokens

    assert (await comp.maybe_compact(history, "r1")).compacted is False

    comp._summarize = _merge  # below-threshold guard no longer applies
    comp.set_window_tokens(500)  # 500 * 0.65 = 325 < ~600
    assert (await comp.maybe_compact(history, "r1")).compacted is True


@pytest.mark.asyncio
async def test_harness_forwards_the_window_to_its_compactor(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(
        store, _never, window_tokens=100_000, trigger_frac=0.65,
        hot_token_frac=0.4, hot_turns=10,
    )
    harness = MemoryHarness(enabled=True, compactor=comp)
    harness.set_window_tokens(4096)
    assert comp._window_tokens == 4096


def test_harness_without_a_compactor_ignores_the_window():
    """NO_OP_HARNESS is a legitimate sink — the caller holds a list and must not
    have to special-case a memory-disabled process."""
    NO_OP_HARNESS.set_window_tokens(4096)  # must not raise
