"""Process-wide provider request limiter (spec §3.11).

Eight concurrent activations at 5–10 s per call send 48–96 requests per minute; free tiers
allow ~40. One token bucket is shared by every model call in the process. The main agent's
calls (the turn the user is waiting on) are served before any agent's whenever both wait.
Counts logical calls: the transport's own retries are not re-acquired — they back off."""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from agentd.providers.usage import METER, USAGE_OWNER

CALL_PRIORITY: ContextVar[str] = ContextVar("crucible_call_priority", default="main")


class ProviderRateLimiter:
    def __init__(
        self, rpm: float, *, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._rpm = rpm
        self._capacity = max(rpm, 1.0)
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._main_waiting = 0

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rpm / 60.0)
        self._last = now

    async def acquire(self, priority: str) -> float:
        if self._rpm <= 0:
            return 0.0
        started = self._clock()
        is_main = priority == "main"
        if is_main:
            self._main_waiting += 1
        try:
            while True:
                self._refill()
                if self._tokens >= 1 and (is_main or self._main_waiting == 0):
                    self._tokens -= 1
                    return self._clock() - started
                await self._sleep(max((1 - self._tokens) * 60.0 / self._rpm, 0.05))
        finally:
            if is_main:
                self._main_waiting -= 1


def _rpm_from_env() -> float:
    try:
        return float(os.getenv("CRUCIBLE_PROVIDER_MAX_RPM", "0") or 0)
    except ValueError:
        return 0.0


PROCESS_LIMITER = ProviderRateLimiter(_rpm_from_env())


class RateLimitedTransport:
    """Wraps any ModelJsonTransport. Attribute reads and writes go to the inner transport, so
    hot-swap, capability flags and reasoning-effort setters keep working."""

    def __init__(self, inner: Any, limiter: Any) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_limiter", limiter)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def wrapped(self) -> Any:
        """The real transport. Its signature, not this wrapper's **kwargs, says which
        optional callbacks a call may carry (see reasoning/engine.py::_accepts)."""
        return self._inner

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    async def _admit(self) -> None:
        waited = await self._limiter.acquire(CALL_PRIORITY.get())
        METER.record(USAGE_OWNER.get(), requests=1, wait_ms=int(waited * 1000))

    async def generate_json(self, **kwargs: Any) -> Any:
        await self._admit()
        return await self._inner.generate_json(**kwargs)

    async def generate_text(self, **kwargs: Any) -> Any:
        await self._admit()
        return await self._inner.generate_text(**kwargs)
