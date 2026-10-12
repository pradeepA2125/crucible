"""A request the provider refuses as over its context window (`context_length_exceeded`)
is compacted and retried once, instead of being retried unchanged as malformed output.

Before: the error became a generic RuntimeError, the loop appended a correction message
(making the next request larger) and resent until the turn gave up. Codex fails the turn
here and compacts on the next one; retrying once after compaction keeps the turn alive and
cannot loop. `response.incomplete` is deliberately NOT treated as overflow: content
filtering and server-side output caps end a response the same way."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.memory.compactor import Compactor
from agentd.memory.harness import MemoryHarness
from agentd.memory.store import MemoryStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.providers.availability import ContextWindowExceeded
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from tests.test_openai_transport import SCHEMA, FakeResponses, _failed, _status_error, _transport

OVERFLOW_BODY = {"error": {"message": "Your input exceeds the context window of this model.",
                           "type": "invalid_request_error", "code": "context_length_exceeded"}}


@pytest.mark.asyncio
async def test_a_stream_failure_for_the_window_is_a_context_overflow() -> None:
    fake = FakeResponses([_failed("context_length_exceeded")])
    with pytest.raises(ContextWindowExceeded):
        await _transport(fake, plan_route=True).generate_json(
            model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    assert len(fake.calls) == 1  # not retried as transient


@pytest.mark.asyncio
async def test_an_http_400_for_the_window_is_a_context_overflow() -> None:
    fake = FakeResponses(_status_error(400, OVERFLOW_BODY))
    with pytest.raises(ContextWindowExceeded):
        await _transport(fake, plan_route=True).generate_json(
            model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    assert len(fake.calls) == 1


class _OverflowThenAnswer:
    """Refuses the first `overflows` requests as over the window, then answers."""

    def __init__(self, overflows: int, limit: int | None = None) -> None:
        self._overflows = overflows
        self._limit = limit
        self.calls = 0

    async def create_controller_step(self, **kwargs: Any) -> dict[str, object]:
        self.calls += 1
        if self.calls <= self._overflows:
            raise ContextWindowExceeded("context_length_exceeded: input exceeds the window",
                                        limit=self._limit)
        return {"type": "answer", "thought": "done", "answer": "ok"}


def _loop(tmp_path: Path, engine: object, forced: list[bool],
          compactors: list[Compactor] | None = None) -> ControllerLoop:
    async def summ(old: str, evicted: str) -> str:
        return "summary"

    class SpyCompactor(Compactor):
        async def maybe_compact(self, history, run_id, observed=None, *, force=False):
            forced.append(force)
            return await super().maybe_compact(history, run_id, observed=observed, force=force)

    compactor = SpyCompactor(MemoryStore(tmp_path / "m.sqlite3"), summ, window_tokens=100_000,
                             trigger_frac=0.65, hot_token_frac=0.4, hot_turns=10)
    if compactors is not None:
        compactors.append(compactor)
    registry = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(engine, registry, EventBroadcaster(), channel_id="c1",
                          phase_sm=ControllerPhaseSM(),
                          memory_harness=MemoryHarness(enabled=True, compactor=compactor))


def _context(tmp_path: Path) -> dict[str, object]:
    return {"goal": "explain", "workspace_path": str(tmp_path), "run_id": "thread-x"}


@pytest.mark.asyncio
async def test_an_overflow_compacts_and_retries_once(tmp_path: Path) -> None:
    engine, forced = _OverflowThenAnswer(overflows=1), []
    outcome = await _loop(tmp_path, engine, forced).run(_context(tmp_path), max_iters=6)

    assert outcome.kind == "answer" and outcome.text == "ok"
    assert engine.calls == 2
    assert forced == [False, True]  # the retry's preparation forced compaction
    # Nothing was added for the model to "correct": that would only grow the request.
    assert not any("Your previous response failed" in str(m.get("content", ""))
                   for m in outcome.history)


@pytest.mark.asyncio
async def test_a_second_overflow_in_a_row_ends_the_turn(tmp_path: Path) -> None:
    engine, forced = _OverflowThenAnswer(overflows=2), []
    with pytest.raises(ContextWindowExceeded, match="even after compacting"):
        await _loop(tmp_path, engine, forced).run(_context(tmp_path), max_iters=6)
    assert engine.calls == 2


@pytest.mark.asyncio
async def test_a_stated_window_lowers_the_compaction_window(tmp_path: Path) -> None:
    # Ollama (n_ctx), NIM, OpenRouter, Anthropic and Gemini name the real window. Using
    # it makes later requests compact before they hit it, instead of overflowing first.
    compactors: list[Compactor] = []
    engine = _OverflowThenAnswer(overflows=1, limit=32_768)
    await _loop(tmp_path, engine, [], compactors).run(_context(tmp_path), max_iters=6)
    assert compactors[0].trigger_tokens() == max(int(32_768 * 0.65), 32_768 - 28_000)


@pytest.mark.asyncio
async def test_a_stated_window_never_raises_the_compaction_window(tmp_path: Path) -> None:
    compactors: list[Compactor] = []
    engine = _OverflowThenAnswer(overflows=1, limit=1_048_576)
    await _loop(tmp_path, engine, [], compactors).run(_context(tmp_path), max_iters=6)
    assert compactors[0].trigger_tokens() == 100_000 - 28_000
