"""Caps bound how many agents a model can start (spec §3.11)."""
import pytest

from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.tool_source import SubAgentToolSource


@pytest.mark.asyncio
async def test_too_many_agents_in_one_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_PER_DISPATCH", "2")
    source = SubAgentToolSource(BUILTIN_AGENTS, dispatch=None)  # type: ignore[arg-type]
    out = await source.execute("dispatch_agents", {"agents": [
        {"agent": "explore", "prompt": f"p{i}"} for i in range(3)]})
    assert out.is_error and "at most 2 agents per call" in out.output


@pytest.mark.asyncio
async def test_the_thread_cap_is_reported_to_the_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from agentd.subagents.config import DispatchCapExceeded

    async def dispatch(requests):  # type: ignore[no-untyped-def]
        raise DispatchCapExceeded("this thread already has 16 agents running — wait for some "
                                  "to finish or stop them")

    source = SubAgentToolSource(BUILTIN_AGENTS, dispatch=dispatch)
    out = await source.execute("dispatch_agents", {"agents": [{"agent": "explore", "prompt": "p"}]})
    assert out.is_error and "16 agents running" in out.output
