"""dispatch_agents (spec §6.1, §6.3)."""
import json

import pytest

from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.runtime import AgentHandle, ChildResult, DispatchRequest
from agentd.subagents.tool_source import SubAgentToolSource


def _fake_dispatch(results: dict[str, ChildResult]):
    calls: list[list[DispatchRequest]] = []

    async def dispatch(requests: list[DispatchRequest]):
        calls.append(requests)
        out = []
        for i, req in enumerate(requests):
            ctx = AgentContext(agent_id=f"agent-{i}", name=req.agent.name, label=req.label,
                               depth=1, parent_agent_id=None, permission=req.agent.permission,
                               allowed_types=("tool_call", "report"), persona="", max_iters=5)
            handle = AgentHandle(context=ctx, definition=req.agent, prompt=req.prompt,
                                 thread_id="t", turn_id="u")
            out.append((handle, results[req.label]))
        return out
    return dispatch, calls


def test_the_definition_carries_the_catalog() -> None:
    src = SubAgentToolSource(BUILTIN_AGENTS, _fake_dispatch({})[0])
    [definition] = src.definitions()
    assert definition.name == "dispatch_agents" and src.owns("dispatch_agents")
    assert "- explore: Read-only investigator" in definition.description
    assert "- general-purpose: Implements one self-contained part" in definition.description
    agent_schema = definition.parameters["properties"]["agents"]["items"]["properties"]["agent"]
    assert agent_schema["enum"] == ["explore", "general-purpose"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args,problem", [
    ({}, "'agents' must be a non-empty list"),
    ({"agents": []}, "'agents' must be a non-empty list"),
    ({"agents": ["x"]}, "agents[0] must be an object"),
    ({"agents": [{"agent": "nope", "prompt": "p"}]}, "agents[0]: unknown agent 'nope'"),
    ({"agents": [{"agent": "explore", "prompt": "  "}]}, "agents[0]: 'prompt' is empty"),
    ({"agents": [{"agent": "explore", "prompt": "p", "label": "x"},
                 {"agent": "explore", "prompt": "q", "label": "x"}]},
     "agents[1]: duplicate label 'x'"),
])
async def test_validation_errors_never_dispatch(args: dict[str, object], problem: str) -> None:
    dispatch, calls = _fake_dispatch({})
    out = await SubAgentToolSource(BUILTIN_AGENTS, dispatch).execute("dispatch_agents", args)
    assert out.is_error
    assert out.output == f"Error: {problem}. Valid agents: explore, general-purpose."
    assert calls == []


@pytest.mark.asyncio
async def test_default_labels_full_reports_and_workspace_changes() -> None:
    long_report = "R" * 40_000
    dispatch, calls = _fake_dispatch({
        "explore-1": ChildResult(status="completed", report="found it", files_changed=[]),
        "general-purpose-1": ChildResult(status="partial", report=long_report,
                                         files_changed=["b.py", "a.py"], stale_refusals=1),
    })
    out = await SubAgentToolSource(BUILTIN_AGENTS, dispatch).execute("dispatch_agents", {
        "agents": [{"agent": "explore", "prompt": "find X"},
                   {"agent": "general-purpose", "prompt": "fix Y"}]})
    assert not out.is_error
    assert [r.label for r in calls[0]] == ["explore-1", "general-purpose-1"]
    entries = json.loads(out.output)
    assert entries[1] == {"agent_id": "agent-1", "agent": "general-purpose",
                          "label": "general-purpose-1", "status": "partial",
                          "report": long_report, "files_changed": ["b.py", "a.py"],
                          "stale_refusals": 1}
    assert out.workspace_changes == ["a.py", "b.py"]
