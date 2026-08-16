import json
import os
from pathlib import Path

from agentd.runtime_lock import (
    LockInfo,
    clear_lock,
    is_pid_alive,
    read_lock,
    write_lock,
)


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    write_lock(tmp_path, port=8123)
    lock = read_lock(tmp_path)
    assert isinstance(lock, LockInfo)
    assert lock.port == 8123 and lock.pid == os.getpid() and lock.started_at > 0
    raw = json.loads((tmp_path / ".crucible/state" / "agentd.lock").read_text())
    assert set(raw) == {"pid", "port", "started_at"}


def test_read_missing_or_corrupt_returns_none(tmp_path: Path) -> None:
    assert read_lock(tmp_path) is None
    (tmp_path / ".crucible/state").mkdir(parents=True)
    (tmp_path / ".crucible/state" / "agentd.lock").write_text("{not json")
    assert read_lock(tmp_path) is None


def test_clear_lock_is_idempotent(tmp_path: Path) -> None:
    write_lock(tmp_path, port=1)
    clear_lock(tmp_path)
    clear_lock(tmp_path)
    assert read_lock(tmp_path) is None


def test_is_pid_alive() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(2**22 + 12345) is False  # exceeds default pid_max


def test_clear_lock_leaves_another_process_lock_alone(tmp_path: Path) -> None:
    """Shutdown must not delete a lock it does not own.

    Found live: three backends shared one workspace, so killing a STALE one ran its
    shutdown hook, which cleared the lockfile belonging to the LIVE backend. The
    extension then saw no lock, spawned a fresh backend on the next activation and
    orphaned the healthy one — recreating the very duplicate-backend state the lock
    exists to prevent. The module docstring claims one-workspace-one-backend holds
    "by construction"; this is the case where it silently did not.
    """
    write_lock(tmp_path, port=9001, pid=os.getpid() + 12345)  # someone else's lock

    clear_lock(tmp_path)

    surviving = read_lock(tmp_path)
    assert surviving is not None, "another process's lock was deleted"
    assert surviving.port == 9001


def test_clear_lock_removes_our_own(tmp_path: Path) -> None:
    write_lock(tmp_path, port=9002)
    clear_lock(tmp_path)
    assert read_lock(tmp_path) is None
