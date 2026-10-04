"""Dispatch returns at once; wait, message and stop (spec §4)."""
import asyncio
import json
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_agent_activation import _controller

DONE = {"type": "submit_changes", "thought": "d", "summary": "done"}


def _tool(tool: str, **args: object) -> dict[str, object]:
    return {"type": "tool_call", "thought": "t", "tool": tool, "args": args}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, main, kids):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=main,
                                     agent_scripts=kids)
    return _controller(ws, tmp_path, store, engine), store, tid


def _tool_results(store: ChatThreadStore, tid: str, tool: str) -> list[object]:
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    return [json.loads(str(m["content"])) for m in history if m.get("tool") == tool]


@pytest.mark.asyncio
async def test_dispatch_then_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [queued] = _tool_results(store, tid, "dispatch_agents")
    assert queued[0]["status"] == "queued"
    [waited] = _tool_results(store, tid, "wait_agents")
    assert waited[0]["status"] == "completed" and "found it" in waited[0]["report"]
    [row] = store.list_agents(tid)
    assert row.report_delivered_at is not None and row.on_finish == "notify"


@pytest.mark.asyncio
async def test_a_second_wait_says_already_delivered(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    entries = await ctrl._agent_ops(tid, "t2", None).wait([row.agent_id], None)
    assert [e.status for e in entries] == ["already delivered"]


@pytest.mark.asyncio
async def test_message_agent_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "one"},
                          {"type": "report", "thought": "t", "summary": "two"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    ops = ctrl._agent_ops(tid, "t2", None)
    queued = await ops.message(row.agent_id, "again", "wake")
    assert queued.agent_id == row.agent_id
    [entry] = await ops.wait([row.agent_id], None)
    assert entry.report == "two" and store.get_agent(row.agent_id).on_finish == "wake"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_childs_late_report_reaches_its_inbox(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from agentd.subagents.runtime import ChildResult

    ctrl, store, tid = _setup(tmp_path, monkeypatch, [DONE], {})
    delivered: list[tuple[str, str]] = []
    ctrl._subagents.deliver = lambda agent_id, item: delivered.append((agent_id, item.text))  # type: ignore[union-attr, method-assign]
    from agentd.chat.models import AgentRecord
    store.insert_agent(AgentRecord(agent_id="lead", thread_id=tid, turn_id="u", depth=1,
                                   name="general-purpose", label="lead", prompt="p",
                                   status="running", dispatcher_id="main"))
    store.insert_agent(AgentRecord(agent_id="kid", thread_id=tid, turn_id="u", depth=2,
                                   parent_agent_id="lead", name="explore", label="kid",
                                   prompt="p", status="completed", dispatcher_id="lead"))
    handle = ctrl._handle_from_record(tid, "kid")
    ctrl._route_report(handle, ChildResult(status="completed", report="kid done",
                                           files_changed=[]))
    assert delivered == [("lead", "kid done")]
    ctrl._waiting[(tid, "lead")] = {"kid"}
    ctrl._route_report(handle, ChildResult(status="completed", report="again",
                                           files_changed=[]))
    assert delivered == [("lead", "kid done")]   # a waiting dispatcher gets it from the wait


@pytest.mark.asyncio
async def test_stopping_the_turn_leaves_agents_running_and_stop_all_stops_them(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "general-purpose", "label": "slow",
                                          "prompt": "run"}]),
        _tool("wait_agents")],
        {"slow": [_tool("run_command", command="sleep", args=["30"])]})
    turn = ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"),
                            channel_id=f"chat:{tid}")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if store.list_agents(tid) and store.list_agents(tid)[0].status == "waiting":
            break
    assert await ctrl.stop_turn(tid)
    await asyncio.gather(turn, return_exceptions=True)
    [row] = store.list_agents(tid)
    assert row.status == "waiting"                 # still parked at its command card
    assert await ctrl.stop_all_agents(tid) == 1
    assert store.get_agent(row.agent_id).status == "stopped"  # type: ignore[union-attr]
    # The next wait says who stopped it, so the model does not restart the work.
    [entry] = await ctrl._agent_ops(tid, "t2", None).wait([row.agent_id], None)
    assert (entry.status, entry.stop_reason) == ("stopped", "user")
