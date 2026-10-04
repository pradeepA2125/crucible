"""One process-wide request budget; the user's turn goes first (spec §3.11)."""
import asyncio

import pytest

from agentd.providers.rate_limit import CALL_PRIORITY, ProviderRateLimiter, RateLimitedTransport


class _Clock:
    """Time moves only when the test says so; sleeping just yields."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_unlimited_never_waits() -> None:
    assert await ProviderRateLimiter(0).acquire("agent") == 0.0


@pytest.mark.asyncio
async def test_the_main_lane_goes_first() -> None:
    clock = _Clock()
    limiter = ProviderRateLimiter(1, clock=clock, sleep=clock.sleep)  # 1 request / minute
    await limiter.acquire("agent")                                    # drains the bucket
    order: list[str] = []

    async def call(lane: str) -> None:
        await limiter.acquire(lane)
        order.append(lane)

    agent = asyncio.create_task(call("agent"))
    main = asyncio.create_task(call("main"))
    for _ in range(5):            # both are now waiting on an empty bucket
        await asyncio.sleep(0)
    clock.now += 60               # one token refills
    for _ in range(5):
        await asyncio.sleep(0)
    assert order == ["main"]      # the user's turn took it
    clock.now += 60
    await asyncio.gather(agent, main)
    assert order == ["main", "agent"]


@pytest.mark.asyncio
async def test_the_transport_wrapper_forwards_and_reads_the_lane() -> None:
    lanes: list[str] = []

    class _Limiter:
        async def acquire(self, priority: str) -> float:
            lanes.append(priority)
            return 0.0

    class _Inner:
        supports_oneof_grammar = True

        async def generate_json(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"ok": True}

    wrapped = RateLimitedTransport(_Inner(), _Limiter())
    assert wrapped.supports_oneof_grammar is True
    assert await wrapped.generate_json(model="m") == {"ok": True}
    token = CALL_PRIORITY.set("agent")
    try:
        await wrapped.generate_json(model="m")
    finally:
        CALL_PRIORITY.reset(token)
    assert lanes == ["main", "agent"]
