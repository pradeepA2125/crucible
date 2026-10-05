import json
import os
import time
from pathlib import Path

from agentd.runtime_lock import LockInfo, is_pid_alive, read_lock, write_lock


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    write_lock(tmp_path, port=8123, pid=os.getpid(), started_at=time.time())
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


def test_is_pid_alive() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(2**22 + 12345) is False  # exceeds default pid_max


def test_write_lock_replaces_a_symlink_without_following_it(tmp_path: Path) -> None:
    state = tmp_path / ".crucible/state"
    state.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text("keep")
    os.symlink(target, state / "agentd.lock")
    write_lock(tmp_path, port=1, pid=2, started_at=3.0)
    assert target.read_text() == "keep"
    assert read_lock(tmp_path) == LockInfo(pid=2, port=1, started_at=3.0)


def test_write_leaves_no_temp_files(tmp_path: Path) -> None:
    write_lock(tmp_path, port=1, pid=2, started_at=3.0)
    assert sorted(p.name for p in (tmp_path / ".crucible/state").iterdir()) == ["agentd.lock"]
