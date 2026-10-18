"""Validated general tool-call graphs and bounded, workspace-aware execution."""

from __future__ import annotations

import asyncio
import os
import shlex
import weakref
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agentd.performance import span
from agentd.tools.arg_aliases import normalize_tool_args
from agentd.tools.registry import ToolDefinition, ToolOutput

MAX_CALLS = 8
MAX_ACTIVE = 4
RESULT_BUDGET = 64_000
output_budget: ContextVar[int] = ContextVar("tool_output_budget", default=100_000)
Prepared = Callable[[], Awaitable[ToolOutput]]


def batching_enabled() -> bool:
    return os.environ.get("CRUCIBLE_TOOL_BATCHING_ENABLED", "").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Call:
    id: str
    tool: str
    args: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    execution: str = "auto"
    provider_call_id: str | None = None


def validate_calls(
    raw: Any,
    definitions: list[ToolDefinition],
    *,
    remaining: int = MAX_CALLS,
    guard: Callable[[dict[str, Any]], str | None] | None = None,
) -> list[Call]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= min(MAX_CALLS, remaining):
        raise ValueError(f"calls must contain 1–{min(MAX_CALLS, remaining)} members")
    visible = {d.name: d for d in definitions}
    calls = []
    ids: set[str] = set()
    provider_ids: set[str] = set()
    for node in raw:
        if not isinstance(node, dict):
            raise ValueError("each call must be an object")
        if set(node) - {
            "id",
            "tool",
            "args",
            "depends_on",
            "after",
            "execution",
            "provider_call_id",
        }:
            raise ValueError("unexpected call fields")
        identity, tool, args = node.get("id"), node.get("tool"), node.get("args")
        if not isinstance(identity, str) or not identity or len(identity) > 80 or identity in ids:
            raise ValueError("call IDs must be nonempty, unique strings of at most 80 characters")
        ids.add(identity)
        if not isinstance(tool, str) or tool not in visible or not isinstance(args, dict):
            raise ValueError(f"call {identity}: tool must be visible and args must be an object")
        args = normalize_tool_args(tool, args)
        errors = list(Draft202012Validator(visible[tool].parameters).iter_errors(args))
        if errors:
            # Do not echo private argument values into logs/corrections.
            raise ValueError(f"call {identity}: arguments do not match the {tool} schema")
        for field in ("depends_on", "after"):
            values = node.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError(f"call {identity}: {field} must be an array of IDs")
            if len(set(values)) != len(values):
                raise ValueError(f"call {identity}: duplicate dependencies")
        execution = node.get("execution", "auto")
        if not isinstance(execution, str) or execution not in {"auto", "parallel", "serial"}:
            raise ValueError(f"call {identity}: invalid execution mode")
        if guard:
            correction = guard({"tool": tool, "args": args})
            if correction:
                raise ValueError(f"call {identity}: {correction}")
        provider_id = node.get("provider_call_id")
        if provider_id is not None and (not isinstance(provider_id, str) or not provider_id):
            raise ValueError("invalid provider call ID")
        if provider_id is not None:
            if provider_id in provider_ids:
                raise ValueError("duplicate provider call ID")
            provider_ids.add(provider_id)
        calls.append(
            Call(
                identity,
                tool,
                args,
                tuple(node.get("depends_on", [])),
                tuple(node.get("after", [])),
                execution,
                provider_id,
            )
        )
    incoming = {c.id: set(c.depends_on + c.after) for c in calls}
    for identity, edges in incoming.items():
        if identity in edges or edges - ids:
            raise ValueError(f"call {identity}: missing or self dependency")
    visited: set[str] = set()
    while len(visited) < len(calls):
        ready = {identity for identity, edges in incoming.items() if edges <= visited} - visited
        if not ready:
            raise ValueError("tool dependency graph contains a cycle")
        visited.update(ready)
    return calls


@dataclass(frozen=True)
class Resource:
    path: Path
    write: bool


def resources_for(call: Call, shadow: Path, real: Path) -> tuple[Resource, ...]:
    shadow, real = shadow.resolve(), real.resolve()
    if call.tool in {"web_search", "web_fetch"} and call.execution != "serial":
        return ()
    if call.tool in {
        "read_file",
        "list_directory",
        "search_code",
        "search_index",
        "find_binary",
        "read_env_profile",
    }:
        root = real
        path = (
            root / str(call.args.get("path", "."))
            if call.tool in {"read_file", "list_directory"}
            else root
        )
        return (Resource(path.resolve(), call.execution == "serial"), Resource(shadow, False))
    if call.tool == "run_command" and call.execution == "parallel":
        # Explicit independence is advisory; obvious shared mutations override it.
        command = str(call.args.get("command", ""))
        args = call.args.get("args", [])
        try:
            tokens = shlex.split(command) + [str(a) for a in args]
        except ValueError:
            tokens = []
        exclusive = {
            "git",
            "setup_env",
            "init_workspace",
            "install",
            "ci",
            "add",
            "remove",
            "sync",
            "venv",
            "pip",
            "checkout",
            "push",
            "reset",
            "rm",
            "mv",
        }
        if tokens and not any(Path(token).name in exclusive for token in tokens):
            cwd = str(call.args.get("cwd") or ".")
            return (
                Resource((shadow / cwd).resolve(), True),
                Resource((real / cwd / ".venv").resolve(), True),
            )
    # Unknown tools and shared mutations remain batchable, conservatively exclusive.
    return (Resource(shadow, True), Resource(real, True))


