"""Backend auth token file (spec §3.2).

<home>/.crucible/run/agentd-<port>.token holds exactly the 43-character token. The
writer and the tidy share an flock on the run directory, so the tidy can never delete
a file a backend binding the same port has just written. Files are never deleted at
shutdown (spec §3.1); a live backend bumps its file's mtime daily, so "older than
7 days" means nothing has owned it for a week.
"""
from __future__ import annotations

import errno
import os
import re
import secrets
import socket
import stat
import time
from collections.abc import Callable
from pathlib import Path

if os.name == "posix":
    import fcntl

TOKEN_LEN = 43
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_NAME_RE = re.compile(r"agentd-(\d+)\.token")
_IS_POSIX = os.name == "posix"
_WINDOWS_REPLACE_RETRIES = 3


class TokenFileError(RuntimeError):
    """The token file or its directory exists but is unsafe or malformed."""


def run_dir(home: Path) -> Path:
    return home / ".crucible" / "run"


def token_path(home: Path, port: int) -> Path:
    return run_dir(home) / f"agentd-{port}.token"


def _require_private(path: Path, st: os.stat_result) -> None:
    if st.st_uid != os.getuid():
        raise TokenFileError(f"{path} is not owned by the current user")
    if st.st_mode & 0o022:
        raise TokenFileError(f"{path} is writable by group or others")


def _open_run_dir(home: Path) -> int:
    """Create and open <home>/.crucible/run, refusing anything not privately ours."""
    crucible = home / ".crucible"
    try:
        os.mkdir(crucible, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(crucible)
    if not stat.S_ISDIR(st.st_mode):
        raise TokenFileError(f"{crucible} is not a directory")
    _require_private(crucible, st)
    rd = run_dir(home)
    try:
        os.mkdir(rd, 0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(rd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise TokenFileError(f"{rd} is a symlink or not a directory") from exc
        raise
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise TokenFileError(f"{rd} is not owned by the current user")
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_token(home: Path, port: int) -> str:
    token = secrets.token_urlsafe(32)
    if not _IS_POSIX:
        _write_token_windows(home, port, token)
        return token
    fd = _open_run_dir(home)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = f".agentd-{port}.{secrets.token_hex(8)}.tmp"
        tfd = os.open(
            tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.write(tfd, token.encode("ascii"))
            os.fsync(tfd)
        finally:
            os.close(tfd)
        os.replace(tmp, f"agentd-{port}.token", src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)  # releases the flock
    return token


def _write_token_windows(home: Path, port: int, token: str) -> None:
    # Spec §6: user-private by ACL inheritance; os.replace can hit a transient
    # PermissionError while a reader holds the file open.
    rd = run_dir(home)
    rd.mkdir(parents=True, exist_ok=True)
    tmp = rd / f".agentd-{port}.{secrets.token_hex(8)}.tmp"
    tmp.write_bytes(token.encode("ascii"))
    for attempt in range(_WINDOWS_REPLACE_RETRIES):
        try:
            os.replace(tmp, token_path(home, port))
            return
        except PermissionError:
            if attempt == _WINDOWS_REPLACE_RETRIES - 1:
                raise
            time.sleep(0.1)


def read_token(home: Path, port: int) -> str | None:
    path = token_path(home, port)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise TokenFileError(f"{path} is a symlink") from exc
        raise
    try:
        if _IS_POSIX:
            st = os.fstat(fd)  # the descriptor, never stat-then-open
            if not stat.S_ISREG(st.st_mode):
                raise TokenFileError(f"{path} is not a regular file")
            if st.st_uid != os.getuid():
                raise TokenFileError(f"{path} is not owned by the current user")
            if st.st_mode & 0o077:
                raise TokenFileError(f"{path} is readable by group or others")
        data = os.read(fd, TOKEN_LEN + 1)
    finally:
        os.close(fd)
    text = data.decode("ascii", errors="replace")
    if not _TOKEN_RE.fullmatch(text):
        raise TokenFileError(f"{path} does not hold a token")
    return text


def touch_token(home: Path, port: int) -> None:
    try:
        os.utime(token_path(home, port), follow_symlinks=False)
    except OSError:
        pass  # best-effort keep-alive


def port_refuses_connection(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except ConnectionRefusedError:
            return True
        except OSError:
            return False  # timeout, backlog full, …: not proof of death
        return False


def tidy_tokens(
    home: Path,
    *,
    max_age_sec: float = 7 * 86400,
    budget_sec: float = 2.0,
    now: Callable[[], float] = time.time,
    port_refuses: Callable[[int], bool] = port_refuses_connection,
) -> list[int]:
    if not _IS_POSIX:
        return []  # spec §6: no dir_fd on Windows
    deadline = time.monotonic() + budget_sec
    removed: list[int] = []
    try:
        fd = _open_run_dir(home)
    except (OSError, TokenFileError):
        return removed
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        for name in os.listdir(fd):
            if time.monotonic() > deadline:
                break
            match = _NAME_RE.fullmatch(name)
            if match is None:
                continue
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or now() - st.st_mtime <= max_age_sec:
                continue
            port = int(match.group(1))
            if port_refuses(port):
                os.unlink(name, dir_fd=fd)
                removed.append(port)
    except OSError:
        pass  # best-effort: never stops startup
    finally:
        os.close(fd)
    return removed
