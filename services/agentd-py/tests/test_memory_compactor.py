import logging

import pytest

from agentd.memory.compactor import (
    Compactor,
    _render,
    _select_hot,
    _truncate_to_tokens,
    estimate_tokens,
)
from agentd.memory.models import ObservedPrompt
from agentd.memory.store import MemoryStore


async def _never(old: str, new: str) -> str:
    raise AssertionError("summarize called below threshold")


@pytest.mark.asyncio
async def test_compaction_result_reports_evicted_count_and_version(tmp_path, caplog):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old: str, evicted: str) -> str:
        return "merged summary"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    with caplog.at_level(logging.INFO, logger="agentd.memory.compactor"):
        result = await comp.maybe_compact(history, "r1")
    assert result.compacted is True
    assert result.evicted_count >= 1  # something was actually evicted
    assert result.anchor_version == 1  # first upsert
    assert any("compacted" in r.message.lower() for r in caplog.records)  # success log emitted


@pytest.mark.asyncio
async def test_below_threshold_reports_zero_counts(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(
        store, _never, window_tokens=100000, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=10
    )
    result = await comp.maybe_compact([{"role": "user", "content": "hi"}], "r1")
    assert result.compacted is False
    assert result.evicted_count == 0 and result.anchor_version == 0


def test_estimate_tokens_uses_conservative_ratio():
    """Regression test for the 2026-07-17 undercount finding: real TurboQuant KV-cache
    tokens (98244) vs the len//4 estimate for equivalent-sized content (~59117) showed a
    1.66x undercount, which let compaction's trigger fire too late to prevent context
    overflow. len//3 is still an approximation but cuts the undercount roughly in half
    without needing a real tokenizer dependency."""
    # 300 chars of representative code/JSON-ish content (the kind that dominates this
    # conversation's history — tool_result payloads, patch_ops, ruff/pytest output).
    text = '{"op": "search_replace", "file": "core/x.py", "search": "a", "replace": "b"}' * 4
    assert len(text) == 304
    # len//4 (the old ratio) would give 76 — the fix must NOT be the old ratio.
    assert estimate_tokens(text) != len(text) // 4
    # len//3 is the new floor (101).
    assert estimate_tokens(text) == len(text) // 3


def test_estimate_tokens_minimum_is_one():
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("") == 1


def test_truncate_keeps_head_and_tail():
    out = _truncate_to_tokens("A" * 100 + "Z" * 100, 10)  # 10 tokens ~ 30 chars (len//3)
    assert "[truncated]" in out
    assert out.startswith("A") and out.endswith("Z")
    assert len(out) < 200


def test_truncate_to_tokens_agrees_with_estimate_tokens():
    """Regression test for the estimate_tokens/_truncate_to_tokens ratio-mismatch bug:
    both directions of the chars<->tokens conversion must share one ratio, else a
    caller relying on _truncate_to_tokens(text, budget) to cap estimate_tokens(...) at
    roughly `budget` overshoots badly (e.g. the hot-floor truncation backstop in
    maybe_compact). Some small overshoot is expected regardless — the "…[truncated]…"
    marker adds ~5 tokens on top of the budgeted content — but with the old mismatched
    ratios (estimate_tokens len//3 vs _truncate_to_tokens max_tokens*4) a 40-token
    budget overshot to 58 estimated tokens (45%); with the unified _CHARS_PER_TOKEN
    ratio it overshoots only to ~45 (the marker's fixed cost), well under a 10-token
    slack.
    """
    text = "x" * 400
    truncated = _truncate_to_tokens(text, 40)
    assert estimate_tokens(truncated) <= 40 + 10


def test_select_hot_token_bounded():
    hist = [{"role": "user", "content": "x" * 80} for _ in range(5)]  # ~26 tok each (len//3)
    evicted, hot, used = _select_hot(hist, hot_budget_tokens=52, hot_turns_cap=10)
    assert len(hot) == 2 and hot == hist[-2:]
    assert len(evicted) == 3 and used <= 52


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
        store, summ, window_tokens=130, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )  # hot_budget = 52 tokens (to fit 2 messages of ~26 tok each with len//3)
    history = [{"role": "user", "content": "z" * 80} for _ in range(6)]  # ~26 tok each (len//3)
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
    )  # hot_budget = 40 tok = 120 chars (len//3)
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
        store, boom, window_tokens=130, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )
    history = [{"role": "user", "content": "y" * 80} for _ in range(6)]
    result = await comp.maybe_compact(history, "r1")
    assert result.degraded is True and result.compacted is True
    assert result.history[-2:] == history[-2:]  # hot preserved (2 messages of ~26 tok each)
    assert len(store.get_segments("r1")) == 4  # evicted still persisted (lossless)
    assert store.get_anchor("r1") is None  # no anchor written on failure


