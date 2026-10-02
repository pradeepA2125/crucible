"""SubAgentRuntime (spec §6.2, §6.7): runs dispatched children under one process-wide
concurrency cap, keeps the registry, and owns slot accounting.

It knows nothing about loops, stores or gates: the controller passes `run_child` (build and
run one child) and `on_status` (persist + broadcast a status change).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import AgentDefinition

if TYPE_CHECKING:
    from agentd.subagents.events import SequencedBroadcaster
    from agentd.subagents.transcript import AgentTranscript

logger = logging.getLogger(__name__)

# `status` is the single scheduling field (spec §3.1). v1's `waiting` means "parked at an
# approval gate" — a live status; v2's peer-wait is `awaiting_peer`, an idle one.
LIVE_STATUSES = frozenset({"queued", "running", "waiting"})
IDLE_STATUSES = frozenset({
    "completed", "awaiting_peer", "partial", "failed", "failed_transient", "stopped"})
# Kept for existing imports: every idle status ends an activation.
TERMINAL_STATUSES = IDLE_STATUSES


def agent_channel(thread_id: str, agent_id: str) -> str:
    return f"chat:{thread_id}:agent:{agent_id}"


@dataclass(frozen=True)
class DispatchRequest:
    agent: AgentDefinition
    prompt: str
    label: str


@dataclass(frozen=True)
class ChildResult:
    status: str
    report: str  # the full report, never truncated (D8)
    files_changed: list[str]
    stale_refusals: int = 0


@dataclass(eq=False)
class AgentHandle:
    context: AgentContext
    definition: AgentDefinition
    prompt: str
    thread_id: str
    turn_id: str
    status: str = "queued"
    held: bool = False  # the slot token (spec §6.2)
    task: asyncio.Task[ChildResult] | None = None
    loop: object | None = None  # the child's ControllerLoop once built
    transcript: AgentTranscript | None = None
    broadcaster: SequencedBroadcaster | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result: ChildResult | None = None

    @property
    def agent_id(self) -> str:
        return self.context.agent_id


class AgentRegistry:
    def __init__(self) -> None:
        self._handles: dict[str, AgentHandle] = {}

    def add(self, handle: AgentHandle) -> None:
        self._handles[handle.agent_id] = handle

    def get(self, agent_id: str) -> AgentHandle | None:
        return self._handles.get(agent_id)

    def for_turn(self, thread_id: str, turn_id: str) -> list[AgentHandle]:
        return [h for h in self._handles.values()
                if h.thread_id == thread_id and h.turn_id == turn_id]

    def subtree_ids(self, agent_id: str) -> set[str]:
        ids, frontier = {agent_id}, [agent_id]
        while frontier:
            current = frontier.pop()
            for handle in self._handles.values():
                if handle.context.parent_agent_id == current and handle.agent_id not in ids:
                    ids.add(handle.agent_id)
                    frontier.append(handle.agent_id)
        return ids


RunChild = Callable[[AgentHandle], Awaitable[ChildResult]]
StatusSink = Callable[[AgentHandle], None]


class SubAgentRuntime:
    def __init__(self, max_concurrent: int, on_status: StatusSink | None = None) -> None:
        # Process-wide on purpose: the cap protects the provider's rate limit. `main` never
        # holds a slot. Bounded, so any accounting bug is a loud ValueError.
        self._slots = asyncio.BoundedSemaphore(max_concurrent)
        self._on_status = on_status
        self.registry = AgentRegistry()

    def set_status(self, handle: AgentHandle, status: str) -> None:
        handle.status = status
        if status == "running" and handle.started_at is None:
            handle.started_at = datetime.now(UTC)
        if status in TERMINAL_STATUSES:
            handle.ended_at = datetime.now(UTC)
        if self._on_status is not None:
            self._on_status(handle)

    async def dispatch(
        self, handles: list[AgentHandle], run_child: RunChild, *,
        dispatcher: AgentHandle | None = None,
    ) -> list[ChildResult]:
        for handle in handles:
            self.registry.add(handle)
        if dispatcher is not None and dispatcher.held:
            # A child waiting on its own children must not pin a slot they may need.
            self._release(dispatcher)
        tasks = [asyncio.create_task(self._run_one(h, run_child)) for h in handles]
        for handle, task in zip(handles, tasks, strict=True):
            handle.task = task
        # A cancel of THIS await (the dispatcher stopping) cancels every child and
        # propagates; the dispatcher then never re-acquires, so `held` stays False.
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if dispatcher is not None:
            await self._acquire(dispatcher)
        return [self._result_of(h, o) for h, o in zip(handles, outcomes, strict=True)]

    async def stop_agent(self, agent_id: str) -> bool:
        handle = self.registry.get(agent_id)
        if handle is None or handle.task is None or handle.task.done():
            return False
        handle.task.cancel()
        await asyncio.wait({handle.task})
        return True

    async def _run_one(self, handle: AgentHandle, run_child: RunChild) -> ChildResult:
        try:
            await self._acquire(handle)
            self.set_status(handle, "running")
            result = await run_child(handle)
        except asyncio.CancelledError:
            self._finish(handle, handle.result or ChildResult(
                status="stopped", report="Stopped before it started.", files_changed=[]))
            raise
        except Exception as exc:  # backstop: run_child is meant to catch its own failures
            logger.exception("[subagent] run_child raised id=%s", handle.agent_id)
            result = handle.result or ChildResult(
                status="failed", report=f"Status: failed — {exc}", files_changed=[])
        finally:
            if handle.held:
                self._release(handle)
        self._finish(handle, result)
        return result

    def _result_of(self, handle: AgentHandle, outcome: object) -> ChildResult:
        if isinstance(outcome, ChildResult):
            return outcome
        assert handle.result is not None  # _run_one finished every path
        return handle.result

    def _finish(self, handle: AgentHandle, result: ChildResult) -> None:
        handle.result = result
        self.set_status(handle, result.status)
        logger.info("[subagent] finish id=%s parent=%s depth=%d name=%s status=%s files=%d",
                    handle.agent_id, handle.context.parent_agent_id, handle.context.depth,
                    handle.context.name, result.status, len(result.files_changed))

    async def _acquire(self, handle: AgentHandle) -> None:
        await self._slots.acquire()
        handle.held = True  # only after acquire returns: a cancel above leaves it False
        logger.info("[subagent] slot acquire id=%s held=%s", handle.agent_id, handle.held)

    def _release(self, handle: AgentHandle) -> None:
        handle.held = False
        self._slots.release()
        logger.info("[subagent] slot release id=%s held=%s", handle.agent_id, handle.held)
