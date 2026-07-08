import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.config import MemoryConfig
from agentd.memory.harness import (
    NO_OP_HARNESS,
    MemoryHarness,
    SummarizerEchoError,
    _extract_summary,
    _is_echo,
    build_memory_harness,
    make_engine_summarizer,
)
from agentd.memory.store import MemoryStore


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def generate_text(self, *, model, system_instructions, user_payload, on_thinking=None):
        self.calls.append((model, system_instructions, user_payload))
        return "<summary>SUMMARY</summary>"


class _SeqTransport:
    """Returns queued responses in order; records the payloads it was called with."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple] = []

    async def generate_text(self, *, model, system_instructions, user_payload, on_thinking=None):
        self.calls.append((model, system_instructions, user_payload))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_disabled_harness_is_passthrough():
    history = [{"role": "user", "content": "hi"}]
    prep = await NO_OP_HARNESS.prepare_turn(history, "r1")
    assert prep.history is history  # exact same object — byte-identical no-op
    assert prep.compacted is False
    assert prep.recalled_memories == []


@pytest.mark.asyncio
async def test_enabled_harness_delegates(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )
    harness = MemoryHarness(enabled=True, compactor=comp)
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    prep = await harness.prepare_turn(history, "r1")
    assert prep.compacted is True and prep.history[0]["content"].startswith("[MEMORY]")


@pytest.mark.asyncio
async def test_prepare_turn_surfaces_compaction_counts(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=2
    )
    harness = MemoryHarness(enabled=True, compactor=comp)
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    prep = await harness.prepare_turn(history, "r1")
    assert prep.compacted is True
    assert prep.evicted_count >= 1
    assert prep.anchor_version == 1


@pytest.mark.asyncio
async def test_prepare_turn_swallows_errors():
    class Boom:
        async def maybe_compact(self, history, run_id):
            raise RuntimeError("kaboom")

    harness = MemoryHarness(enabled=True, compactor=Boom())  # type: ignore[arg-type]
    history = [{"role": "user", "content": "x"}]
    prep = await harness.prepare_turn(history, "r1")
    assert prep.history is history and prep.compacted is False


@pytest.mark.asyncio
async def test_recall_stub_returns_empty():
    assert await NO_OP_HARNESS.recall("anything", "r1") == []


def test_extract_summary_pulls_content_inside_tags():
    assert _extract_summary("noise <summary>kept text</summary> trailing") == "kept text"


def test_extract_summary_returns_stripped_when_no_tags():
    assert _extract_summary("  plain prose summary  ") == "plain prose summary"


def test_is_echo_true_for_json_object():
    # The exact failure we saw live: the model parrots its input payload as JSON.
    assert _is_echo('{"prior_summary": "x", "evicted_messages": "y"}') is True


def test_is_echo_true_for_empty():
    assert _is_echo("   ") is True


def test_is_echo_false_for_prose():
    assert _is_echo("Goal: do the thing. Files touched: a.py") is False


@pytest.mark.asyncio
async def test_summarizer_sends_single_key_payload_and_extracts_summary():
    t = _SeqTransport(["<summary>merged memory</summary>"])
    summ = make_engine_summarizer(t, "m1")
    out = await summ("prior memory", "new evicted msgs")
    assert out == "merged memory"
    model, _system, payload = t.calls[0]
    assert model == "m1"
    # Single text field — NOT the two-key {prior_summary, evicted_messages} shape the
    # model was echoing back verbatim.
    assert list(payload.keys()) == ["transcript"]
    assert "prior memory" in payload["transcript"]
    assert "new evicted msgs" in payload["transcript"]


@pytest.mark.asyncio
async def test_summarizer_retries_once_on_echo_then_succeeds():
    t = _SeqTransport(['{"transcript": "...echoed..."}', "<summary>real summary</summary>"])
    summ = make_engine_summarizer(t, "m1")
    out = await summ("prior", "evicted")
    assert out == "real summary"
    assert len(t.calls) == 2  # retried exactly once


@pytest.mark.asyncio
async def test_summarizer_raises_after_two_echoes():
    echo = '{"transcript": "echoed both times"}'
    t = _SeqTransport([echo, echo])
    summ = make_engine_summarizer(t, "m1")
    with pytest.raises(SummarizerEchoError):
        await summ("prior", "evicted")
    assert len(t.calls) == 2  # one try + one retry, then give up


@pytest.mark.asyncio
async def test_persistent_echo_degrades_and_keeps_prior_anchor(tmp_path):
    # Full fallback path: make_engine_summarizer -> Compactor. A model that always echoes
    # must NOT overwrite the anchor with garbage — compaction still happens (degraded),
    # the prior anchor is preserved.
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.upsert_anchor("r1", "PRIOR ANCHOR")
    always_echo = _SeqTransport(['{"transcript": "echo"}'] * 4)
    comp = Compactor(
        store,
        make_engine_summarizer(always_echo, "m1"),
        window_tokens=100,
        trigger_frac=0.1,
        hot_token_frac=0.4,
        hot_turns=2,
    )
    history = [{"role": "user", "content": "q" * 80} for _ in range(6)]
    result = await comp.maybe_compact(history, "r1")
    assert result.compacted is True
    assert result.degraded is True
    assert store.get_anchor("r1").summary_md == "PRIOR ANCHOR"  # garbage never persisted
    assert len(always_echo.calls) == 2  # one try + one retry, then gave up


def test_build_memory_harness_disabled_returns_noop():
    cfg = MemoryConfig.from_env({"AI_EDITOR_MEMORY_ENABLED": "false"})
    assert build_memory_harness(cfg, _FakeTransport(), "m1") is NO_OP_HARNESS


@pytest.mark.asyncio
async def test_build_memory_harness_enabled_end_to_end(tmp_path):
    cfg = MemoryConfig.from_env(
        {
            "AI_EDITOR_MEMORY_ENABLED": "1",
            "AI_EDITOR_MEMORY_DB_PATH": str(tmp_path / "m.sqlite3"),
            "AI_EDITOR_MEMORY_WINDOW_TOKENS": "100",
            "AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC": "0.1",
            "AI_EDITOR_MEMORY_HOT_TURNS": "2",
        }
    )
    transport = _FakeTransport()
    harness = build_memory_harness(cfg, transport, "m1")
    history = [{"role": "user", "content": "z" * 80} for _ in range(6)]
    prep = await harness.prepare_turn(history, "r1")
    assert prep.compacted is True
    assert "SUMMARY" in prep.history[0]["content"]  # real summarizer path exercised
    assert transport.calls  # transport.generate_text was actually invoked
