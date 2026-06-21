"""Phase 2 of the Finding 6 fix: run_command in a chat EDIT turn is gated.

Mirrors the engine's task-path command gate (_build_command_approval_callback) on the
controller's thread-gate machinery (the same pattern as the per-edit gate): honors
AI_EDITOR_SHELL_POLICY (ALLOW_ALL skips; a remembered workspace rule auto-approves),
otherwise raises a durable kind="command" gate and awaits /command-decision. Reuses
CommandDecision / CommandRuleStore / rule_from_decision — no parallel rule logic.
"""
import asyncio
from functools import partial
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import CommandDecision, CommandRule, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.command_rules import CommandRuleStore


def _controller(tmp_path, store, policy=ShellPolicy.ASK):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=policy)


@pytest.mark.asyncio
async def test_allow_all_skips_gate(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store, ShellPolicy.ALLOW_ALL)
    decision = await ctrl._command_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "pytest", ["-q"], "")
    assert decision.approve is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_ask_raises_command_gate_then_resolve_approves(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store, ShellPolicy.ASK)
    cb_task = asyncio.create_task(ctrl._command_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "rm", ["-rf", "x"], "sub"))
    await asyncio.sleep(0)  # let the cb set the gate and start awaiting
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "command"
    assert gate.payload["command"] == "rm" and gate.payload["args"] == ["-rf", "x"]

    assert await ctrl.resolve_command(
        th.thread_id, CommandDecision(approve=True)) is True
    decision = await cb_task
    assert decision.approve is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None  # cleared in place


@pytest.mark.asyncio
async def test_ask_broadcasts_command_approval_requested_for_instant_render(tmp_path: Path):
    # Consistency with the task path: broadcast command_approval_requested so the FE
    # pokes /live and the card renders instantly (it still renders FROM /live — durable).
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    bc = EventBroadcaster()
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=bc, retrieval_client=None, shell_policy=ShellPolicy.ASK)
    cid = f"chat:{th.thread_id}"
    q = bc.subscribe(cid)
    cb_task = asyncio.create_task(
        ctrl._command_approval_cb(th.thread_id, cid, "rm", ["-rf"], ""))
    await asyncio.sleep(0)  # let the cb set the gate + broadcast
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    poke = [e for e in events if e["type"] == "command_approval_requested"]
    assert poke, [e["type"] for e in events]
    assert poke[0]["payload"]["command"] == "rm" and poke[0]["payload"]["args"] == ["-rf"]

    await ctrl.resolve_command(th.thread_id, CommandDecision(approve=False))
    await cb_task


@pytest.mark.asyncio
async def test_resolve_command_returns_false_when_no_pending(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    assert await ctrl.resolve_command(
        th.thread_id, CommandDecision(approve=True)) is False


@pytest.mark.asyncio
async def test_resolve_command_restart_orphan_clears_stale_gate(tmp_path: Path):
    # S8 analog: gate persisted in sqlite, no in-memory waiter (post-restart).
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store)
    store.set_controller_gate(
        th.thread_id, PendingGate(kind="command", payload={"command": "ls"}))
    assert await ctrl.resolve_command(
        th.thread_id, CommandDecision(approve=True)) is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None  # stale gate cleared


@pytest.mark.asyncio
async def test_remembered_workspace_rule_auto_approves(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store, ShellPolicy.ASK)
    CommandRuleStore(str(tmp_path)).add(
        CommandRule(type="binary", value="pytest", added_at="x"))
    decision = await ctrl._command_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "pytest", ["-q"], "")
    assert decision.approve is True
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_approve_remember_persists_workspace_rule(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store, ShellPolicy.ASK)
    cb_task = asyncio.create_task(ctrl._command_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "pytest", ["-q"], ""))
    await asyncio.sleep(0)
    await ctrl.resolve_command(
        th.thread_id, CommandDecision(approve=True, remember=True, scope="binary"))
    await cb_task
    assert CommandRuleStore(str(tmp_path)).matches("pytest", ["-q"]) is True


@pytest.mark.asyncio
async def test_command_decision_timeout_rejects(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK, command_decision_timeout_sec=0.05)
    decision = await ctrl._command_approval_cb(
        th.thread_id, f"chat:{th.thread_id}", "rm", ["-rf"], "")
    assert decision.approve is False
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_run_command_in_edit_loop_raises_command_gate(tmp_path: Path):
    # End-to-end: a ControllerLoop in EDIT with the controller's real registry+cb wired.
    # run_command must raise the command gate; on approve it executes and the loop finishes.
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = _controller(tmp_path, store, ShellPolicy.ASK)
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    cid = f"chat:{th.thread_id}"
    reg = ctrl._build_registry(partial(ctrl._command_approval_cb, th.thread_id, cid))
    steps = [
        {"type": "tool_call", "thought": "run", "tool": "run_command",
         "args": {"command": "touch", "args": ["s.txt"]}},
        {"type": "submit_changes", "thought": "done", "summary": "ran"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, ctrl._broadcaster, channel_id=cid, phase_sm=sm)
    run = asyncio.create_task(
        loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6))
    gate = None
    for _ in range(100):
        await asyncio.sleep(0.01)
        gate = store.get_thread(th.thread_id).pending_controller_gate
        if gate is not None and gate.kind == "command":
            break
    assert gate is not None and gate.kind == "command"
    assert not (tmp_path / "s.txt").exists()  # not run yet — held at the gate

    await ctrl.resolve_command(th.thread_id, CommandDecision(approve=True))
    out = await run
    assert out.kind == "submit_changes"
    assert (tmp_path / "s.txt").exists()  # ran after approval
