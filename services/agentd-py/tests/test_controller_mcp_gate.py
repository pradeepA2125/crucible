"""mcp_tool gate: MCP tool calls in a controller turn pause for live approval —
mirror of the command gate on the same thread-gate machinery (spec §3.4)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import McpToolDecision
from agentd.mcp.rules import McpRuleStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _controller(tmp_path, store, broadcaster=None, mcp_manager=None):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=broadcaster or EventBroadcaster(), retrieval_client=None,
        mcp_manager=mcp_manager)


@pytest.mark.asyncio
async def test_gate_raised_then_approve_resolves(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._mcp_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "gh", "create_issue", {"title": "x"}))
    await asyncio.sleep(0)
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "mcp_tool"
    assert gate.payload["server"] == "gh" and gate.payload["tool"] == "create_issue"
    assert gate.payload["args"] == {"title": "x"}

    assert await ctrl.resolve_mcp(th.thread_id, McpToolDecision(approve=True)) is True
    assert await cb_task is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None  # cleared in place


@pytest.mark.asyncio
async def test_reject_returns_false(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._mcp_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "gh", "t", {}))
    await asyncio.sleep(0)
    await ctrl.resolve_mcp(th.thread_id, McpToolDecision(approve=False))
    assert await cb_task is False


@pytest.mark.asyncio
async def test_remember_persists_rule_and_auto_approves_next(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    cb_task = asyncio.create_task(ctrl._mcp_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "gh", "t", {}))
    await asyncio.sleep(0)
    await ctrl.resolve_mcp(th.thread_id, McpToolDecision(approve=True, remember=True))
    assert await cb_task is True
    assert McpRuleStore(str(tmp_path)).matches("gh", "t") is True
    # Second call: no gate — remembered rule auto-approves.
    assert await ctrl._mcp_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "gh", "t", {}) is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_broadcasts_mcp_approval_requested_poke(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    bc = EventBroadcaster()
    ctrl = _controller(tmp_path, store, broadcaster=bc)
    cid = f"chat:{th.thread_id}"
    q = bc.subscribe(cid)
    cb_task = asyncio.create_task(ctrl._mcp_approval_cb(th.thread_id, cid, "gh", "t", {}))
    await asyncio.sleep(0)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    poke = [e for e in events if e["type"] == "mcp_approval_requested"]
    assert poke and poke[0]["payload"]["server"] == "gh" and poke[0]["payload"]["tool"] == "t"
    await ctrl.resolve_mcp(th.thread_id, McpToolDecision(approve=False))
    await cb_task


@pytest.mark.asyncio
async def test_timeout_rejects(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_EDITOR_MCP_DECISION_TIMEOUT_SEC", "0.05")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl._mcp_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "gh", "t", {}) is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_resolve_mcp_no_pending_returns_false_and_clears_orphan(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl.resolve_mcp(th.thread_id, McpToolDecision(approve=True)) is False
    # Restart orphan: gate persisted, no in-memory waiter → cleared + breadcrumb.
    store.set_controller_gate(
        th.thread_id, PendingGate(kind="mcp_tool", payload={"server": "s", "tool": "t"}))
    assert await ctrl.resolve_mcp(th.thread_id, McpToolDecision(approve=True)) is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_registry_includes_mcp_source_when_manager_present(tmp_path: Path):
    from agentd.tools.registry import ToolDefinition

    class _StubManager:
        def tool_definitions(self):
            return [ToolDefinition(name="mcp__gh__t", description="d",
                                   parameters={"type": "object", "properties": {}})]

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store, mcp_manager=_StubManager())

    async def _cb(server, tool, args):
        return True

    registry = ctrl._build_registry(mcp_approval_cb=_cb)
    assert "mcp__gh__t" in [d.name for d in registry.definitions()]
    # No manager → no MCP tools.
    ctrl_off = _controller(tmp_path, store)
    registry_off = ctrl_off._build_registry(mcp_approval_cb=_cb)
    assert not any(d.name.startswith("mcp__") for d in registry_off.definitions())