def conflict(left: tuple[Resource, ...], right: tuple[Resource, ...]) -> bool:
    return any(
        (a.write or b.write)
        and (a.path == b.path or a.path in b.path.parents or b.path in a.path.parents)
        for a in left
        for b in right
    )


class ExecutionPool:
    def __init__(self, limit: int = MAX_ACTIVE):
        self.limit = limit
        self.condition = asyncio.Condition()
        self.active: dict[object, tuple[Resource, ...]] = {}
        self.high_water = 0
        self.exclusive: object | None = None

    @asynccontextmanager
    async def lease(self, resources: tuple[Resource, ...], *, exclusive: bool = False):
        token = object()
        async with self.condition:
            await self.condition.wait_for(
                lambda: (
                    self.exclusive is None
                    and (not exclusive or not self.active)
                    and len(self.active) < self.limit
                    and not any(conflict(resources, held) for held in self.active.values())
                )
            )
            self.active[token] = resources
            if exclusive:
                self.exclusive = token
            self.high_water = max(self.high_water, len(self.active))
        try:
            yield
        finally:
            async with self.condition:
                self.active.pop(token)
                if self.exclusive is token:
                    self.exclusive = None
                self.condition.notify_all()


# Pools live only while their event loop/workspace callers retain them.
_pools: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def workspace_pool(root: Path) -> ExecutionPool:
    loop = asyncio.get_running_loop()
    by_root = _pools.setdefault(loop, {})
    return by_root.setdefault(str(root.resolve()), ExecutionPool())


@dataclass
class Outcome:
    call: Call
    state: str = "pending"
    output: ToolOutput | None = None
    started: bool = False


class BatchUpdateError(RuntimeError):
    """Persistence/coordination failures stop the group, preserving actual tool outcomes."""


async def run_batch(
    calls: list[Call],
    *,
    prepare: Callable[[Call], Awaitable[Prepared | ToolOutput]],
    resources: Callable[[Call], tuple[Resource, ...]],
    pool: ExecutionPool,
    update: Callable[[Outcome], Awaitable[None]],
    budget: int = RESULT_BUDGET,
) -> list[Outcome]:
    outcomes = {call.id: Outcome(call) for call in calls}
    done = {call.id: asyncio.Event() for call in calls}
    share = max(512, budget // len(calls))

    async def publish(outcome):
        try:
            await update(outcome)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BatchUpdateError("batch state could not be persisted") from exc

    async def finish(outcome: Outcome, state: str, output: ToolOutput) -> None:
        outcome.state, outcome.output = state, output
        await publish(outcome)

    async def member(call: Call):
        outcome = outcomes[call.id]
        try:
            for predecessor in call.depends_on + call.after:
                await done[predecessor].wait()
            failures = [key for key in call.depends_on if outcomes[key].state != "success"]
            if failures:
                await finish(
                    outcome,
                    "blocked",
                    ToolOutput(
                        "Not executed: unsuccessful dependencies: " + ", ".join(failures),
                        True,
                        status="blocked",
                    ),
                )
                return
            if budget < 512 * len(calls):
                await finish(
                    outcome,
                    "blocked",
                    ToolOutput(
                        "Not executed: insufficient result headroom; "
                        "compact context before retrying.",
                        True,
                        status="blocked",
                    ),
                )
                return
            outcome.state = "waiting_approval"
            await publish(outcome)
            prepared = await prepare(call)
            if isinstance(prepared, ToolOutput):
                await finish(
                    outcome,
                    prepared.status or ("error" if prepared.is_error else "success"),
                    prepared,
                )
                return

            @asynccontextmanager
            async def slot():
                # Dispatch/wait may need children that use this very pool. Never hold
                # a parent workspace lease or worker while waiting for those children.
                if call.tool in {"dispatch_agents", "wait_agents"}:
                    yield
                else:
                    async with pool.lease(resources(call), exclusive=call.execution == "serial"):
                        yield

            async with slot():
                outcome.started = True
                outcome.state = "running"
                await publish(outcome)
                token = output_budget.set(share)
                try:
                    with span(f"tool.{call.tool}"):
                        result = await prepared()
                finally:
                    output_budget.reset(token)
            if len(result.output) > share and not result.whole:
                notice = "\n[Result omitted beyond aggregate budget; request the missing portion.]"
                result.output = result.output[: max(0, share - len(notice))] + notice
            await finish(
                outcome, result.status or ("error" if result.is_error else "success"), result
            )
        except asyncio.CancelledError:
            if outcome.output is None:
                await finish(
                    outcome,
                    "cancelled",
                    ToolOutput("Not completed: turn cancelled.", True, status="cancelled"),
                )
            else:
                await publish(outcome)  # preserve a result completed before persistence was stopped
            raise
        except BatchUpdateError:
            raise
        except Exception as exc:
            await finish(outcome, "error", ToolOutput(f"Tool failed: {type(exc).__name__}", True))
        finally:
            done[call.id].set()

    tasks = [asyncio.create_task(member(call)) for call in calls]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes.values():
            if outcome.output is None:
                await finish(
                    outcome,
                    "cancelled",
                    ToolOutput("Not started: turn cancelled.", True, status="cancelled"),
                )
        raise
    from agentd.performance import context_measurement

    context_measurement(
        {
            "layer": "tool_batch",
            "issued": len(calls),
            "executed": sum(o.started for o in outcomes.values()),
            "high_water": pool.high_water,
            "result_budget_chars": budget,
        }
    )
    return list(outcomes.values())
