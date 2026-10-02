"""AgentSupervisor (spec §3.4, §3.6)."""
import asyncio

import pytest

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.inbox import InboxItem
from agentd.subagents.runtime import (
    ActivationInProgress,
    AgentHandle,
    AgentSupervisor,
    ChildResult,
    SubAgentRuntime,
)


def _handle(agent_id: str, parent: str | None = None) -> AgentHandle:
    ctx = AgentContext(agent_id=agent_id, name="general-purpose", label=agent_id,
                       depth=1 if parent is None else 2, parent_agent_id=parent,
                       permission="default", allowed_types=("report",), persona="",
                       max_iters=4)
    return AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"],
                       prompt="p", thread_id="t", turn_id="u")


def _done(status: str = "completed") -> ChildResult:
    return ChildResult(status=status, report="r", files_changed=[])


def test_alias() -> None:
    assert SubAgentRuntime is AgentSupervisor


@pytest.mark.asyncio
async def test_enqueue_then_wait() -> None:
    sup = AgentSupervisor(max_concurrent=2)
    h = _handle("a")

    async def run(handle: AgentHandle) -> ChildResult:
        return _done()

    sup.enqueue(h, run)
    assert sup.is_active("a")
    [result] = await sup.wait([h])
    assert result.status == "completed" and not sup.is_active("a")


@pytest.mark.asyncio
async def test_one_activation_at_a_time() -> None:
    sup = AgentSupervisor(max_concurrent=2)
    h = _handle("a")
    gate = asyncio.Event()

    async def run(handle: AgentHandle) -> ChildResult:
        await gate.wait()
        return _done()

    sup.enqueue(h, run)
    with pytest.raises(ActivationInProgress):
        sup.enqueue(h, run)
    gate.set()
    await sup.wait([h])


@pytest.mark.asyncio
async def test_stop_cascades_deepest_first() -> None:
    tree = {"a": ["b"], "b": ["c"], "c": []}
    order: list[str] = []
    sup = AgentSupervisor(max_concurrent=8, children_of=lambda i: tree.get(i, []))
    handles = {i: _handle(i) for i in tree}
    never = asyncio.Event()

    async def run(handle: AgentHandle) -> ChildResult:
        try:
            await never.wait()
        finally:
            order.append(handle.agent_id)
        return _done()

    for h in handles.values():
        sup.enqueue(h, run)
    await asyncio.sleep(0)
    assert await sup.stop("a")
    assert order == ["c", "b", "a"]
    assert handles["c"].stop_reason == "cascade" and handles["a"].stop_reason == "user"
    assert all(h.status == "stopped" for h in handles.values())


@pytest.mark.asyncio
async def test_drain_returns_notes_and_one_report() -> None:
    sup = AgentSupervisor(max_concurrent=1)
    sup.deliver("a", InboxItem(kind="report", text="r1", wakes=True))
    sup.deliver("a", InboxItem(kind="note", text="n1", wakes=False))
    sup.deliver("a", InboxItem(kind="report", text="r2", wakes=True))
    assert [i.text for i in sup.drain("a")] == ["r1", "n1"]
    assert sup.has_pending_report("a")
    assert [i.text for i in sup.drain("a")] == ["r2"]


@pytest.mark.asyncio
async def test_waking_leftovers_are_handed_back() -> None:
    got: list[list[str]] = []
    sup = AgentSupervisor(max_concurrent=1,
                          on_leftover=lambda h, items: got.append([i.text for i in items]))
    h = _handle("a")

    async def run(handle: AgentHandle) -> ChildResult:
        # Arrives after the loop's last drain.
        sup.deliver("a", InboxItem(kind="report", text="late", wakes=True))
        return _done()

    sup.enqueue(h, run)
    await sup.wait([h])
    assert got == [["late"]]


@pytest.mark.asyncio
async def test_non_waking_leftovers_stay() -> None:
    got: list[object] = []
    sup = AgentSupervisor(max_concurrent=1, on_leftover=lambda h, items: got.append(items))
    sup.deliver("a", InboxItem(kind="note", text="fyi", wakes=False))
    assert got == [] and [i.text for i in sup.drain("a")] == ["fyi"]
