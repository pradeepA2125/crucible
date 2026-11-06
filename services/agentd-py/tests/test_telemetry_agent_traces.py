"""Sub-agents and team members as their own linked traces (observability spec §6.1)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.telemetry.context import remember_turn
from agentd.telemetry.spans import agent_span
from tests.test_background_dispatch import DONE, _setup, _tool


def _one(spans: list[Any], name: str) -> Any:
    found = [s for s in spans if s.name == name]
    assert len(found) == 1, [s.name for s in spans]
    return found[0]


def _user_id(store: Any, tid: str) -> str:
    return str([m.id for m in store.get_thread(tid).messages if m.role == "user"][-1])


@pytest.mark.asyncio
async def test_dispatched_agent_is_its_own_linked_trace(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout",
                                          "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    main_chose_wait = asyncio.Event()
    scripted_step = ScriptedReasoningEngine.create_controller_step

    async def gated_step(self: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(kwargs.get("render_ctx"), "agent_label", None) == "scout":
            await main_chose_wait.wait()
        resp = await scripted_step(self, *args, **kwargs)
        calls = resp.get("calls")
        if isinstance(calls, list) and any(c.get("tool") == "wait_agents" for c in calls):
            main_chose_wait.set()
        return resp

    monkeypatch.setattr(ScriptedReasoningEngine, "create_controller_step", gated_step)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    spans = list(test_exporter.get_finished_spans())
    turn = _one(spans, "chat.turn")
    child = _one(spans, "subagent:scout")
    assert child.parent is None and child.context.trace_id != turn.context.trace_id
    assert [link.context.span_id for link in child.links] == [turn.context.span_id]
    assert child.attributes["openinference.span.kind"] == "AGENT"
    assert child.attributes["session.id"] == tid
    assert child.attributes["crucible.request_id"] == _user_id(store, tid)
    assert child.attributes["crucible.agent_kind"] == "subagent"


@pytest.mark.asyncio
async def test_agent_outliving_a_stopped_turn_is_not_parented_under_it(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "general-purpose", "label": "slow",
                                          "prompt": "run"}]),
        _tool("wait_agents")],
        {"slow": [_tool("run_command", command="sleep", args=["30"])]})
    turn_task = ctrl.launch_turn(
        tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"), channel_id=f"chat:{tid}")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if store.list_agents(tid) and store.list_agents(tid)[0].status == "waiting":
            break
    assert await ctrl.stop_turn(tid)
    await asyncio.gather(turn_task, return_exceptions=True)
    assert await ctrl.stop_all_agents(tid) == 1
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(s.name == "subagent:slow" for s in test_exporter.get_finished_spans()):
            break
    spans = list(test_exporter.get_finished_spans())
    turn = _one(spans, "chat.turn")
    child = _one(spans, "subagent:slow")
    assert turn.status.description == "cancelled"
    assert child.parent is None and child.context.trace_id != turn.context.trace_id


def test_team_member_span_names_the_team(test_exporter: Any) -> None:
    remember_turn("turn-1", None)
    with agent_span(thread_id="t", agent_id="a1", label="builder", kind="team_member",
                    team_name="game", link_turn_id="turn-1", request_id="m1"):
        pass
    (span,) = test_exporter.get_finished_spans()
    assert span.name == "team:game/builder" and span.parent is None and not span.links
    assert span.attributes["crucible.agent_kind"] == "team_member"
    assert span.attributes["crucible.agent_id"] == "a1"


def test_agent_span_is_a_no_op_without_tracing() -> None:
    with agent_span(thread_id="t", agent_id="a1", label="x", kind="subagent", team_name=None,
                    link_turn_id=None, request_id=None):
        pass
