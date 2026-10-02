"""Provider outages are retried, never counted as malformed output (spec §3.3)."""
from pathlib import Path

import pytest

from agentd.chat import controller_loop
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.providers.availability import ProviderUnavailable, is_provider_unavailable
from agentd.providers.openai_compatible_transport import TransientTransportError
from tests.loop_harness import run_child_loop


class _Status(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.status_code = code


def test_predicate() -> None:
    assert is_provider_unavailable(_Status(429))
    assert is_provider_unavailable(_Status(503))
    assert is_provider_unavailable(_Status(408))
    assert not is_provider_unavailable(_Status(400))
    assert is_provider_unavailable(TimeoutError("slow"))
    assert is_provider_unavailable(TransientTransportError("ResourceExhausted (32/32)"))
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = _Status(429)
    assert is_provider_unavailable(wrapped)
    assert not is_provider_unavailable(ValueError("bad json"))


class _Flaky(ScriptedReasoningEngine):
    """Raises the given errors first, then serves the script."""

    def __init__(self, errors: list[Exception], script: list[dict[str, object]]) -> None:
        super().__init__(None, [], controller_step_responses=script)
        self.errors = list(errors)
        self.calls = 0

    async def create_controller_step(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return await super().create_controller_step(*args, **kwargs)


REPORT = [{"type": "report", "thought": "t", "summary": "ok"}]


@pytest.mark.asyncio
async def test_one_outage_then_success_completes(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_loop, "PROVIDER_RETRY_BACKOFFS_SEC", (0.0, 0.0))
    engine = _Flaky([TransientTransportError("ResourceExhausted (32/32)")], REPORT)
    outcome = await run_child_loop(tmp_path, REPORT, engine=engine)
    assert outcome.kind == "report" and engine.calls == 2
    # Nothing was appended for the outage: history is the report turn only.
    assert all("failed" not in str(m.get("content", "")) for m in outcome.history or [])


@pytest.mark.asyncio
async def test_persistent_outage_raises_provider_unavailable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_loop, "PROVIDER_RETRY_BACKOFFS_SEC", (0.0, 0.0))
    engine = _Flaky([_Status(429)] * 10, REPORT)
    with pytest.raises(ProviderUnavailable):
        await run_child_loop(tmp_path, REPORT, engine=engine)
    assert engine.calls == 3  # one attempt + two loop-level retries, never more


@pytest.mark.asyncio
async def test_malformed_output_still_uses_the_malformed_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _Flaky([ValueError("unparseable")], REPORT)
    outcome = await run_child_loop(tmp_path, REPORT, engine=engine)
    assert outcome.kind == "report"
    assert any("failed" in str(m.get("content", "")) for m in outcome.history or [])
