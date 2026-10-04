"""SubAgentRuntime (spec §6.2, §6.7): runs dispatched children under one process-wide
concurrency cap, keeps the registry, and owns slot accounting.

It knows nothing about loops, stores or gates: the controller passes `run_child` (build and
run one child) and `on_status` (persist + broadcast a status change).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import AgentDefinition
from agentd.subagents.inbox import InboxItem

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
    stop_reason: str = "user"   # user | cascade | deadline | budget | disband (spec §3.3)
    activation_input: str = ""  # what this activation was started with
    # Restrictions inherited from the dispatcher, persisted on the row (spec §3.12).
    inherited: dict[str, bool] = field(default_factory=dict)

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


class AgentNotFoundError(LookupError):
    """No agent with that id in this thread."""


class AgentNotYoursError(PermissionError):
    """Only an agent's dispatcher may resume it (spec §4.3)."""


class AgentBusyError(RuntimeError):
    """The agent is queued or running; wait for its report or stop it first."""


class ActivationInProgress(RuntimeError):
    """An agent has at most one queued-or-running activation (spec §3.4); new input for a
    running agent goes to its inbox instead."""


ChildrenOf = Callable[[str], list[str]]
LeftoverSink = Callable[["AgentHandle", list[InboxItem]], None]


class AgentSupervisor:
    """Runs agent activations under one process-wide concurrency cap (spec §3.4).

    It knows nothing about loops, stores or gates: callers pass `run_child` (build and run
    one activation), `on_status` (persist + broadcast), `children_of` (the agent tree, read
    from the database because idle agents have no handle) and `on_leftover` (start the next
    activation for input that arrived after the last drain)."""

    def __init__(
        self, max_concurrent: int, on_status: StatusSink | None = None,
        children_of: ChildrenOf | None = None, on_leftover: LeftoverSink | None = None,
    ) -> None:
        # Process-wide on purpose: the cap protects the provider's rate limit. `main` never
        # holds a slot. Bounded, so any accounting bug is a loud ValueError.
        self._slots = asyncio.BoundedSemaphore(max_concurrent)
        self._on_status = on_status
        self._children_of = children_of or (lambda _agent_id: [])
        self._on_leftover = on_leftover
        self._inboxes: dict[str, list[InboxItem]] = {}
        self.registry = AgentRegistry()

    def set_status(self, handle: AgentHandle, status: str) -> None:
        handle.status = status
        if status == "running" and handle.started_at is None:
            handle.started_at = datetime.now(UTC)
        if status in IDLE_STATUSES:
            handle.ended_at = datetime.now(UTC)
        if self._on_status is not None:
            self._on_status(handle)

    def is_active(self, agent_id: str) -> bool:
        handle = self.registry.get(agent_id)
        return handle is not None and handle.task is not None and not handle.task.done()

    def enqueue(self, handle: AgentHandle, run_child: RunChild) -> asyncio.Task[ChildResult]:
        if self.is_active(handle.agent_id):
            raise ActivationInProgress(
                f"agent {handle.agent_id} is still running — its input goes to its inbox")
        self.registry.add(handle)
        handle.result = None
        handle.status = "queued"
        task = asyncio.create_task(self._run_one(handle, run_child))
        handle.task = task
        return task

    async def wait(
        self, handles: list[AgentHandle], *, dispatcher: AgentHandle | None = None,
    ) -> list[ChildResult]:
        if dispatcher is not None and dispatcher.held:
            # A dispatcher waiting on its agents must not pin a slot they may need.
            self._release(dispatcher)
        tasks = [h.task for h in handles if h.task is not None]
        # A cancel of THIS await (the dispatcher stopping) propagates; the dispatcher then
        # never re-acquires, so `held` stays False.
        await asyncio.gather(*tasks, return_exceptions=True)
        if dispatcher is not None:
            await self._acquire(dispatcher)
        return [self._result_of(h) for h in handles]

    async def wait_for(
        self, handles: list[AgentHandle], *, dispatcher: AgentHandle | None = None,
        timeout: float | None = None,
    ) -> list[ChildResult | None]:
        """`wait_agents` (spec §4.2): the results that are in by the timeout; None for an
        agent still running. The dispatcher lends its slot while it waits, as in `wait`."""
        if dispatcher is not None and dispatcher.held:
            self._release(dispatcher)
        tasks = [h.task for h in handles if h.task is not None and not h.task.done()]
        # Not try/finally: a cancel of THIS await (the dispatcher stopping) propagates and
        # the dispatcher never re-acquires — the same contract as `wait`.
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        if dispatcher is not None:
            await self._acquire(dispatcher)
        return [h.result if h.task is not None and h.task.done() else None for h in handles]

    async def dispatch(
        self, handles: list[AgentHandle], run_child: RunChild, *,
        dispatcher: AgentHandle | None = None,
    ) -> list[ChildResult]:
        """v1's dispatch-and-wait, kept until always-background dispatch (Phase 2)."""
        for handle in handles:
            self.enqueue(handle, run_child)
        return await self.wait(handles, dispatcher=dispatcher)

    async def stop(self, agent_id: str, reason: str = "user") -> bool:
        """Stop the agent's queued or running descendants (deepest first), then the agent
        (spec §3.4) — an independently scheduled agent must not outlive its dispatcher."""
        for child_id in self._children_of(agent_id):
            await self.stop(child_id, "cascade")
        handle = self.registry.get(agent_id)
        if handle is None or handle.task is None or handle.task.done():
            return False
        handle.stop_reason = reason
        handle.task.cancel()
        await asyncio.wait({handle.task})
        return True

    async def stop_agent(self, agent_id: str) -> bool:
        return await self.stop(agent_id, "user")

    def deliver(self, agent_id: str, item: InboxItem) -> None:
        self._inboxes.setdefault(agent_id, []).append(item)
        if not self.is_active(agent_id):
            handle = self.registry.get(agent_id)
            if handle is not None:
                self._hand_back_leftovers(handle)

    def drain(self, agent_id: str) -> list[InboxItem]:
        """All notes plus at most one report: reports are appended one per iteration so the
        memory compactor can run between large ones (spec §3.6)."""
        pending = self._inboxes.get(agent_id, [])
        taken: list[InboxItem] = []
        kept: list[InboxItem] = []
        report_taken = False
        for item in pending:
            if item.kind == "report":
                if report_taken:
                    kept.append(item)
                    continue
                report_taken = True
            taken.append(item)
        self._inboxes[agent_id] = kept
        return taken

    def discard_reports(self, agent_id: str, source_ids: set[str]) -> None:
        """A wait_agents result already carried these reports (spec §4.2): drop the inbox
        copies so the dispatcher never reads one twice."""
        self._inboxes[agent_id] = [
            i for i in self._inboxes.get(agent_id, [])
            if not (i.kind == "report" and i.source_id in source_ids)]

    def has_pending_report(self, agent_id: str) -> bool:
        return any(i.kind == "report" for i in self._inboxes.get(agent_id, []))

    def _hand_back_leftovers(self, handle: AgentHandle) -> None:
        items = self._inboxes.get(handle.agent_id, [])
        if self._on_leftover is None or not any(i.wakes for i in items):
            return
        self._inboxes[handle.agent_id] = []
        self._on_leftover(handle, items)

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
        self._hand_back_leftovers(handle)
        return result

    def _result_of(self, handle: AgentHandle) -> ChildResult:
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


# Existing imports keep working; new code uses AgentSupervisor.
SubAgentRuntime = AgentSupervisor
