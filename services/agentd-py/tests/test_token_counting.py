"""The tokenizer is loaded once at startup and only ever read afterwards; memory
budgets use it when it is loaded and fall back to the character estimate otherwise."""
from __future__ import annotations

import pytest

from agentd import context_metrics
from agentd.memory.compactor import Compactor, estimate_tokens
from agentd.memory.harness import MemoryHarness, summary_input_tokens
from agentd.memory.models import ObservedPrompt
from agentd.memory.store import MemoryStore


class _CharTokens:
    """One token per character: denser than any real text, so its counts win."""

    def encode(self, text: str, disallowed_special=()) -> list[int]:
        return [0] * len(text)


@pytest.fixture
def loaded(monkeypatch):
    monkeypatch.setattr(context_metrics, "_ENCODING", _CharTokens())


@pytest.fixture
def unloaded(monkeypatch):
    monkeypatch.setattr(context_metrics, "_ENCODING", None)


def test_reading_the_encoding_never_loads_it(unloaded, monkeypatch):
    def load(*_):
        raise AssertionError("loaded on demand")

    monkeypatch.setattr(context_metrics, "import_module", load)
    assert context_metrics.loaded_encoding() is None
    assert context_metrics.count_text_tokens("hello") is None


def test_startup_load_makes_the_encoding_available(unloaded, monkeypatch):
    class Tiktoken:
        @staticmethod
        def get_encoding(name):
            assert name == "o200k_base"
            return _CharTokens()

    monkeypatch.setattr(context_metrics, "import_module", lambda name: Tiktoken)
    context_metrics.load_encoding()
    assert context_metrics.count_text_tokens("hello") == 5


def test_a_failed_startup_load_leaves_the_estimates_in_place(unloaded, monkeypatch):
    def broken(name):
        raise OSError("offline")

    monkeypatch.setattr(context_metrics, "import_module", broken)
    context_metrics.load_encoding()
    assert context_metrics.loaded_encoding() is None


def test_summary_budget_stays_chars_over_three_when_loaded(unloaded, monkeypatch):
    # Compaction sizes folds with ~20 calls on long prefixes; tokenizing them blocked
    # the event loop for ~0.25 s per compaction and never changed the result.
    estimated = summary_input_tokens("anchor", "evicted " * 50)
    monkeypatch.setattr(context_metrics, "_ENCODING", _CharTokens())
    assert summary_input_tokens("anchor", "evicted " * 50) == estimated


def test_recall_budget_counts_with_the_tokenizer_when_loaded(unloaded, monkeypatch):
    harness = MemoryHarness(enabled=True, compactor=None, recall_token_budget=100)

    class Mem:
        kind = "semantic"
        content = "x" * 60

    estimated = len(harness._render_recall([Mem()] * 20))
    monkeypatch.setattr(context_metrics, "_ENCODING", _CharTokens())
    counted = len(harness._render_recall([Mem()] * 20))
    assert counted < estimated
    # Unloaded, recall uses the same chars/3 estimate as the rest of memory.
    line = f"- (semantic) {'x' * 60}"
    assert estimated == 100 // estimate_tokens(line)


@pytest.mark.asyncio
async def test_unmeasured_overhead_counts_toward_the_trigger(tmp_path):
    # Providers that report no usage never give the compactor a measured count, so
    # the system prompt and schemas were invisible to the trigger.
    async def summ(old, evicted):
        return "A"

    comp = Compactor(MemoryStore(tmp_path / "m.sqlite3"), summ, window_tokens=100,
                     trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2)
    history = [{"role": "user", "content": "a" * 30}, {"role": "assistant", "content": "b" * 60}]
    assert not (await comp.maybe_compact(list(history), "r1")).compacted
    assert (await comp.maybe_compact(list(history), "r2", unmeasured_overhead=40)).compacted


@pytest.mark.asyncio
async def test_unmeasured_overhead_is_ignored_once_usage_is_measured(tmp_path):
    async def summ(old, evicted):
        return "A"

    comp = Compactor(MemoryStore(tmp_path / "m.sqlite3"), summ, window_tokens=100,
                     trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2)
    history = [{"role": "user", "content": "a" * 30}, {"role": "assistant", "content": "b" * 60}]
    observed = ObservedPrompt(tokens=40, message_count=2)
    result = await comp.maybe_compact(list(history), "r", observed=observed,
                                      unmeasured_overhead=500)
    assert not result.compacted


@pytest.mark.asyncio
async def test_harness_passes_the_overhead_only_when_given():
    seen = []

    class Plain:
        async def maybe_compact(self, history, run_id, observed=None):
            seen.append("plain")
            from agentd.memory.models import CompactionResult
            return CompactionResult(compacted=False, history=history)

    class Aware(Plain):
        async def maybe_compact(self, history, run_id, observed=None, *,
                                unmeasured_overhead=0):
            seen.append(unmeasured_overhead)
            from agentd.memory.models import CompactionResult
            return CompactionResult(compacted=False, history=history)

    await MemoryHarness(enabled=True, compactor=Plain()).prepare_turn([], "r")  # type: ignore[arg-type]
    await MemoryHarness(enabled=True, compactor=Aware()).prepare_turn(  # type: ignore[arg-type]
        [], "r", unmeasured_overhead=123)
    assert seen == ["plain", 123]
