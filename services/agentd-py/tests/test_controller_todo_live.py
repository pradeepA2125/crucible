from agentd.chat.live_state import resolve_thread_live
from agentd.chat.models import ChatThread, PendingGate


def _get_task_raises(_tid):
    raise KeyError("no task")


def test_todos_surface_with_no_task_no_gate():
    thread = ChatThread(
        thread_id="t1", workspace_path="/w",
        controller_todos=[{"title": "A", "status": "pending", "note": ""}])
    live = resolve_thread_live(thread, active_task_id=None, get_task=_get_task_raises)
    assert live.todos == [{"title": "A", "status": "pending", "note": ""}]


def test_todos_surface_alongside_controller_gate():
    thread = ChatThread(
        thread_id="t1", workspace_path="/w",
        pending_controller_gate=PendingGate(kind="mode", payload={"x": 1}),
        controller_todos=[{"title": "A", "status": "in_progress", "note": ""}])
    live = resolve_thread_live(thread, active_task_id=None, get_task=_get_task_raises)
    assert live.pending_gate is not None and live.pending_gate.kind == "mode"
    assert live.todos == [{"title": "A", "status": "in_progress", "note": ""}]


def test_no_todos_is_none():
    thread = ChatThread(thread_id="t1", workspace_path="/w")
    live = resolve_thread_live(thread, active_task_id=None, get_task=_get_task_raises)
    assert live.todos is None
