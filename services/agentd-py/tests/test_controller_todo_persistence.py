from pathlib import Path

from agentd.chat.storage import ChatThreadStore
from agentd.chat.todo_ledger import TodoItem, TodoLedger


def test_set_get_roundtrip(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    led = TodoLedger()
    led.replace([TodoItem("A", "done"), TodoItem("B", "pending")])
    store.set_controller_todos(thread.thread_id, led.to_json())
    back = TodoLedger.from_json(store.get_controller_todos(thread.thread_id))
    assert [(i.title, i.status) for i in back.items] == [("A", "done"), ("B", "pending")]


def test_get_default_none(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    assert store.get_controller_todos(thread.thread_id) is None


def test_clear_with_none(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_todos(thread.thread_id, "[]")
    store.set_controller_todos(thread.thread_id, None)
    assert store.get_controller_todos(thread.thread_id) is None


def test_chatthread_carries_controller_todos(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_todos(thread.thread_id, '[{"title": "A", "status": "pending", "note": ""}]')
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    assert reloaded.controller_todos == [{"title": "A", "status": "pending", "note": ""}]
