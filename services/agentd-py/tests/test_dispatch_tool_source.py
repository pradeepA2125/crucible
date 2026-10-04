"""The four sub-agent tools (spec §4.1)."""
import json

import pytest

from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.framing import frame
from agentd.subagents.runtime import AgentBusyError, DispatchRequest
from agentd.subagents.tool_source import (
    AgentResultEntry,
    QueuedAgent,
    SubAgentOps,
    SubAgentToolSource,
)


class _Ops:
    def __init__(self) -> None:
        self.dispatched: list[tuple[list[DispatchRequest], dict[str, str]]] = []
        self.waited: list[tuple[list[str] | None, float | None]] = []

    async def dispatch(self, requests, on_finish):  # type: ignore[no-untyped-def]
        self.dispatched.append((requests, on_finish))
        return [QueuedAgent(agent_id=f"agent-{i}", label=r.label, name=r.agent.name)
                for i, r in enumerate(requests)]

    async def wait(self, agent_ids, timeout):  # type: ignore[no-untyped-def]
        self.waited.append((agent_ids, timeout))
        return [AgentResultEntry(agent_id="agent-0", label="a", name="explore",
                                 status="completed", report="R" * 40_000,
                                 files_changed=["b.py", "a.py"], stale_refusals=1),
                AgentResultEntry(agent_id="agent-1", label="b", name="explore",
                                 status="already delivered")]

    async def message(self, agent_id, message, on_finish):  # type: ignore[no-untyped-def]
        raise AgentBusyError("agent 'a' is still running — wait for its report or stop it")

    async def stop(self, agent_id):  # type: ignore[no-untyped-def]
        return agent_id == "agent-0"


def _source(ops: _Ops) -> SubAgentToolSource:
    return SubAgentToolSource(BUILTIN_AGENTS, SubAgentOps(
        dispatch=ops.dispatch, wait=ops.wait, message=ops.message, stop=ops.stop))


def test_four_tools_and_the_catalog() -> None:
    src = _source(_Ops())
    definitions = src.definitions()
    assert [d.name for d in definitions] == [
        "dispatch_agents", "wait_agents", "message_agent", "stop_agent"]
    assert all(src.owns(d.name) for d in definitions)
    assert "- explore: Read-only investigator" in definitions[0].description
    agent_schema = definitions[0].parameters["properties"]["agents"]["items"]["properties"]["agent"]
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
    ops = _Ops()
    out = await _source(ops).execute("dispatch_agents", args)
    assert out.is_error
    assert out.output == f"Error: {problem}. Valid agents: explore, general-purpose."
    assert ops.dispatched == []


@pytest.mark.asyncio
async def test_dispatch_returns_at_once() -> None:
    ops = _Ops()
    out = await _source(ops).execute("dispatch_agents", {"agents": [
        {"agent": "explore", "prompt": "find X", "on_finish": "wake"},
        {"agent": "general-purpose", "prompt": "fix Y"}]})
    assert not out.is_error
    assert json.loads(out.output) == [
        {"agent_id": "agent-0", "label": "explore-1", "agent": "explore", "status": "queued"},
        {"agent_id": "agent-1", "label": "general-purpose-1", "agent": "general-purpose",
         "status": "queued"}]
    assert ops.dispatched[0][1] == {"explore-1": "wake", "general-purpose-1": "notify"}


@pytest.mark.asyncio
async def test_wait_frames_full_reports_and_reports_changed_files() -> None:
    ops = _Ops()
    out = await _source(ops).execute("wait_agents", {"agent_ids": ["agent-0"], "timeout_sec": 30})
    entries = json.loads(out.output)
    assert entries[0]["report"] == frame("a (explore)", "report", "R" * 40_000)
    assert entries[1] == {"agent_id": "agent-1", "label": "b", "agent": "explore",
                          "status": "already delivered — see the earlier message"}
    assert out.workspace_changes == ["a.py", "b.py"]
    assert ops.waited == [(["agent-0"], 30.0)]


@pytest.mark.asyncio
async def test_refusals_become_tool_errors() -> None:
    out = await _source(_Ops()).execute("message_agent", {"agent_id": "agent-0", "message": "go"})
    assert out.is_error and "still running" in out.output


@pytest.mark.asyncio
async def test_stop_agent() -> None:
    src = _source(_Ops())
    assert json.loads((await src.execute("stop_agent", {"agent_id": "agent-0"})).output) == {
        "agent_id": "agent-0", "stopped": True}


def test_a_user_stop_tells_the_model_not_to_restart() -> None:
    from agentd.subagents.tool_source import format_wait_result

    [entry] = json.loads(format_wait_result([AgentResultEntry(
        agent_id="a", label="lister", name="general-purpose", status="stopped",
        report="Status: stopped", stop_reason="user")]))
    assert entry["stopped_by"] == "user"
    assert "do not restart" in entry["note"].lower()
    [cascade] = json.loads(format_wait_result([AgentResultEntry(
        agent_id="b", label="kid", name="explore", status="stopped", report="r",
        stop_reason="cascade")]))
    assert "note" not in cascade and cascade["stopped_by"] == "cascade"
