"""Requests and tokens are counted per owner (spec §3.11)."""
from pathlib import Path

import pytest

from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.providers.usage import METER, USAGE_OWNER, Usage, UsageMeter
from tests.test_agent_activation import _controller, _dispatch


def test_meter_records_and_takes() -> None:
    meter = UsageMeter()
    meter.record("agent-a", requests=1, wait_ms=5)
    meter.record("agent-a", prompt=100, completion=20)
    meter.record(None, requests=1)  # no owner: dropped
    assert meter.take("agent-a") == Usage(requests=1, prompt_tokens=100,
                                          completion_tokens=20, wait_ms=5)
    assert meter.take("agent-a") == Usage()


def test_store_increments(tmp_path: Path) -> None:
    from agentd.chat.models import AgentRecord
    from agentd.chat.storage import ChatThreadStore

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.insert_agent(AgentRecord(agent_id="a", thread_id=tid, turn_id="u", depth=1,
                                   name="general-purpose", label="a", prompt="p",
                                   status="queued"))
    store.add_agent_usage("a", Usage(requests=2, prompt_tokens=10, completion_tokens=3,
                                     wait_ms=7))
    store.add_agent_usage("a", Usage(requests=1))
    rec = store.get_agent("a")
    assert rec is not None and (rec.requests, rec.prompt_tokens, rec.limiter_wait_ms) == (3, 10, 7)
    store.add_thread_usage(tid, Usage(requests=4, prompt_tokens=9))
    assert store.thread_usage(tid) == Usage(requests=4, prompt_tokens=9)


class _Metered(ScriptedReasoningEngine):
    """Stands in for the transport wrapper, which scripted engines bypass."""

    async def create_controller_step(self, plan_context, history, tool_definitions, **kwargs):  # type: ignore[no-untyped-def]
        METER.record(USAGE_OWNER.get(), requests=1, prompt=10, completion=2)
        return await super().create_controller_step(
            plan_context, history, tool_definitions, **kwargs)


@pytest.mark.asyncio
async def test_usage_lands_on_the_agent_and_the_thread(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentd.chat.storage import ChatThreadStore

    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = _Metered(None, [], controller_step_responses=[
        _dispatch("kid", "Investigate"),
        {"type": "tool_call", "thought": "collect", "tool": "wait_agents", "args": {}},
        {"type": "submit_changes", "thought": "d", "summary": "done"}],
        agent_scripts={"kid": [{"type": "report", "thought": "t", "summary": "one"}]})
    await _controller(ws, tmp_path, store, engine).handle_message(
        tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    assert (row.requests, row.prompt_tokens, row.completion_tokens) == (1, 10, 2)
    assert store.thread_usage(tid).requests == 3   # the main turn's three calls
