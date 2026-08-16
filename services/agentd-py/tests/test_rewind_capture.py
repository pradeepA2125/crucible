"""Rewind capture: copy-on-first-write snapshots of pre-edit file state."""
import json

from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore


def _fixture(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("original a\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), "t")
    return ws, store, thread


def test_capture_copies_pre_edit_content_once(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    rewind.capture(thread.thread_id, ["src/a.py"])
    (ws / "src" / "a.py").write_text("edited once\n")
    # A second capture of the same path must NOT overwrite the original snapshot.
    rewind.capture(thread.thread_id, ["src/a.py"])

    snap = ws / ".crucible" / "state" / "rewind" / thread.thread_id / "0" / "files" / "src" / "a.py"
    assert snap.read_text() == "original a\n"


def test_capture_records_created_file_as_absent(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    rewind.capture(thread.thread_id, ["src/new.py"])

    cp = store.get_checkpoint_by_anchor(thread.thread_id, msg_id)
    entry = next(f for f in cp.files if f.path == "src/new.py")
    assert entry.existed is False


def test_capture_skips_oversize_file(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    (ws / "big.bin").write_text("x" * 100)
    rewind = RewindStore(store, ws, max_file_bytes=10)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    rewind.capture(thread.thread_id, ["big.bin"])

    cp = store.get_checkpoint_by_anchor(thread.thread_id, msg_id)
    entry = next(f for f in cp.files if f.path == "big.bin")
    assert entry.oversize is True
    assert not (ws / ".crucible" / "state" / "rewind" / thread.thread_id / "0"
                / "files" / "big.bin").exists()


def test_capture_without_open_checkpoint_is_silent_noop(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    rewind.capture(thread.thread_id, ["src/a.py"])  # must not raise
    assert store.list_checkpoints(thread.thread_id) == []


def test_open_checkpoint_snapshots_controller_blobs(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    store.set_controller_history(thread.thread_id, [{"role": "user", "content": "earlier"}])
    store.set_controller_todos(thread.thread_id, '[{"title": "t"}]')
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))

    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id),
                           memory_anchor_md="anchor v1")

    cp = store.get_checkpoint_by_anchor(thread.thread_id, msg_id)
    assert json.loads(cp.controller_history_json) == [{"role": "user", "content": "earlier"}]
    assert json.loads(cp.controller_todo_json) == [{"title": "t"}]
    assert cp.memory_anchor_md == "anchor v1"


def test_second_checkpoint_gets_next_seq(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    t = store.get_thread(thread.thread_id)
    first = rewind.open_checkpoint(thread.thread_id, "m1", "turn1", thread=t,
                                   memory_anchor_md=None)
    second = rewind.open_checkpoint(thread.thread_id, "m2", "turn2", thread=t,
                                    memory_anchor_md=None)
    assert (first, second) == (0, 1)
