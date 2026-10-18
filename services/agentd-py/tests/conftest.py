"""Suite-wide fixtures.

CRUCIBLE_SUBAGENTS_ENABLED defaults ON (spec §12, Phase 5). With it on, every controller
built in a test would offer dispatch_agents, append the SUB-AGENTS teaching block and track
reads in a write log — changing prompts, tool lists and goldens that have nothing to do
with sub-agents. So the suite runs with it OFF; sub-agent tests opt in with
monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1") (spec §14).
CRUCIBLE_TEAMS_ENABLED (default ON since Phase 6) is forced off for the same reason; team tests
opt in with "1". To test "off", set "0" explicitly: delenv now means the default, which is ON.
"""
import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def provider_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Record provider backoff without waiting; keep real event-loop timeouts.

    Opt in only from tests with fake providers. Replace each transport's asyncio
    binding, not asyncio.sleep globally: stream/deadline tests still need real time.
    """
    from agentd.providers import (
        ollama_transport,
        openai_compatible_transport,
        turboquant_transport,
    )

    async def yield_once(_delay: float) -> None:
        await asyncio.sleep(0)

    sleep = AsyncMock(side_effect=yield_once)
    transport_asyncio = SimpleNamespace(
        sleep=sleep, wait_for=asyncio.wait_for, get_running_loop=asyncio.get_running_loop,
    )
    for module in (ollama_transport, openai_compatible_transport, turboquant_transport):
        monkeypatch.setattr(module, "asyncio", transport_asyncio)
    return sleep


@pytest.fixture(autouse=True)
def _subagents_off_unless_a_test_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "0")


@pytest.fixture(autouse=True)
def _teams_off_unless_a_test_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "0")


@pytest.fixture(autouse=True)
def _fresh_shared_models() -> Iterator[None]:
    # Tests swap in fake `sentence_transformers` modules; a model cached by one test must
    # not be served to the next.
    from agentd.model_loading import clear_shared_models
    clear_shared_models()
    yield
    clear_shared_models()
