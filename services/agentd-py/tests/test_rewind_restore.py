"""Rewind restore: folding a span of checkpoints back onto the workspace."""
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore


def _fixture(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "a.py").write_text("v0\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), "t")
    return ws, store, thread, RewindStore(store, ws)


def _turn(store, rewind, thread_id, text, turn_id):
    msg_id = store.append_message(thread_id, ChatMessage(role="user", content=text))
    rewind.open_checkpoint(thread_id, msg_id, turn_id,
                           thread=store.get_thread(thread_id), memory_anchor_md=None)
    return msg_id


def test_restore_folds_multiple_turns_first_seen_wins(tmp_path):
    """Rewinding past two turns must restore the OLDEST pre-state, not the most recent."""
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id

    first = _turn(store, rewind, tid, "turn one", "t1")
    rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v1\n")

    _turn(store, rewind, tid, "turn two", "t2")
    rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v2\n")

    outcome = rewind.restore(tid, first)

    assert (ws / "a.py").read_text() == "v0\n"
    assert outcome.restored_files == ["a.py"]


def test_restore_deletes_files_the_span_created(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    first = _turn(store, rewind, tid, "make it", "t1")
    rewind.capture(tid, ["new.py"])
    (ws / "new.py").write_text("created\n")

    outcome = rewind.restore(tid, first)

    assert not (ws / "new.py").exists()
    assert outcome.deleted_files == ["new.py"]


def test_restore_truncates_transcript_and_returns_prefill(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    store.append_message(tid, ChatMessage(role="user", content="keep me"))
    store.append_message(tid, ChatMessage(role="agent", content="kept reply"))
    anchor = _turn(store, rewind, tid, "rewind me", "t1")
    store.append_message(tid, ChatMessage(role="agent", content="dropped reply"))

    outcome = rewind.restore(tid, anchor)

    remaining = [m.content for m in store.get_thread(tid).messages]
    assert remaining == ["keep me", "kept reply"]
    assert outcome.removed_messages == 2
    assert outcome.prefill_text == "rewind me"


def test_restore_restores_controller_blobs_from_the_target(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    store.set_controller_history(tid, [{"role": "user", "content": "old"}])
    anchor = _turn(store, rewind, tid, "go", "t1")
    store.set_controller_history(tid, [{"role": "user", "content": "new"}])
    store.set_controller_todos(tid, '[{"title": "later"}]')

    rewind.restore(tid, anchor)

    reloaded = store.get_thread(tid)
    assert reloaded.controller_conversation_history == [{"role": "user", "content": "old"}]
    assert reloaded.controller_todos is None


def test_restore_drops_checkpoints_from_the_target_forward(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    keep = _turn(store, rewind, tid, "one", "t1")
    anchor = _turn(store, rewind, tid, "two", "t2")

    rewind.restore(tid, anchor)

    assert [c.anchor_message_id for c in store.list_checkpoints(tid)] == [keep]


def test_restore_reports_per_file_failure_without_aborting(tmp_path, monkeypatch):
    """A rewind that half-worked must SAY so — never stop silently partway."""
    import shutil as _shutil

    ws, store, thread, rewind = _fixture(tmp_path)
    (ws / "b.py").write_text("b0\n")
    tid = thread.thread_id
    anchor = _turn(store, rewind, tid, "go", "t1")
    rewind.capture(tid, ["a.py", "b.py"])
    (ws / "a.py").write_text("a1\n")
    (ws / "b.py").write_text("b1\n")

    real_copy = _shutil.copy2

    def _explode(src, dst, **kw):
        if str(dst).endswith("a.py"):
            raise OSError("disk on fire")
        return real_copy(src, dst, **kw)

    monkeypatch.setattr("agentd.chat.rewind.shutil.copy2", _explode)
    outcome = rewind.restore(tid, anchor)

    assert outcome.failed and outcome.failed[0]["path"] == "a.py"
    assert (ws / "b.py").read_text() == "b0\n"  # the other file still restored


def test_restore_unknown_anchor_returns_none(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    assert rewind.restore(thread.thread_id, "nope") is None


def test_preview_counts_distinct_files_across_the_span(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    first = _turn(store, rewind, tid, "one", "t1")
    rewind.capture(tid, ["a.py"])
    _turn(store, rewind, tid, "two", "t2")
    rewind.capture(tid, ["a.py", "c.py"])  # a.py already counted once

    preview = rewind.preview(tid, first)

    assert preview.files == 2
    assert preview.messages == 2
