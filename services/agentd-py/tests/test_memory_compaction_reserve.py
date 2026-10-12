"""Compaction triggers at max(trigger_frac × window, window − reserve), and can be forced.

At 65% of a 272k window, 95k tokens sat above the trigger. The count it compares already
holds everything the next request sends (the last call's measured input, its output and
the new tool results), so the only unknown is the NEXT response — measured at most ~1.5k
tokens across 388 calls, more for a large single-file edit. A fixed reserve covers that;
small windows keep the 65% floor. Forcing is for a request the provider refused as over
its window (context_length_exceeded): compact, then retry."""
from __future__ import annotations

import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.config import MemoryConfig
from agentd.memory.models import ObservedPrompt
from agentd.memory.store import MemoryStore


async def _never(old: str, new: str) -> str:
    raise AssertionError("summarize called below the trigger")


async def _summary(old: str, new: str) -> str:
    return "summary"


def _history() -> list[dict[str, object]]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 300}
            for i in range(12)]


def _compactor(tmp_path, summarize, *, window: int, reserve: int | None = 28_000) -> Compactor:
    kwargs = {} if reserve is None else {"reserve_tokens": reserve}
    return Compactor(MemoryStore(tmp_path / "m.sqlite3"), summarize, window_tokens=window,
                     trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2, **kwargs)


def _measured(history: list[dict[str, object]], tokens: int) -> ObservedPrompt:
    return ObservedPrompt(tokens=tokens, message_count=len(history))


@pytest.mark.asyncio
async def test_a_large_window_compacts_near_its_end_not_at_65_percent(tmp_path):
    history = _history()
    # 200k is past 65% of 272k (176.8k) but well short of 272k − 28k (244k).
    below = await _compactor(tmp_path, _never, window=272_000).maybe_compact(
        history, "r1", observed=_measured(history, 200_000))
    assert below.compacted is False

    at = await _compactor(tmp_path, _summary, window=272_000).maybe_compact(
        history, "r2", observed=_measured(history, 245_000))
    assert at.compacted is True


@pytest.mark.asyncio
async def test_a_small_window_keeps_the_65_percent_floor(tmp_path):
    history = _history()
    # 40k − 28k = 12k would compact almost at once; 65% (26k) wins.
    below = await _compactor(tmp_path, _never, window=40_000).maybe_compact(
        history, "r1", observed=_measured(history, 20_000))
    assert below.compacted is False

    at = await _compactor(tmp_path, _summary, window=40_000).maybe_compact(
        history, "r2", observed=_measured(history, 27_000))
    assert at.compacted is True


@pytest.mark.asyncio
async def test_forcing_compacts_below_the_trigger(tmp_path):
    history = _history()
    result = await _compactor(tmp_path, _summary, window=272_000).maybe_compact(
        history, "r1", observed=_measured(history, 50_000), force=True)
    assert result.compacted is True


def test_the_reserve_comes_from_the_environment_with_a_28k_default():
    assert MemoryConfig.from_env({}).reserve_tokens == 28_000
    env = {"CRUCIBLE_MEMORY_COMPACT_RESERVE_TOKENS": "16000"}
    assert MemoryConfig.from_env(env).reserve_tokens == 16_000
