"""Gates raised by sub-agents (spec §5.6, §8)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, DiffEntry, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.events import SequencedBroadcaster
from agentd.subagents.permissions import child_allowed_types
from agentd.subagents.runtime import AgentHandle, agent_channel
from agentd.subagents.transcript import AgentTranscript
from tests.gate_helpers import first_gate


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permission: str = "default"):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None, shell_policy=ShellPolicy.ASK)
    assert ctrl._subagents is not None
    ctx = AgentContext(agent_id="agent-1", name="general-purpose", label="impl", depth=1,
                       parent_agent_id=None, permission=permission,
                       allowed_types=child_allowed_types(permission), persona="", max_iters=5)
    handle = AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"],
                         prompt="p", thread_id=tid, turn_id="turn-1", status="running")
    broadcaster = SequencedBroadcaster(ctrl._broadcaster, agent_channel(tid, "agent-1"))
    handle.broadcaster = broadcaster
    handle.transcript = AgentTranscript(lambda msgs: None, lambda: broadcaster.last_seq)
    store.insert_agent(AgentRecord(agent_id="agent-1", thread_id=tid, turn_id="turn-1",
                                   depth=1, name="general-purpose", label="impl",
                                   prompt="p", status="running"))
    ctrl._subagents.registry.add(handle)
    return ctrl, store, tid, handle


async def _gate(store: ChatThreadStore, tid: str):
    for _ in range(100):
        await asyncio.sleep(0)
        gate = first_gate(store.get_thread(tid))
        if gate is not None:
            return gate
    raise AssertionError("no gate")


@pytest.mark.asyncio
async def test_a_child_command_gate_is_tagged_and_recorded_on_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch)
    pokes = ctrl._broadcaster.subscribe(f"chat:{tid}")
    pending = asyncio.create_task(ctrl._child_command_approval_cb(handle, "ls", ["-la"], ""))
    gate = await _gate(store, tid)
    assert gate.kind == "command" and gate.agent is not None
    assert (gate.agent.id, gate.agent.label, gate.agent.name) == (
        "agent-1", "impl", "general-purpose")
    assert handle.status == "waiting" and store.get_agent("agent-1").status == "waiting"
    events = [pokes.get_nowait() for _ in range(pokes.qsize())]
    poke = next(e for e in events if e["type"] == "command_approval_requested")
    assert poke["payload"]["agent"] == {"id": "agent-1", "label": "impl",
                                        "name": "general-purpose"}
    assert await ctrl.resolve_command(tid, CommandDecision(approve=True),
                                      gate_id=gate.gate_id) is True
    assert (await pending).approved is True
    assert handle.status == "running"
    assert handle.transcript is not None
    assert [m.content for m in handle.transcript.messages] == ["✓ Command approved: ls"]
    thread = store.get_thread(tid)
    assert thread is not None
    assert not any("Command approved" in m.content for m in thread.messages)


@pytest.mark.asyncio
async def test_dont_ask_denies_by_policy_without_a_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch, permission="dontAsk")
    outcome = await ctrl._child_command_approval_cb(handle, "rm", ["-rf", "x"], "")
    assert outcome.approved is False and outcome.denied_by == "policy"
    mcp = await ctrl._child_mcp_approval_cb(handle, "gh", "create_issue", {})
    assert mcp.approved is False and mcp.denied_by == "policy"
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []


@pytest.mark.asyncio
async def test_a_child_edit_gate_records_its_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch)
    diff = [DiffEntry(path="a.py", additions=1, deletions=0, temp_path="/s/a.py",
                      unified_diff="@@\n+a")]
    pending = asyncio.create_task(ctrl._child_edit_decision_cb(handle, diff))
    gate = await _gate(store, tid)
    assert gate.kind == "edit" and gate.agent is not None
    assert gate.payload["shadow_key"] == f"chatturn-{tid}-agent-1"
    await ctrl.resolve_edit(tid, {"decision": "accept"}, gate_id=gate.gate_id)
    assert (await pending)["decision"] == "accept"


def test_status_changes_reach_the_store_and_the_thread_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, store, tid, handle = _setup(tmp_path, monkeypatch)
    events = ctrl._broadcaster.subscribe(f"chat:{tid}")
    from agentd.subagents.runtime import ChildResult
    handle.result = ChildResult(status="completed", report="done", files_changed=["a.py"],
                                stale_refusals=1)
    assert ctrl._subagents is not None
    ctrl._subagents.set_status(handle, "completed")
    row = store.get_agent("agent-1")
    assert row is not None
    assert (row.status, row.report, row.files_changed, row.stale_refusals) == (
        "completed", "done", ["a.py"], 1)
    assert row.ended_at is not None
    seen = [events.get_nowait() for _ in range(events.qsize())]
    assert [e["type"] for e in seen] == ["agent_status", "agent_finished"]
    assert seen[1]["payload"] == {"agent_id": "agent-1", "status": "completed",
                                  "files_changed": ["a.py"]}
