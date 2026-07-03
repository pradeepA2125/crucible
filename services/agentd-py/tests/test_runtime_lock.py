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
    raw = json.loads((tmp_path / ".agentd" / "agentd.lock").read_text())
    assert set(raw) == {"pid", "port", "started_at"}


def test_read_missing_or_corrupt_returns_none(tmp_path: Path) -> None:
    assert read_lock(tmp_path) is None
    (tmp_path / ".agentd").mkdir()
    (tmp_path / ".agentd" / "agentd.lock").write_text("{not json")
    assert read_lock(tmp_path) is None


def test_clear_lock_is_idempotent(tmp_path: Path) -> None:
    write_lock(tmp_path, port=1)
    clear_lock(tmp_path)
    clear_lock(tmp_path)
    assert read_lock(tmp_path) is None


def test_is_pid_alive() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(2**22 + 12345) is False  # exceeds default pid_max
