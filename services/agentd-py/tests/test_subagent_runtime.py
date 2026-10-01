"""SubAgentRuntime (spec §6.2, §6.7): cap, slot tokens, isolation, stop."""
import asyncio

import pytest

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.runtime import (
    AgentHandle,
    ChildResult,
    SubAgentRuntime,
    agent_channel,
)


def _handle(agent_id: str, parent: str | None = None, depth: int = 1) -> AgentHandle:
    ctx = AgentContext(agent_id=agent_id, name="general-purpose", label=agent_id, depth=depth,
                       parent_agent_id=parent, permission="default",
                       allowed_types=("tool_call", "edit", "progress", "report"),
                       persona="", max_iters=10)
    return AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"],
                       prompt="p", thread_id="t1", turn_id="turn-1")


def test_the_child_channel_name() -> None:
    assert agent_channel("t1", "agent-a") == "chat:t1:agent:agent-a"


@pytest.mark.asyncio
async def test_the_cap_queues_extra_children_and_statuses_flow() -> None:
    seen: list[tuple[str, str]] = []
    runtime = SubAgentRuntime(1, on_status=lambda h: seen.append((h.agent_id, h.status)))
    running = {"now": 0, "peak": 0}

    async def run_child(handle: AgentHandle) -> ChildResult:
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return ChildResult(status="completed", report=f"done {handle.agent_id}",
                           files_changed=[])

    results = await runtime.dispatch([_handle("a"), _handle("b")], run_child)
    assert [r.report for r in results] == ["done a", "done b"]
    assert running["peak"] == 1
    assert seen == [("a", "running"), ("a", "completed"), ("b", "running"), ("b", "completed")]
    handle = runtime.registry.get("a")
    assert handle is not None and handle.started_at and handle.ended_at and not handle.held


@pytest.mark.asyncio
async def test_a_dispatching_child_lends_its_slot_and_takes_it_back() -> None:
    runtime = SubAgentRuntime(1)
    parent = _handle("p")
    await runtime._acquire(parent)  # the dispatcher is running and holds the only slot

    async def run_child(handle: AgentHandle) -> ChildResult:
        return ChildResult(status="completed", report="ok", files_changed=[])

    results = await asyncio.wait_for(
        runtime.dispatch([_handle("c", parent="p", depth=2)], run_child, dispatcher=parent),
        timeout=1)
    assert results[0].status == "completed" and parent.held
    runtime._release(parent)
    with pytest.raises(ValueError):  # BoundedSemaphore: any over-release is loud
        runtime._slots.release()


@pytest.mark.asyncio
async def test_a_cancelled_dispatcher_never_takes_its_slot_back() -> None:
    runtime = SubAgentRuntime(1)
    parent = _handle("p")
    await runtime._acquire(parent)
    started = asyncio.Event()

    async def run_child(handle: AgentHandle) -> ChildResult:
        started.set()
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    waiting = asyncio.create_task(
        runtime.dispatch([_handle("c", parent="p", depth=2)], run_child, dispatcher=parent))
    await started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not parent.held
    child = runtime.registry.get("c")
    assert child is not None and child.status == "stopped"
    with pytest.raises(ValueError):  # every slot is back; one more release would overflow
        runtime._slots.release()


@pytest.mark.asyncio
async def test_one_child_failing_or_stopped_never_touches_its_siblings() -> None:
    runtime = SubAgentRuntime(4)
    started = asyncio.Event()

    async def run_child(handle: AgentHandle) -> ChildResult:
        if handle.agent_id == "boom":
            raise RuntimeError("crash")
        if handle.agent_id == "slow":
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                handle.result = ChildResult(status="stopped", report="stopped mid-way",
                                            files_changed=["s.py"])
                raise
        return ChildResult(status="completed", report="ok", files_changed=[])

    dispatching = asyncio.create_task(runtime.dispatch(
        [_handle("ok"), _handle("boom"), _handle("slow")], run_child))
    await started.wait()
    assert await runtime.stop_agent("slow") is True
    results = await dispatching
    assert [r.status for r in results] == ["completed", "failed", "stopped"]
    assert results[1].report == "Status: failed — crash"
    assert results[2].report == "stopped mid-way" and results[2].files_changed == ["s.py"]
    assert await runtime.stop_agent("slow") is False


def test_subtree_ids() -> None:
    runtime = SubAgentRuntime(2)
    for handle in (_handle("a"), _handle("b", parent="a", depth=2),
                   _handle("c", parent="b", depth=3), _handle("d")):
        runtime.registry.add(handle)
    assert runtime.registry.subtree_ids("a") == {"a", "b", "c"}
    assert runtime.registry.subtree_ids("d") == {"d"}
    assert [h.agent_id for h in runtime.registry.for_turn("t1", "turn-1")] == ["a", "b", "c", "d"]