@pytest.mark.asyncio
async def test_observation_makes_the_trigger_fire_on_real_occupancy(tmp_path):
    """The estimate sees message contents only. A history that looks small can
    still be sitting behind a large system prompt, and the provider's count is
    the only thing that knows."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    # threshold = 100 * 0.65 = 65 tokens of input
    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2
    )
    # Estimated at 30 tokens — well under the threshold on its own.
    history = [
        {"role": "user", "content": "a" * 30},
        {"role": "assistant", "content": "b" * 60},
    ]

    without = await comp.maybe_compact(list(history), "r-no-obs")
    assert without.compacted is False, "estimate alone should stay under threshold"

    # The provider says this same history really costs 80 tokens of input.
    observed = ObservedPrompt(tokens=80, message_count=2)
    with_obs = await comp.maybe_compact(list(history), "r-obs", observed=observed)
    assert with_obs.compacted is True, "measured input is over threshold and must compact"


@pytest.mark.asyncio
async def test_stale_observation_does_not_trigger_compaction(tmp_path):
    """After a compaction rewrites history the pinned count is meaningless; using
    it would compact again immediately, every turn, forever."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2
    )
    history = [
        {"role": "user", "content": "a" * 30},
        {"role": "assistant", "content": "b" * 60},
    ]
    stale = ObservedPrompt(tokens=5000, message_count=99)
    result = await comp.maybe_compact(history, "r-stale", observed=stale)
    assert result.compacted is False


@pytest.mark.asyncio
async def test_eviction_floor_leaves_room_for_the_fixed_overhead(tmp_path):
    """hot_token_frac budgets retained HISTORY, but the trigger now measures total
    input. A large system prompt must shrink the history that survives, or
    compaction cannot actually get back under the window."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    # hot floor = 1000 * 0.4 = 400 tokens of input.
    comp = Compactor(
        store, summ, window_tokens=1000, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=50
    )
    history = [{"role": "user", "content": "q" * 300} for _ in range(10)]   # 100 tokens each

    # 300 tokens of that 400 floor is system prompt: measured 1300 total against
    # 1000 tokens of history.
    observed = ObservedPrompt(tokens=1300, message_count=10)
    result = await comp.maybe_compact(list(history), "r-floor", observed=observed)

    assert result.compacted is True
    retained = sum(len(str(m.get("content", ""))) // 3
                   for m in result.history if not str(m.get("content", "")).startswith("[MEMORY]"))
    assert retained <= 100, f"kept {retained} tokens of history against a 100-token budget"


@pytest.mark.asyncio
async def test_pathological_overhead_cannot_collapse_the_eviction_floor(tmp_path):
    """A wildly over-stated fixed_overhead (an `observed` count far above what the
    estimator sees for the same slice — the `_CHARS_PER_TOKEN` comment documents
    this ratio can be off in either direction) must not collapse the hot budget
    all the way to 1: at 1, _select_hot keeps a single message and the oversize
    backstop truncates it to _truncate_to_tokens's own 8-char floor, destroying
    the user's newest message instead of just retaining less history. The clamp
    is a quarter of the nominal floor (final whole-branch review, finding 3)."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    # nominal hot floor = 128000 * 0.4 = 51200 tokens; the fix clamps to
    # nominal // 4 = 12800 tokens even under a pathological overhead.
    comp = Compactor(
        store, summ, window_tokens=128000, trigger_frac=0.01, hot_token_frac=0.4, hot_turns=50
    )
    history = [{"role": "user", "content": "x" * 9000} for _ in range(8)]  # ~3000 tok each

    # A provider reporting 5,000,000 total input tokens for this same slice is the
    # pathological case: fixed_overhead = 5,000,000 - estimate(~24000) is enormous,
    # and the old max(1, ...) clamp collapsed hot_budget to 1.
    observed = ObservedPrompt(tokens=5_000_000, message_count=len(history))
    result = await comp.maybe_compact(list(history), "r-pathological", observed=observed)

    assert result.compacted is True
    assert "…[truncated]…" not in _render(result.history), (
        "the newest message was truncated to a handful of characters — the "
        "eviction floor collapsed toward 1 instead of clamping to a fraction "
        "of the nominal floor")
    assert len(result.history) > 1, "only the single-message degenerate case survived"
