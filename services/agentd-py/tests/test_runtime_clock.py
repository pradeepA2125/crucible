"""The round deadline counts active time only (spec v2 §8.3)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentd.providers.usage import METER, UsageMeter
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.inbox import InboxItem
from agentd.subagents.runtime import AgentHandle, AgentSupervisor, active_seconds, paused_seconds


def _handle(agent_id: str = "agent-x") -> AgentHandle:
    ctx = AgentContext(agent_id=agent_id, name="general-purpose", label="alice", depth=1,
                       parent_agent_id=None, permission="default",
                       allowed_types=("tool_call", "edit", "progress", "report"), persona="",
                       max_iters=10)
    return AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"], prompt="p",
                       thread_id="t", turn_id="u")


def test_peek_does_not_consume() -> None:
    meter = UsageMeter()
    meter.record("a", requests=2, wait_ms=500)
    assert meter.peek("a").wait_ms == 500
    assert meter.peek("a").requests == 2
    assert meter.take("a").requests == 2
    assert meter.peek("a").requests == 0


def test_gate_waits_accumulate_across_status_changes() -> None:
    sup = AgentSupervisor(1)
    handle = _handle()
    sup.set_status(handle, "running")
    sup.set_status(handle, "waiting")
    handle.waiting_since = datetime.now(UTC) - timedelta(seconds=30)   # parked 30 s
    sup.set_status(handle, "running")
    assert 29.5 < handle.gate_wait_s < 31
    assert handle.waiting_since is None


def test_active_seconds_exclude_gate_and_limiter_waits() -> None:
    handle = _handle("agent-clock")
    now = datetime.now(UTC)
    assert active_seconds(handle, now) is None                 # not started yet
    handle.started_at = now - timedelta(seconds=100)
    handle.gate_wait_s = 20
    handle.waiting_since = now - timedelta(seconds=10)         # parked right now
    METER.record("agent-clock", wait_ms=15_000)                # throttled 15 s
    try:
        assert paused_seconds(handle, now) == 30
        assert abs(active_seconds(handle, now) - 55) < 0.01    # 100 - 30 - 15
    finally:
        METER.take("agent-clock")


def test_keep_puts_items_back_without_waking() -> None:
    woken: list[str] = []
    sup = AgentSupervisor(1, on_leftover=lambda h, items: woken.append(h.agent_id))
    sup.keep("agent-k", [InboxItem(kind="note", text="later", wakes=True)])
    assert woken == []
    assert [i.text for i in sup.drain("agent-k")] == ["later"]
