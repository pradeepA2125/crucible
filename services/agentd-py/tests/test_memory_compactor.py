import pytest

from agentd.memory.compactor import (
    Compactor,
    _select_hot,
    _truncate_to_tokens,
    estimate_tokens,
)
from agentd.memory.store import MemoryStore


async def _never(old: str, new: str) -> str:
    raise AssertionError("summarize called below threshold")


def test_estimate_tokens_charsdiv4():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("") == 1


def test_truncate_keeps_head_and_tail():
    out = _truncate_to_tokens("A" * 100 + "Z" * 100, 10)  # 10 tokens ~ 40 chars
    assert "[truncated]" in out
    assert out.startswith("A") and out.endswith("Z")
    assert len(out) < 200


def test_select_hot_token_bounded():
    hist = [{"role": "user", "content": "x" * 80} for _ in range(5)]  # ~20 tok each
    evicted, hot, used = _select_hot(hist, hot_budget_tokens=45, hot_turns_cap=10)
    assert len(hot) == 2 and hot == hist[-2:]
    assert len(evicted) == 3 and used <= 45


def test_select_hot_count_capped():
    hist = [{"role": "user", "content": "x" * 4} for _ in range(20)]  # tiny msgs
    evicted, hot, _ = _select_hot(hist, hot_budget_tokens=10_000, hot_turns_cap=3)
    assert len(hot) == 3 and hot == hist[-3:]


def test_select_hot_always_keeps_one():
    hist = [{"role": "user", "content": "x" * 4000}]  # one huge msg over any budget
    evicted, hot, used = _select_hot(hist, hot_budget_tokens=10, hot_turns_cap=10)
    assert len(hot) == 1 and evicted == [] and used > 10


def test_select_hot_lossless_at_turn_boundary():
    # Naive per-message walk would strand tool_result "r1" (its action a1 falls outside
    # the count cap). Trim must push r1 to eviction so hot begins at a turn start.
    hist = [
        {"role": "assistant", "content": "a1"},
        {"role": "tool_result", "content": "r1"},  # older turn
        {"role": "assistant", "content": "a2"},
        {"role": "tool_result", "content": "r2"},  # newer turn
    ]
    evicted, hot, _ = _select_hot(hist, hot_budget_tokens=10_000, hot_turns_cap=3)
    assert hot[0]["role"] != "tool_result"  # hot begins at a turn start
    assert hot == hist[-2:]  # whole newer turn kept
    assert hist[1] in evicted  # stranded r1 pushed to eviction


def test_select_hot_keeps_action_result_pair_together():
    hist = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "tool_result", "content": "r"},
    ]
    evicted, hot, _ = _select_hot(hist, hot_budget_tokens=10_000, hot_turns_cap=10)
    assert hot == hist and evicted == []  # all fit; pair stays intact


@pytest.mark.asyncio
async def test_below_threshold_is_noop(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(
        store, _never, window_tokens=10000, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=10
    )
    history = [{"role": "user", "content": "xxxx"} for _ in range(3)]
    result = await comp.maybe_compact(history, "r1")
    assert result.compacted is False
    assert result.history == history
    assert store.get_anchor("r1") is None


@pytest.mark.asyncio
async def test_over_threshold_compacts(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    captured = {}

    async def summ(old: str, evicted: str) -> str:
        captured["old"], captured["evicted"] = old, evicted
        return "MERGED ANCHOR"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )  # hot_budget = 40 tokens
    history = [{"role": "user", "content": "z" * 80} for _ in range(6)]  # ~20 tok each
    result = await comp.maybe_compact(history, "r1")
    assert result.compacted is True
    assert result.history[-2:] == history[-2:]  # last 2 verbatim (count cap)
    assert result.history[0]["content"].startswith("[MEMORY]")
    assert "MERGED ANCHOR" in result.history[0]["content"]
    assert len(store.get_segments("r1")) == 4  # first 4 evicted
    assert store.get_anchor("r1").summary_md == "MERGED ANCHOR"


@pytest.mark.asyncio
async def test_anchor_merges_not_regenerates(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.upsert_anchor("r1", "PRIOR")
    seen = {}

    async def summ(old: str, evicted: str) -> str:
        seen["old"] = old
        return old + " + NEW"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )
    history = [{"role": "user", "content": "z" * 80} for _ in range(6)]
    await comp.maybe_compact(history, "r1")
    assert seen["old"] == "PRIOR"  # prior anchor fed back in
    assert store.get_anchor("r1").summary_md == "PRIOR + NEW"
    assert store.get_anchor("r1").version == 2


@pytest.mark.asyncio
async def test_single_oversize_message_is_truncated(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old: str, evicted: str) -> str:
        raise AssertionError("summarize should not run when nothing is evicted")

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=10
    )  # hot_budget = 40 tok = 160 chars
    history = [{"role": "user", "content": "q" * 4000}]  # ~1000 tok, sole newest turn
    result = await comp.maybe_compact(history, "r1")
    assert result.compacted is True and result.degraded is True
    assert len(result.history) == 1
    assert len(result.history[0]["content"]) < 4000
    assert "[truncated]" in result.history[0]["content"]
    assert len(store.get_segments("r1")) == 1
    assert store.get_segments("r1")[0].content == "q" * 4000  # full original persisted


@pytest.mark.asyncio
async def test_summarizer_failure_falls_back(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def boom(old: str, evicted: str) -> str:
        raise RuntimeError("provider down")

    comp = Compactor(
        store, boom, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )
    history = [{"role": "user", "content": "y" * 80} for _ in range(6)]
    result = await comp.maybe_compact(history, "r1")
    assert result.degraded is True and result.compacted is True
    assert result.history[-2:] == history[-2:]  # hot preserved
    assert len(store.get_segments("r1")) == 4  # evicted still persisted (lossless)
    assert store.get_anchor("r1") is None  # no anchor written on failure
