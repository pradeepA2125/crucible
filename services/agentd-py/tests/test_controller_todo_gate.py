from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.todo_ledger import TodoItem, TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry
from agentd.workspace.shadow import ShadowWorkspaceManager


class _ScriptedReasoning:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    async def create_controller_step(self, **kwargs):
        resp = self._responses[self._i]
        self._i += 1
        return resp


def _edit_sm() -> ControllerPhaseSM:
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    return sm


def _wt(items):
    return {"type": "tool_call", "thought": "todos", "tool": "write_todos",
            "args": {"items": items}}


def _loop(ledger, reasoning):
    return ControllerLoop(
        reasoning, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c1", phase_sm=_edit_sm(), todo_ledger=ledger)


@pytest.mark.asyncio
async def test_submit_blocked_until_ledger_clear():
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "pending"}, {"title": "B", "status": "pending"}]),
        {"type": "submit_changes", "thought": "?", "summary": "early"},     # BLOCKED (2 pending)
        _wt([{"title": "A", "status": "done"}, {"title": "B", "status": "done"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "all done"},  # passes
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert outcome.kind == "submit_changes" and outcome.text == "all done"
    assert any("BLOCKED" in str(m.get("content", "")) for m in (outcome.history or []))


@pytest.mark.asyncio
async def test_blocked_item_does_not_deadlock_submit():
    # One done, one blocked -> nothing pending -> submit must pass (blocked != pending).
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "done"},
             {"title": "B", "status": "blocked", "note": "needs API key"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "A done, B blocked"},
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert outcome.kind == "submit_changes" and outcome.text == "A done, B blocked"


@pytest.mark.asyncio
async def test_gate_block_not_counted_as_malformed():
    ledger = TodoLedger()
    sub = {"type": "submit_changes", "thought": "?", "summary": "x"}
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "pending"}]),
        sub, sub, sub, sub,   # 4 blocked in a row would trip _MAX_MALFORMED (3) if counted
        _wt([{"title": "A", "status": "done"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "done"},
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=20)
    assert outcome.kind == "submit_changes" and outcome.text == "done"


@pytest.mark.asyncio
async def test_submit_passes_with_no_ledger():
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        {"type": "submit_changes", "thought": "ok", "summary": "nothing pending"}]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=5)
    assert outcome.kind == "submit_changes" and outcome.text == "nothing pending"


def test_active_item_prefers_in_progress_then_pending():
    """active_item() = the item the next reconcile checkpoint names: the in_progress one
    (what the model is working), falling back to the first pending; None when nothing is open."""
    led = TodoLedger(items=[
        TodoItem(title="A", status="done"),
        TodoItem(title="B", status="pending"),
        TodoItem(title="C", status="in_progress"),
    ])
    assert led.active_item().title == "C"  # in_progress wins over pending
    led2 = TodoLedger(items=[TodoItem(title="A", status="done"), TodoItem(title="B", status="pending")])
    assert led2.active_item().title == "B"  # first pending when none in_progress
    led3 = TodoLedger(items=[TodoItem(title="A", status="done")])
    assert led3.active_item() is None      # nothing open


