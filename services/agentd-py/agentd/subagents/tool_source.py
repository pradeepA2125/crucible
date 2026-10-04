"""The sub-agent tools (spec §4.1): dispatch_agents, wait_agents, message_agent, stop_agent."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agentd.subagents.config import DispatchCapExceeded, subagent_max_per_dispatch
from agentd.subagents.definitions import AgentDefinition
from agentd.subagents.framing import frame
from agentd.subagents.runtime import (
    AgentBusyError,
    AgentNotFoundError,
    AgentNotYoursError,
    DispatchRequest,
)
from agentd.tools.registry import ToolDefinition, ToolOutput

DISPATCH_TOOL_NAME = "dispatch_agents"
WAIT_TOOL_NAME = "wait_agents"
MESSAGE_TOOL_NAME = "message_agent"
STOP_TOOL_NAME = "stop_agent"
_TOOLS = (DISPATCH_TOOL_NAME, WAIT_TOOL_NAME, MESSAGE_TOOL_NAME, STOP_TOOL_NAME)
_ON_FINISH = ("notify", "wake")
_REFUSALS = (DispatchCapExceeded, AgentNotFoundError, AgentNotYoursError, AgentBusyError)


@dataclass(frozen=True)
class QueuedAgent:
    agent_id: str
    label: str
    name: str
    warning: str = ""   # e.g. a workspace definition overriding a built-in (spec §3.12)


@dataclass(frozen=True)
class AgentResultEntry:
    agent_id: str
    label: str
    name: str
    status: str          # an idle status, "still running", or "already delivered"
    report: str = ""     # full, never truncated; framed when written into the result
    files_changed: list[str] = field(default_factory=list)
    stale_refusals: int = 0
    stop_reason: str = ""  # "user" or "cascade" when status is "stopped"


@dataclass(frozen=True)
class SubAgentOps:
    """What the tools do, supplied by the controller per caller (main or a child)."""
    dispatch: Callable[[list[DispatchRequest], dict[str, str]], Awaitable[list[QueuedAgent]]]
    wait: Callable[[list[str] | None, float | None], Awaitable[list[AgentResultEntry]]]
    message: Callable[[str, str, str | None], Awaitable[QueuedAgent]]
    stop: Callable[[str], Awaitable[bool]]


def format_queued(agents: list[QueuedAgent]) -> str:
    out: list[dict[str, str]] = []
    for a in agents:
        entry = {"agent_id": a.agent_id, "label": a.label, "agent": a.name, "status": "queued"}
        if a.warning:
            entry["warning"] = a.warning
        out.append(entry)
    return json.dumps(out, indent=2)


def format_wait_result(entries: list[AgentResultEntry]) -> str:
    """One entry per agent (spec §4.2): full reports, framed as data (§3.10)."""
    out: list[dict[str, object]] = []
    for e in entries:
        base: dict[str, object] = {"agent_id": e.agent_id, "label": e.label, "agent": e.name}
        if e.status == "already delivered":
            out.append({**base, "status": "already delivered — see the earlier message"})
        elif e.status == "still running":
            out.append({**base, "status": "still running"})
        else:
            entry: dict[str, object] = {
                **base, "status": e.status,
                "report": frame(f"{e.label} ({e.name})", "report", e.report),
                "files_changed": e.files_changed, "stale_refusals": e.stale_refusals}
            if e.stop_reason:
                entry["stopped_by"] = e.stop_reason
            if e.stop_reason == "user":
                # Found live: a model that saw only "stopped" re-dispatched the same work.
                entry["note"] = ("Stopped by the user. Do not restart this work unless the "
                                 "user asks for it.")
            out.append(entry)
    return json.dumps(out, indent=2)


class SubAgentToolSource:
    name = "subagents"

    def __init__(self, catalog: dict[str, AgentDefinition], ops: SubAgentOps) -> None:
        self._catalog = catalog
        self._ops = ops

    def definitions(self) -> list[ToolDefinition]:
        def line(d: AgentDefinition) -> str:
            if d.trust == "capped":
                return f"- {d.name}:\n" + frame(f"definition {d.source} (untrusted)",
                                                  "agent description", d.description)
            return f"- {d.name}: {d.description}"
        lines = "\n".join(line(d) for d in self._catalog.values())
        return [
            ToolDefinition(
                name=DISPATCH_TOOL_NAME,
                description=(
                    "Start sub-agents in the background. Each starts with ONLY the prompt you "
                    "write. Returns at once with each agent's id; call wait_agents to collect "
                    "their reports. on_finish: \"notify\" (default) holds a report finished "
                    "after your turn until the user's next message; \"wake\" starts a turn for "
                    f"you when it arrives. Available agents:\n{lines}"),
                parameters={"type": "object", "properties": {"agents": {
                    "type": "array", "items": {"type": "object", "properties": {
                        "agent": {"type": "string", "enum": list(self._catalog)},
                        "prompt": {"type": "string"},
                        "label": {"type": "string"},
                        "on_finish": {"type": "string", "enum": list(_ON_FINISH)},
                    }, "required": ["agent", "prompt"]}}},
                    "required": ["agents"]}),
            ToolDefinition(
                name=WAIT_TOOL_NAME,
                description=(
                    "Wait for agents you dispatched and get their full reports and changed "
                    "files. agent_ids defaults to every agent of yours still running. With "
                    "timeout_sec, returns what has finished and lists the rest as still "
                    "running."),
                parameters={"type": "object", "properties": {
                    "agent_ids": {"type": "array", "items": {"type": "string"}},
                    "timeout_sec": {"type": "number"}}}),
            ToolDefinition(
                name=MESSAGE_TOOL_NAME,
                description=(
                    "Send a follow-up to an agent you dispatched that has finished (or "
                    "failed, or was stopped). It continues with everything it already read "
                    "and did. Returns at once; collect the new report with wait_agents."),
                parameters={"type": "object", "properties": {
                    "agent_id": {"type": "string"}, "message": {"type": "string"},
                    "on_finish": {"type": "string", "enum": list(_ON_FINISH)}},
                    "required": ["agent_id", "message"]}),
            ToolDefinition(
                name=STOP_TOOL_NAME,
                description="Stop an agent you dispatched, and every agent it started.",
                parameters={"type": "object", "properties": {"agent_id": {"type": "string"}},
                            "required": ["agent_id"]}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in _TOOLS

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == DISPATCH_TOOL_NAME:
                return await self._dispatch(args)
            if tool == WAIT_TOOL_NAME:
                return await self._wait(args)
            if tool == MESSAGE_TOOL_NAME:
                return await self._message(args)
            if tool == STOP_TOOL_NAME:
                agent_id = str(args.get("agent_id", ""))
                return ToolOutput(output=json.dumps(
                    {"agent_id": agent_id, "stopped": await self._ops.stop(agent_id)}))
        except _REFUSALS as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)

    async def _dispatch(self, args: dict[str, object]) -> ToolOutput:
        requests, on_finish, problem = self._parse(args)
        if problem is not None:
            valid = ", ".join(self._catalog)
            return ToolOutput(output=f"Error: {problem}. Valid agents: {valid}.", is_error=True)
        return ToolOutput(output=format_queued(await self._ops.dispatch(requests, on_finish)))

    async def _wait(self, args: dict[str, object]) -> ToolOutput:
        raw_ids = args.get("agent_ids")
        ids = [str(i) for i in raw_ids] if isinstance(raw_ids, list) else None
        raw_timeout = args.get("timeout_sec")
        timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float)) else None
        entries = await self._ops.wait(ids, timeout)
        changed = sorted({f for e in entries for f in e.files_changed})
        return ToolOutput(output=format_wait_result(entries), workspace_changes=changed)

    async def _message(self, args: dict[str, object]) -> ToolOutput:
        on_finish = str(args.get("on_finish") or "")
        queued = await self._ops.message(
            str(args.get("agent_id", "")), str(args.get("message", "")),
            on_finish if on_finish in _ON_FINISH else None)
        return ToolOutput(output=format_queued([queued]))

    def _parse(
        self, args: dict[str, object],
    ) -> tuple[list[DispatchRequest], dict[str, str], str | None]:
        raw = args.get("agents")
        if not isinstance(raw, list) or not raw:
            return [], {}, "'agents' must be a non-empty list"
        if len(raw) > subagent_max_per_dispatch():
            return [], {}, f"at most {subagent_max_per_dispatch()} agents per call"
        requests: list[DispatchRequest] = []
        on_finish: dict[str, str] = {}
        labels: set[str] = set()
        counts: dict[str, int] = {}
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return [], {}, f"agents[{i}] must be an object"
            name = str(item.get("agent", ""))
            definition = self._catalog.get(name)
            if definition is None:
                return [], {}, f"agents[{i}]: unknown agent {name!r}"
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                return [], {}, f"agents[{i}]: 'prompt' is empty"
            counts[name] = counts.get(name, 0) + 1
            label = str(item.get("label") or "").strip() or f"{name}-{counts[name]}"
            if label in labels:
                return [], {}, f"agents[{i}]: duplicate label {label!r}"
            labels.add(label)
            choice = str(item.get("on_finish") or "notify")
            on_finish[label] = choice if choice in _ON_FINISH else "notify"
            requests.append(DispatchRequest(agent=definition, prompt=prompt, label=label))
        return requests, on_finish, None
