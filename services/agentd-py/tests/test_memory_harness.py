import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.config import MemoryConfig
from agentd.memory.harness import (
    NO_OP_HARNESS,
    MemoryHarness,
    build_memory_harness,
    make_engine_summarizer,
)
from agentd.memory.store import MemoryStore


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def generate_text(self, *, model, system_instructions, user_payload, on_thinking=None):
        self.calls.append((model, system_instructions, user_payload))
        return "SUMMARY"


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


@pytest.mark.asyncio
async def test_engine_summarizer_calls_transport():
    t = _FakeTransport()
    summ = make_engine_summarizer(t, "m1")
    out = await summ("prior", "evicted text")
    assert out == "SUMMARY"
    model, _system, payload = t.calls[0]
    assert model == "m1"
    assert payload["prior_summary"] == "prior"
    assert payload["evicted_messages"] == "evicted text"


def test_build_memory_harness_disabled_returns_noop():
    cfg = MemoryConfig.from_env({})  # disabled
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
