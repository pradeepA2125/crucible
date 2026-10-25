"""Explicit per-file recovery, independent of conversation Rewind.

All public calls are synchronous. Routes call them without awaiting: the idle
check, durable journal and file mutation form one event-loop admission barrier.
External programs can still write; hashes and descriptor checks narrow, but do
not pretend to eliminate, that external-writer race.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

LIMIT = 1024 * 1024
PROTECTED = {".git", ".crucible", ".agentd", ".ai-editor"}


class RestoreConflict(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def text_bytes(data):
    if data is not None:
        if len(data) > LIMIT or b"\0" in data:
            raise RestoreConflict("Only text files up to 1 MiB can be restored here.")
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeError:
            raise RestoreConflict("Only UTF-8 text files can be restored here.") from None
    return data


def parts_for(path):
    parts = path.split("/")
    if (
        not path
        or len(path) > 4096
        or len(parts) > 128
        or ":" in path
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 or c == "\ufffd" for c in path)
        or any(not p or p in {".", ".."} or p.lower() in PROTECTED for p in parts)
    ):
        raise RestoreConflict("This path is protected or is not a safe project file.")
    return parts


@contextmanager
def parent_fd(root, path):
    parts = parts_for(path)
    if not getattr(os, "O_NOFOLLOW", 0) or os.open not in os.supports_dir_fd:
        raise RestoreConflict("Safe file restore is unavailable on this platform.")
    directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    except OSError:
        raise RestoreConflict(
            "The file or its parent is linked, missing or unavailable. Refresh."
        ) from None
    finally:
        os.close(fd)


def read_at(fd, name):
    try:
        file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None, None
    try:
        info = os.fstat(file)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > LIMIT
            or info.st_mode & 0o7000
        ):
            raise RestoreConflict("Only ordinary, unlinked text files up to 1 MiB can be restored.")
        data = bytearray()
        while len(data) <= LIMIT:
            chunk = os.read(file, min(65536, LIMIT + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(file)
        if (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise RestoreConflict("This file changed while it was read. Refresh.")
        return text_bytes(bytes(data)), stat.S_IMODE(info.st_mode)
    finally:
        os.close(file)


class FileRestore:
    def __init__(self, store, rewind, root, idle, process, clock=time.monotonic):
        self.store, self.rewind, self.root = store, rewind, Path(root).resolve()
        self.idle, self.process, self.clock = idle, process, clock
        self.previews = {}
        self.db = store._conn
        self.db.execute("""CREATE TABLE IF NOT EXISTS file_recoveries (
            id TEXT PRIMARY KEY, thread TEXT NOT NULL, path TEXT NOT NULL,
            before BLOB, target BLOB, before_mode INTEGER, target_mode INTEGER,
            action TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL)""")
        self.db.commit()

    def _check(self, thread):
        self.idle()
        found = self.db.execute(
            "SELECT workspace_path FROM chat_threads WHERE thread_id=?", (thread,)
        ).fetchone()
        if (
            found is None
            or not found["workspace_path"]
            or Path(found["workspace_path"]).resolve() != self.root
        ):
            raise RestoreConflict("Open a conversation belonging to this project.")

    def _baseline(self, thread, checkpoint, path, supplying):
        points = self.rewind.checkpoints(thread)

        def identity(c):
            return {
                "seq": c.seq,
                "anchorMessageId": c.anchor_message_id,
                "createdAt": c.created_at.isoformat(),
            }

        if not any(identity(c) == checkpoint for c in points) or not any(
            identity(c) == supplying for c in points
        ):
            raise RestoreConflict("This retained checkpoint changed or was removed. Refresh.")
        found = self.rewind.baseline_file(thread, checkpoint["seq"], path, max_bytes=LIMIT)
        if (
            found is None
            or found.from_seq != supplying["seq"]
            or found.state not in {"captured", "absent"}
        ):
            raise RestoreConflict("No safe retained content is available for this file.")
        return text_bytes(found.content if found.state == "captured" else None)

    def _remember(self, thread, path, current, mode, target, target_mode, action, baseline=None):
        self.previews = {k: v for k, v in self.previews.items() if self.clock() < v["expires"]}
        if len(self.previews) >= 32:
            raise RestoreConflict("Too many open restore previews. Wait a minute, then retry.")
        token = str(uuid.uuid4())
        self.previews[token] = dict(
            thread=thread,
            path=path,
            current=current,
            mode=mode,
            target=target,
            target_mode=target_mode,
            action=action,
            baseline=baseline,
            process=self.process(),
            expires=self.clock() + 60,
        )
        return {
            "kind": "preview",
            "token": token,
            "path": path,
            "action": action,
            "effect": "delete" if target is None else "create" if current is None else "replace",
            "currentBytes": len(current) if current is not None else 0,
            "targetBytes": len(target) if target is not None else 0,
        }

    def prepare(self, thread, checkpoint, supplying, path, fingerprint):
        self._check(thread)
        parts_for(path)
        target = self._baseline(thread, checkpoint, path, supplying)
        with parent_fd(self.root, path) as (fd, name):
            current, mode = read_at(fd, name)
        status = "A" if target is None else "D" if current is None else "M"
        header = json.dumps(
            [thread, path, checkpoint, supplying, status, len(target or b""), len(current or b"")],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        actual = hashlib.sha256(header + (target or b"") + (current or b"")).hexdigest()
        if actual != fingerprint or target == current:
            raise RestoreConflict(
                "The displayed comparison no longer matches this file. Refresh its diff."
            )
        return self._remember(
            thread,
            path,
            current,
            mode,
            target,
            mode if mode is not None else 0o644,
            "restore",
            (checkpoint, supplying),
        )

    def history(self, thread):
        found = self.db.execute(
            "SELECT workspace_path FROM chat_threads WHERE thread_id=?", (thread,)
        ).fetchone()
        if (
            found is None
            or not found["workspace_path"]
            or Path(found["workspace_path"]).resolve() != self.root
        ):
            raise RestoreConflict("Conversation is not in this project.")
        rows = self.db.execute(
            "SELECT id,path,action,state,created FROM file_recoveries "
            "WHERE thread=? ORDER BY created DESC LIMIT 200",
            (thread,),
        ).fetchall()
        return {"kind": "history", "entries": [dict(r) for r in rows]}

    def prepare_undo(self, thread, receipt):
        self._check(thread)
        row = self.db.execute(
            "SELECT * FROM file_recoveries WHERE id=? AND thread=?", (receipt, thread)
        ).fetchone()
        if row is None or row["state"] == "undone":
            raise RestoreConflict("This recovery is unavailable or has already been undone.")
        with parent_fd(self.root, row["path"]) as (fd, name):
            current, mode = read_at(fd, name)
        if current != row["target"] or mode != row["target_mode"] and current is not None:
            raise RestoreConflict(
                "This file changed after the restore. Undo will not overwrite newer work."
            )
        if current == row["before"]:
            raise RestoreConflict("This recovery is already present; no file change is needed.")
        result = self._remember(
            thread, row["path"], current, mode, row["before"], row["before_mode"], "undo"
        )
        self.previews[result["token"]]["undo_receipt"] = receipt
        return result

    def apply(self, thread, token):
        self._check(thread)
        preview = self.previews.pop(token, None)
        if (
            not preview
            or preview["thread"] != thread
            or preview["process"] != self.process()
            or self.clock() >= preview["expires"]
        ):
            raise RestoreConflict(
                "This restore preview expired or its project session changed. Preview again."
            )
        path, current, target = preview["path"], preview["current"], preview["target"]
        if preview["baseline"]:
            checkpoint, supplying = preview["baseline"]
            if self._baseline(thread, checkpoint, path, supplying) != target:
                raise RestoreConflict("Retained content changed. Refresh the comparison.")
        with parent_fd(self.root, path) as (fd, name):
            if read_at(fd, name) != (current, preview["mode"]):
                raise RestoreConflict(
                    "This file changed after the preview. Refresh before restoring."
                )
            receipt = preview.get("undo_receipt")
            if receipt:
                # The original journal already contains BOTH byte states. Reuse it:
                # storage pressure must never disable Undo of a completed restore.
                row = self.db.execute(
                    "SELECT * FROM file_recoveries WHERE id=? AND thread=?", (receipt, thread)
                ).fetchone()
                if (
                    row is None
                    or row["state"] == "undone"
                    or row["path"] != path
                    or row["target"] != current
                    or row["before"] != target
                ):
                    raise RestoreConflict("Recovery data changed. Preview again.")
                self.db.execute(
                    "UPDATE file_recoveries SET state='undo_prepared' WHERE id=?", (receipt,)
                )
            else:
                # Quota refuses new work rather than silently erasing an existing recovery.
                quota = self.db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(length(before)),0)+"
                    "COALESCE(SUM(length(target)),0) FROM file_recoveries"
                ).fetchone()
                if (
                    quota[0] >= 200
                    or quota[1] + len(current or b"") + len(target or b"") > 64 * LIMIT
                ):
                    raise RestoreConflict(
                        "Recovery storage is full. Existing recovery copies have been kept; "
                        "new restores are unavailable. Undo remains available."
                    )
                receipt = str(uuid.uuid4())
                from datetime import UTC, datetime

                created = datetime.now(UTC).isoformat()
                self.db.execute(
                    "INSERT INTO file_recoveries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        receipt,
                        thread,
                        path,
                        current,
                        target,
                        preview["mode"],
                        preview["target_mode"],
                        preview["action"],
                        "prepared",
                        created,
                    ),
                )
            self.db.commit()  # Recovery is durable BEFORE the file changes.
            temporary = ".crucible-restore-" + secrets.token_hex(16)
            try:
                if target is not None:
                    out = os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=fd,
                    )
                    try:
                        with os.fdopen(out, "wb", closefd=False) as f:
                            f.write(target)
                            f.flush()
                        os.fchmod(
                            out,
                            (
                                preview["target_mode"]
                                if preview["target_mode"] is not None
                                else 0o644
                            )
                            & 0o777,
                        )
                        os.fsync(out)
                    finally:
                        os.close(out)
                # Reject a renamed/replaced parent before touching its pinned descriptor.
                with parent_fd(self.root, path) as (named_parent, _):
                    if (os.fstat(named_parent).st_dev, os.fstat(named_parent).st_ino) != (
                        os.fstat(fd).st_dev,
                        os.fstat(fd).st_ino,
                    ):
                        raise RestoreConflict(
                            "The parent directory changed. Refresh before restoring."
                        )
                # Revalidate after persistence; no event-loop await can admit our own writers.
                if read_at(fd, name) != (current, preview["mode"]):
                    raise RestoreConflict(
                        "The file changed while recovery was saved. No restore was applied."
                    )
                if target is None:
                    os.unlink(name, dir_fd=fd)
                else:
                    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                self.db.execute(
                    "UPDATE file_recoveries SET state=? WHERE id=?",
                    ("undone" if preview["action"] == "undo" else "applied", receipt),
                )
                self.db.commit()
            except Exception:
                # Prepared state is intentionally recoverable after ambiguous OS/DB failures.
                raise RestoreConflict(
                    "Restore could not be confirmed. Check recovery history before trying again."
                ) from None
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
        return {"kind": "applied", "receipt": receipt, "path": path, "action": preview["action"]}
