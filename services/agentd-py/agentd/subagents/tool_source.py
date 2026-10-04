"""SubAgentToolSource: the dispatch_agents tool (spec §6.1, §6.3)."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from agentd.subagents.config import DispatchCapExceeded, subagent_max_per_dispatch
from agentd.subagents.definitions import AgentDefinition
from agentd.subagents.framing import frame
from agentd.subagents.runtime import AgentHandle, ChildResult, DispatchRequest
from agentd.tools.registry import ToolDefinition, ToolOutput

DISPATCH_TOOL_NAME = "dispatch_agents"

Dispatch = Callable[[list[DispatchRequest]], Awaitable[list[tuple[AgentHandle, ChildResult]]]]


def format_dispatch_result(pairs: list[tuple[AgentHandle, ChildResult]]) -> str:
    """The dispatch_agents tool result (spec §6.3): one entry per child, full reports."""
    return json.dumps([{
        "agent_id": handle.agent_id, "agent": handle.context.name,
        "label": handle.context.label, "status": result.status,
        "report": frame(f"{handle.context.label} ({handle.context.name})", "report",
                        result.report),
        "files_changed": result.files_changed,
        "stale_refusals": result.stale_refusals,
    } for handle, result in pairs], indent=2)


class SubAgentToolSource:
    name = "subagents"

    def __init__(self, catalog: dict[str, AgentDefinition], dispatch: Dispatch) -> None:
        self._catalog = catalog
        self._dispatch = dispatch

    def definitions(self) -> list[ToolDefinition]:
        def line(d: AgentDefinition) -> str:
            if d.trust == "capped":
                return f"- {d.name}:\n" + frame(f"definition {d.source} (untrusted)",
                                                  "agent description", d.description)
            return f"- {d.name}: {d.description}"
        lines = "\n".join(line(d) for d in self._catalog.values())
        return [ToolDefinition(
            name=DISPATCH_TOOL_NAME,
            description=(
                "Run sub-agents in parallel. Each starts with ONLY the prompt you write and "
                "returns one full report plus the files it changed; the call returns when "
                f"every agent has finished. Available agents:\n{lines}"),
            parameters={
                "type": "object",
                "properties": {"agents": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "enum": list(self._catalog)},
                        "prompt": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["agent", "prompt"],
                }}},
                "required": ["agents"],
            })]

    def owns(self, tool: str) -> bool:
        return tool == DISPATCH_TOOL_NAME

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != DISPATCH_TOOL_NAME:
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        requests, problem = self._parse(args)
        if problem is not None:
            valid = ", ".join(self._catalog)
            return ToolOutput(output=f"Error: {problem}. Valid agents: {valid}.", is_error=True)
        try:
            pairs = await self._dispatch(requests)
        except DispatchCapExceeded as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        changed = sorted({f for _, result in pairs for f in result.files_changed})
        return ToolOutput(output=format_dispatch_result(pairs), workspace_changes=changed)

    def _parse(self, args: dict[str, object]) -> tuple[list[DispatchRequest], str | None]:
        raw = args.get("agents")
        if not isinstance(raw, list) or not raw:
            return [], "'agents' must be a non-empty list"
        if len(raw) > subagent_max_per_dispatch():
            return [], f"at most {subagent_max_per_dispatch()} agents per call"
        requests: list[DispatchRequest] = []
        labels: set[str] = set()
        counts: dict[str, int] = {}
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return [], f"agents[{i}] must be an object"
            name = str(item.get("agent", ""))
            definition = self._catalog.get(name)
            if definition is None:
                return [], f"agents[{i}]: unknown agent {name!r}"
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                return [], f"agents[{i}]: 'prompt' is empty"
            counts[name] = counts.get(name, 0) + 1
            label = str(item.get("label") or "").strip() or f"{name}-{counts[name]}"
            if label in labels:
                return [], f"agents[{i}]: duplicate label {label!r}"
            labels.add(label)
            requests.append(DispatchRequest(agent=definition, prompt=prompt, label=label))
        return requests, None
