"""A ChatGPT plan access stop ends the work at once: no retries, no corrections."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.chat import controller_loop
from agentd.providers.availability import is_provider_unavailable
from agentd.providers.plan_access import (
    PlanUsageLimitReached,
    access_payload,
    describe_access_stop,
    find_access_stop,
)
from tests.loop_harness import run_child_loop
from tests.test_provider_unavailable import REPORT, _Flaky


def _limit() -> PlanUsageLimitReached:
    return PlanUsageLimitReached("limit", code="subscription_sharing_usage_limit_exceeded",
                                 status=429, request_id="req_1")


def test_found_through_wrappers_and_never_unavailable() -> None:
    stop = _limit()
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = stop
    assert find_access_stop(wrapped) is stop
    assert not is_provider_unavailable(wrapped)  # a 429, but not an outage
    assert find_access_stop(ValueError("x")) is None


def test_usage_limit_wording_follows_the_ui_guidelines() -> None:
    payload = access_payload(_limit())
    assert payload["kind"] == "usage_limit" and payload["request_id"] == "req_1"
    assert describe_access_stop(_limit()).startswith("Usage limit reached.")
    assert "ChatGPT settings" in describe_access_stop(_limit())


@pytest.mark.asyncio
async def test_the_loop_stops_on_the_first_access_stop(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_loop, "PROVIDER_RETRY_BACKOFFS_SEC", (0.0, 0.0))
    engine = _Flaky([_limit()] * 5, REPORT)
    with pytest.raises(PlanUsageLimitReached):
        await run_child_loop(tmp_path, REPORT, engine=engine)
    assert engine.calls == 1


@pytest.mark.asyncio
async def test_a_wrapped_access_stop_is_unwrapped(tmp_path: Path) -> None:
    outer = RuntimeError("engine wrapper")
    outer.__cause__ = _limit()
    engine = _Flaky([outer], REPORT)
    with pytest.raises(PlanUsageLimitReached):
        await run_child_loop(tmp_path, REPORT, engine=engine)
    assert engine.calls == 1
