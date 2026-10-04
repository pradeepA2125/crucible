"""One review control for the whole process (spec §5.4)."""
import asyncio
from pathlib import Path

import pytest

from agentd.chat.models import GateAgent, PendingGate
from tests.test_background_dispatch import _setup


@pytest.mark.asyncio
async def test_flip_to_auto_accept_resolves_gates_in_every_thread(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    other = store.create_thread(str(tmp_path), title="o").thread_id
    loop = asyncio.get_running_loop()
    futures = {}
    for thread_id, gate in (
            (tid, PendingGate.new("edit", {"diff_entries": []})),
            (other, PendingGate.new("edit", {"diff_entries": []},
                                    agent=GateAgent(id="a1", label="bg", name="explore"))),
            (other, PendingGate.new("edit", {"diff_entries": [], "protected": True}))):
        store.add_controller_gate(thread_id, gate)
        futures[gate.gate_id] = loop.create_future()
        ctrl._pending_edit[gate.gate_id] = futures[gate.gate_id]
        ctrl._pending_edit_gates[gate.gate_id] = (thread_id, gate)
    ctrl._review_control.auto_accept_edits = False
    result = ctrl.set_review_pref(auto_accept=True)
    assert (result.auto_resolved, result.background) == (2, 1)
    assert ctrl._review_control.auto_accept_edits is True
    assert sum(f.done() for f in futures.values()) == 2


@pytest.mark.asyncio
async def test_a_message_sets_the_value_without_resolving(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch,
                              [{"type": "answer", "thought": "t", "answer": "ok"}], {})
    fut = asyncio.get_running_loop().create_future()
    gate = PendingGate.new("edit", {"diff_entries": []})
    ctrl._pending_edit[gate.gate_id] = fut
    ctrl._pending_edit_gates[gate.gate_id] = ("elsewhere", gate)
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}", step_review=False)
    assert ctrl._review_control.auto_accept_edits is True and not fut.done()
    await ctrl.handle_message(tid, "hi", channel_id=f"chat:{tid}", step_review=True)
    assert ctrl._review_control.auto_accept_edits is False


def test_plan_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _store, _tid = _setup(tmp_path, monkeypatch, [], {})
    ctrl.set_plan_mode(True)
    assert ctrl._plan_mode is True