class _RecordingPlanCtx:
    """Captures a shallow copy of plan_context at each step so a test can assert which
    keys (e.g. the reconcile marker) the loop surfaced on that turn."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0
        self.plan_contexts: list[dict] = []

    async def create_controller_step(self, *, plan_context, history, tool_definitions,
                                     phase, on_thinking=None):
        self.plan_contexts.append(dict(plan_context))
        resp = self._responses[self._i]
        self._i += 1
        return resp


@pytest.mark.asyncio
async def test_reconcile_marker_set_after_edit_and_cleared_on_write_todos(tmp_path: Path):
    """Q1: after an applied edit with an ACTIVE ledger, the loop sets a reconcile marker
    (just-edited files + the active item) so the NEXT turn's instruction can lead with a
    pointed 'is THIS item done?' checkpoint. A write_todos call clears the marker (the
    model answered) so the checkpoint doesn't keep nagging."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    ledger = TodoLedger()
    rec = _RecordingPlanCtx([
        _wt([{"title": "A", "status": "in_progress"}]),                       # 0
        {"type": "edit", "thought": "t", "patch_ops": [                       # 1 (applies)
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        _wt([{"title": "A", "status": "done", "note": "edited f.py"}]),       # 2 (SEES marker)
        {"type": "submit_changes", "thought": "d", "summary": "done"},        # 3 (marker cleared)
    ])
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c", phase_sm=sm, edit_session=sess, todo_ledger=ledger)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=10, auto_accept_edits=True)

    assert out.kind == "submit_changes"
    # The write_todos turn saw the marker naming the just-edited file + the active item.
    assert rec.plan_contexts[2].get("pending_reconcile_files") == ["f.py"]
    assert rec.plan_contexts[2].get("reconcile_item", {}).get("title") == "A"
    # Cleared after that write_todos: the submit turn no longer carries it.
    assert "pending_reconcile_files" not in rec.plan_contexts[3]
    assert "reconcile_item" not in rec.plan_contexts[3]


@pytest.mark.asyncio
async def test_empty_edit_redirects_to_write_todos_not_malformed(tmp_path: Path):
    """An 'edit' with empty patch_ops gets a redirect naming write_todos-as-tool_call (the live
    fumble fix: the model wanted the todo list but emitted type='edit' with no ops). The redirect
    is NOT counted malformed, so four in a row don't trip the cap, and the model recovers by
    calling write_todos then a real edit."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    ledger = TodoLedger()
    empty = {"type": "edit", "thought": "todos first", "patch_ops": []}
    rec = _RecordingPlanCtx([
        empty, empty, empty, empty,   # 4 empty edits — would trip _MAX_MALFORMED(3) if counted
        _wt([{"title": "A", "status": "in_progress"}]),
        {"type": "edit", "thought": "do it", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        _wt([{"title": "A", "status": "done", "note": "edited f.py"}]),
        {"type": "submit_changes", "thought": "d", "summary": "done"},
    ])
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c", phase_sm=sm, edit_session=sess, todo_ledger=ledger)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=20, auto_accept_edits=True)
    assert out.kind == "submit_changes"  # recovered, not exhausted-malformed
    assert (real / "f.py").read_text() == "x = 2\n"
    # The redirect explicitly named write_todos as a tool_call.
    assert any("write_todos" in str(m.get("content", "")) and "tool_call" in str(m.get("content", ""))
               for m in (out.history or []))


@pytest.mark.asyncio
async def test_edit_entry_flag_set_until_productive_start(tmp_path: Path):
    """edit_entry is True on the first EDIT action (no list, no edit applied) and clears once a
    todo list exists — the signal that swaps the entry hint for the mid-turn reconcile hint."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    ledger = TodoLedger()
    rec = _RecordingPlanCtx([
        _wt([{"title": "A", "status": "in_progress"}]),                       # 0: entry=True
        {"type": "edit", "thought": "e", "patch_ops": [                       # 1: entry=False (list exists)
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        _wt([{"title": "A", "status": "done", "note": "f.py"}]),
        {"type": "submit_changes", "thought": "d", "summary": "done"},
    ])
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c", phase_sm=sm, edit_session=sess, todo_ledger=ledger)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=10, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert rec.plan_contexts[0].get("edit_entry") is True    # first action, nothing started
    assert rec.plan_contexts[1].get("edit_entry") is False   # list now exists


@pytest.mark.asyncio
async def test_no_reconcile_marker_when_ledger_empty(tmp_path: Path):
    """A small/cohesive edit with NO active list must not get a phantom reconcile marker —
    the gate is `ledger.pending()`, empty here, so nothing is set."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    ledger = TodoLedger()
    rec = _RecordingPlanCtx([
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        {"type": "submit_changes", "thought": "d", "summary": "done"},
    ])
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c", phase_sm=sm, edit_session=sess, todo_ledger=ledger)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=10, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert all("pending_reconcile_files" not in pc for pc in rec.plan_contexts)
