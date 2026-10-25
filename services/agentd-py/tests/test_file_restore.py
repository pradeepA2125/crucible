import hashlib
import json
import os

import pytest

from agentd.chat.file_restore import FileRestore, RestoreConflict
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore


def fixture(tmp_path, before=b"original\n", after=b"changed\n"):
    root = tmp_path / "workspace"
    root.mkdir()
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(root), "Test")
    message = store.append_message(thread.thread_id, ChatMessage(role="user", content="Test"))
    rewind = RewindStore(store, root)
    rewind.open_checkpoint(
        thread.thread_id,
        message,
        "turn",
        thread=store.get_thread(thread.thread_id),
        memory_anchor_md=None,
    )
    path = root / "sample.txt"
    if before is not None:
        path.write_bytes(before)
    rewind.capture(thread.thread_id, ["sample.txt"])
    if after is not None:
        path.write_bytes(after)
    elif path.exists():
        path.unlink()
    point = rewind.checkpoints(thread.thread_id)[0]
    identity = {
        "seq": point.seq,
        "anchorMessageId": point.anchor_message_id,
        "createdAt": point.created_at.isoformat(),
    }
    now = [0]
    process = ["process"]
    busy = [False]

    def idle():
        if busy[0]:
            raise RestoreConflict("Project busy")

    service = FileRestore(store, rewind, root, idle, lambda: process[0], lambda: now[0])
    status = "A" if before is None else "D" if after is None else "M"
    header = json.dumps(
        [
            thread.thread_id,
            "sample.txt",
            identity,
            identity,
            status,
            len(before or b""),
            len(after or b""),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    fingerprint = hashlib.sha256(header + (before or b"") + (after or b"")).hexdigest()

    def preview():
        return service.prepare(thread.thread_id, identity, identity, "sample.txt", fingerprint)

    return service, thread.thread_id, path, preview, now, process, busy, identity


@pytest.mark.parametrize(
    "before,after", [(b"old", b"new"), (None, b"created"), (b"deleted", None), (b"", b"nonempty")]
)
def test_restore_then_undo_survives_service_restart(tmp_path, before, after):
    s, t, path, preview, *_ = fixture(tmp_path, before, after)
    count = len(s.store.get_thread(t).messages)
    p = preview()
    r = s.apply(t, p["token"])
    assert (path.read_bytes() if path.exists() else None) == before
    fresh_store = ChatThreadStore(s.store._db_path)
    fresh = FileRestore(fresh_store, RewindStore(fresh_store, s.root), s.root, s.idle, s.process)
    assert fresh.history(t)["entries"][0]["id"] == r["receipt"]
    u = fresh.prepare_undo(t, r["receipt"])
    fresh.apply(t, u["token"])
    assert (path.read_bytes() if path.exists() else None) == after
    assert len(s.store.get_thread(t).messages) == count


def test_changed_display_and_changed_preview_refuse(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    p = preview()
    path.write_text("external edit")
    with pytest.raises(RestoreConflict, match="changed"):
        s.apply(t, p["token"])
    with pytest.raises(RestoreConflict, match="comparison"):
        preview()
    assert path.read_text() == "external edit"
    assert s.history(t)["entries"] == []


@pytest.mark.parametrize("condition", ["busy", "expired", "process"])
def test_commit_rechecks_admission_and_identity(tmp_path, condition):
    s, t, path, preview, now, process, busy, _ = fixture(tmp_path)
    p = preview()
    if condition == "busy":
        busy[0] = True
    elif condition == "expired":
        now[0] = 61
    else:
        process[0] = "other"
    with pytest.raises(RestoreConflict):
        s.apply(t, p["token"])
    assert path.read_bytes() == b"changed\n"


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        ".git/config",
        ".Crucible/secret",
        "a/.agentd/token",
        "a\\b",
        "a//b",
    ],
)
def test_protected_paths_refuse(tmp_path, path):
    s, t, _, _, _, _, _, cp = fixture(tmp_path)
    with pytest.raises(RestoreConflict):
        s.prepare(t, cp, cp, path, "0" * 64)


def test_binary_current_and_outside_parent_refuse(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    path.write_bytes(b"a\0b")
    with pytest.raises(RestoreConflict):
        preview()
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(RestoreConflict):
        preview()
    assert not (tmp_path / "outside").exists()


def test_checkpoint_changed_between_preview_and_commit(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    p = preview()
    s.store._conn.execute("DELETE FROM chat_checkpoints")
    s.store._conn.commit()
    with pytest.raises(RestoreConflict, match="checkpoint"):
        s.apply(t, p["token"])
    assert path.read_bytes() == b"changed\n"


def test_undo_refuses_newer_work(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    r = s.apply(t, preview()["token"])
    path.write_text("newer work")
    with pytest.raises(RestoreConflict, match="newer"):
        s.prepare_undo(t, r["receipt"])
    assert path.read_text() == "newer work"


def test_journal_failure_prevents_write(tmp_path, monkeypatch):
    s, t, path, preview, *_ = fixture(tmp_path)
    s.db.execute("PRAGMA query_only=ON")
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        s.apply(t, preview()["token"])
    assert path.read_bytes() == b"changed\n"


def test_post_replace_failure_keeps_recovery_available(tmp_path, monkeypatch):
    s, t, path, preview, *_ = fixture(tmp_path)
    real = os.fsync

    def fail_directory(fd):
        import stat

        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("disk failure")
        real(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(RestoreConflict, match="confirmed"):
        s.apply(t, preview()["token"])
    assert path.read_bytes() == b"original\n"
    row = s.history(t)["entries"][0]
    assert row["state"] == "prepared"
    monkeypatch.setattr(os, "fsync", real)
    s.apply(t, s.prepare_undo(t, row["id"])["token"])
    assert path.read_bytes() == b"changed\n"


def test_file_mode_preserved(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    path.chmod(0o755)
    s.apply(t, preview()["token"])
    assert path.stat().st_mode & 0o777 == 0o755


def test_undo_reuses_durable_record_and_cannot_be_replayed(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    result = s.apply(t, preview()["token"])
    undo = s.prepare_undo(t, result["receipt"])
    s.apply(t, undo["token"])
    rows = s.history(t)["entries"]
    assert len(rows) == 1 and rows[0]["state"] == "undone"
    with pytest.raises(RestoreConflict, match="undone"):
        s.prepare_undo(t, result["receipt"])
    assert path.read_bytes() == b"changed\n"


def test_full_recovery_storage_does_not_disable_existing_undo(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    result = s.apply(t, preview()["token"])
    import uuid

    for _ in range(199):
        s.db.execute(
            "INSERT INTO file_recoveries SELECT ?,thread,path,before,target,"
            "before_mode,target_mode,action,state,created FROM file_recoveries WHERE id=?",
            (str(uuid.uuid4()), result["receipt"]),
        )
    s.db.commit()
    undo = s.prepare_undo(t, result["receipt"])
    s.apply(t, undo["token"])
    assert path.read_bytes() == b"changed\n"


def test_same_bytes_in_another_path_cannot_reuse_displayed_fingerprint(tmp_path):
    s, t, path, preview, *_, identity = fixture(tmp_path)
    other = s.root / "other.txt"
    other.write_bytes(b"original\n")
    s.rewind.capture(t, ["other.txt"])
    other.write_bytes(b"changed\n")
    header = json.dumps(
        [t, "sample.txt", identity, identity, "M", 9, 8], ensure_ascii=False, separators=(",", ":")
    ).encode()
    fingerprint = hashlib.sha256(header + b"original\n" + b"changed\n").hexdigest()
    assert preview()["path"] == "sample.txt"
    with pytest.raises(RestoreConflict, match="comparison"):
        s.prepare(t, identity, identity, "other.txt", fingerprint)


def test_second_undo_preview_cannot_replay_a_consumed_record(tmp_path):
    s, t, path, preview, *_ = fixture(tmp_path)
    receipt = s.apply(t, preview()["token"])["receipt"]
    first = s.prepare_undo(t, receipt)
    second = s.prepare_undo(t, receipt)
    s.apply(t, first["token"])
    path.write_bytes(b"original\n")
    with pytest.raises(RestoreConflict, match="Recovery data changed"):
        s.apply(t, second["token"])
    assert path.read_bytes() == b"original\n"
