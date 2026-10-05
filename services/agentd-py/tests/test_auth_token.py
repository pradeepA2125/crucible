"""Token file (spec §3.2): owner-only, atomic, symlink-safe, and a tidy that can never
delete a live backend's file."""
from __future__ import annotations

import fcntl
import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

from agentd.auth_token import (
    TOKEN_LEN,
    TokenFileError,
    port_refuses_connection,
    read_token,
    run_dir,
    tidy_tokens,
    token_path,
    touch_token,
    write_token,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file semantics")


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    token = write_token(tmp_path, 8123)
    assert len(token) == TOKEN_LEN
    assert read_token(tmp_path, 8123) == token
    assert token_path(tmp_path, 8123).read_bytes() == token.encode("ascii")  # no newline


def test_modes(tmp_path: Path) -> None:
    write_token(tmp_path, 8123)
    assert stat.S_IMODE(os.stat(token_path(tmp_path, 8123)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(run_dir(tmp_path)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / ".crucible").st_mode) == 0o700


def test_existing_run_dir_is_tightened(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir(mode=0o700)
    run_dir(tmp_path).mkdir(mode=0o755)
    os.chmod(run_dir(tmp_path), 0o755)
    write_token(tmp_path, 1)
    assert stat.S_IMODE(os.stat(run_dir(tmp_path)).st_mode) == 0o700


def test_refuses_group_writable_crucible_dir(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir()
    os.chmod(tmp_path / ".crucible", 0o775)
    with pytest.raises(TokenFileError):
        write_token(tmp_path, 1)


def test_refuses_symlinked_run_dir(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    os.symlink(elsewhere, run_dir(tmp_path))
    with pytest.raises((TokenFileError, OSError)):
        write_token(tmp_path, 1)
    assert list(elsewhere.iterdir()) == []


def test_write_replaces_an_old_file(tmp_path: Path) -> None:
    first = write_token(tmp_path, 9)
    second = write_token(tmp_path, 9)
    assert first != second
    assert read_token(tmp_path, 9) == second


def test_read_missing_is_none(tmp_path: Path) -> None:
    assert read_token(tmp_path, 4) is None


def test_read_refuses_symlink(tmp_path: Path) -> None:
    write_token(tmp_path, 5)
    real = token_path(tmp_path, 5)
    link = token_path(tmp_path, 6)
    os.symlink(real, link)
    with pytest.raises(TokenFileError):
        read_token(tmp_path, 6)


def test_read_refuses_group_readable(tmp_path: Path) -> None:
    write_token(tmp_path, 7)
    os.chmod(token_path(tmp_path, 7), 0o640)
    with pytest.raises(TokenFileError):
        read_token(tmp_path, 7)


@pytest.mark.parametrize("content", [b"x" * 43 + b"\n", b"short", b"!" * 43, b""])
def test_read_refuses_malformed(tmp_path: Path, content: bytes) -> None:
    write_token(tmp_path, 8)
    path = token_path(tmp_path, 8)
    path.write_bytes(content)
    os.chmod(path, 0o600)
    with pytest.raises(TokenFileError):
        read_token(tmp_path, 8)


def _age(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old), follow_symlinks=False)


def test_tidy_removes_old_file_whose_port_refuses(tmp_path: Path) -> None:
    write_token(tmp_path, 40001)
    _age(token_path(tmp_path, 40001), 8)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: True) == [40001]
    assert not token_path(tmp_path, 40001).exists()


def test_tidy_keeps_fresh_file(tmp_path: Path) -> None:
    write_token(tmp_path, 40002)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: True) == []
    assert token_path(tmp_path, 40002).exists()


def test_tidy_keeps_file_when_connect_is_not_refused(tmp_path: Path) -> None:
    # A timeout or any other connect error is NOT proof the port is dead.
    write_token(tmp_path, 40003)
    _age(token_path(tmp_path, 40003), 8)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: False) == []
    assert token_path(tmp_path, 40003).exists()


def test_tidy_keeps_symlink_and_foreign_names(tmp_path: Path) -> None:
    write_token(tmp_path, 40004)
    rd = run_dir(tmp_path)
    os.symlink(token_path(tmp_path, 40004), rd / "agentd-40005.token")
    (rd / "notes.txt").write_text("x")
    for name in ("agentd-40005.token", "notes.txt"):
        _age(rd / name, 8)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: True) == []
    assert (rd / "agentd-40005.token").is_symlink()
    assert (rd / "notes.txt").exists()


def test_port_refuses_connection_is_false_for_a_listener() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        assert port_refuses_connection(srv.getsockname()[1]) is False


def test_port_refuses_connection_is_true_for_a_closed_port() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port_refuses_connection(port) is True


def test_writer_waits_for_tidy_lock(tmp_path: Path) -> None:
    write_token(tmp_path, 1)  # creates the run dir
    fd = os.open(run_dir(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()
    thread = threading.Thread(target=lambda: (write_token(tmp_path, 2), done.set()))
    thread.start()
    time.sleep(0.2)
    assert not done.is_set()  # blocked behind the lock
    assert not token_path(tmp_path, 2).exists()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    thread.join(5)
    assert done.is_set()
    assert read_token(tmp_path, 2) is not None


def test_touch_bumps_mtime(tmp_path: Path) -> None:
    write_token(tmp_path, 3)
    _age(token_path(tmp_path, 3), 8)
    touch_token(tmp_path, 3)
    assert time.time() - token_path(tmp_path, 3).stat().st_mtime < 60
