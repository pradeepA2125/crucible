"""/live roster, single-agent stop, and a stopped dispatch remembered (spec §11.1, §11.4)."""
import asyncio
import json
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager
from tests.gate_helpers import first_gate


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore,
                engine: ScriptedReasoningEngine) -> ChatController:
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)


DISPATCH = {"type": "tool_call", "thought": "fan out", "tool": "dispatch_agents",
            "args": {"agents": [
                {"agent": "general-purpose", "label": "waiter", "prompt": "Run ls"},
                {"agent": "general-purpose", "label": "quick", "prompt": "Report now"}]}}
WAIT = [{"type": "tool_call", "thought": "t", "tool": "run_command",
         "args": {"command": "ls"}},
        {"type": "report", "thought": "t", "summary": "Listed."}]
QUICK = [{"type": "report", "thought": "t", "summary": "Quick done."}]
DONE = {"type": "submit_changes", "thought": "d", "summary": "ok"}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[DISPATCH, DONE],
                                     agent_scripts={"waiter": WAIT, "quick": QUICK})
    return _controller(ws, tmp_path, store, engine), store, tid


async def _wait_for_gate(store: ChatThreadStore, tid: str):
    for _ in range(400):
        await asyncio.sleep(0.005)
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            return gate
    raise AssertionError("the waiter never raised its command gate")


@pytest.mark.asyncio
async def test_live_roster_then_stopping_one_agent(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch)
    turn = asyncio.create_task(ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"))
    gate = await _wait_for_gate(store, tid)
    assert gate.agent is not None and gate.agent.label == "waiter"
    roster = {a["label"]: a for a in ctrl.live_agents(tid)}
    assert roster["waiter"]["status"] == "waiting"
    assert roster["waiter"]["depth"] == 1 and roster["waiter"]["report_preview"] == ""
    assert set(roster["waiter"]) == {
        "agent_id", "parent_agent_id", "depth", "name", "label", "status", "now",
        "tool_count", "files_changed_count", "started_at", "ended_at", "report_preview"}
    assert await ctrl.stop_agent(tid, gate.agent.id) is True
    await turn
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []
    history = thread.controller_conversation_history or []
    result = next(m for m in history if m.get("tool") == "dispatch_agents")
    statuses = {e["label"]: e["status"] for e in json.loads(str(result["content"]))}
    assert statuses == {"waiter": "stopped", "quick": "completed"}
    assert ctrl.live_agents(tid) == []  # the turn is over
    assert await ctrl.stop_agent(tid, gate.agent.id) is False


@pytest.mark.asyncio
async def test_a_stopped_turn_remembers_its_dispatch(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch)
    ctrl.launch_turn(tid, ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}"),
                     channel_id=f"chat:{tid}")
    await _wait_for_gate(store, tid)
    assert await ctrl.stop_turn(tid) is True
    rows = {r.label: r for r in store.list_agents(tid)}
    assert rows["waiter"].status == "stopped"
    assert rows["waiter"].report.startswith("Status: stopped — stopped before reporting")
    history = store.get_thread(tid).controller_conversation_history or []
    assert json.loads(str(history[-2]["content"]))["tool"] == "dispatch_agents"
    assert history[-1]["tool"] == "dispatch_agents"
    entries = json.loads(str(history[-1]["content"]))
    assert {e["label"]: e["status"] for e in entries}["waiter"] == "stopped"
    assert ctrl._inflight_dispatch == {}
