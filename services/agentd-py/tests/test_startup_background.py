"""Best-effort startup work must not delay serving: the managed spawn treats /health as
readiness and kills a backend that is not healthy within 60s (backend-process.ts)."""
import asyncio

import pytest

from agentd.startup import background_tasks, in_background


@pytest.mark.asyncio
async def test_the_startup_handler_returns_before_the_work_finishes() -> None:
    release, done = asyncio.Event(), asyncio.Event()

    async def slow_probe() -> None:
        await release.wait()
        done.set()

    await asyncio.wait_for(in_background(slow_probe, "slow-probe")(), timeout=0.5)
    assert not done.is_set()
    assert any(t.get_name() == "slow-probe" for t in background_tasks())  # kept alive
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)
    assert done.is_set()
    assert not any(t.get_name() == "slow-probe" for t in background_tasks())
