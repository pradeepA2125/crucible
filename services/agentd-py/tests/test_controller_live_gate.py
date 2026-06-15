from pathlib import Path

from agentd.chat.live_state import resolve_thread_live
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore


def _raise_keyerror(_id):
    raise KeyError(_id)


def test_controller_gate_overlays_live(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    store.set_controller_gate(th.thread_id, PendingGate(kind="mode", payload={"plan_sketch": "x"}))

    th2 = store.get_thread(th.thread_id)
    assert th2 is not None
    live = resolve_thread_live(th2, active_task_id=None, get_task=_raise_keyerror)
    assert live.pending_gate is not None and live.pending_gate.kind == "mode"
    assert live.pending_gate.payload["plan_sketch"] == "x"

    # Clearing the gate removes it (durable round-trip through sqlite).
    store.set_controller_gate(th.thread_id, None)
    reloaded = store.get_thread(th.thread_id)
    assert reloaded is not None and reloaded.pending_controller_gate is None


def test_thread_live_falls_back_to_task_gate_when_no_controller_gate(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    # No controller gate set → wrapper delegates to resolve_live_state (no task → empty).
    live = resolve_thread_live(th, active_task_id=None, get_task=_raise_keyerror)
    assert live.pending_gate is None
