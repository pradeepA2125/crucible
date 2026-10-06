"""Private files for ChatGPT credentials: owner-only, symlink-safe, atomic, lockable.

The same rules as the backend token file (`agentd/auth_token.py`): directories 0700,
files 0600, refuse anything another user owns or anything group/world-writable, never
follow a symlink, and replace files atomically so a reader never sees half a record.
Refresh tokens rotate, so every read-modify-write of a record happens under an
exclusive lock that other backend processes honor too.
"""
from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import secrets
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

_IS_POSIX = os.name == "posix"
if _IS_POSIX:
    import fcntl
else:  # pragma: no cover - exercised on Windows only
    import msvcrt


class CredentialFileError(RuntimeError):
    """A credential file or directory exists but is unsafe or unreadable."""


def ensure_private_dir(path: Path) -> Path:
    """Create `path` (and parents) as 0700 directories and check it is privately ours."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise CredentialFileError(f"{path} is not a directory")
    if _IS_POSIX:
        if st.st_uid != os.getuid():
            raise CredentialFileError(f"{path} is not owned by the current user")
        os.chmod(path, 0o700)
    return path


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    ensure_private_dir(path.parent)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_private_json(path: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CredentialFileError(f"{path} is a symlink") from exc
        raise
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        if _IS_POSIX:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise CredentialFileError(f"{path} is not a regular file")
            if st.st_uid != os.getuid():
                raise CredentialFileError(f"{path} is not owned by the current user")
            if st.st_mode & 0o077:
                raise CredentialFileError(f"{path} is readable by group or others")
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise CredentialFileError(f"{path} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise CredentialFileError(f"{path} does not hold a JSON object")
    return data


def _acquire(path: Path) -> int:
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if _IS_POSIX:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:  # pragma: no cover
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.asynccontextmanager
async def exclusive_lock(path: Path) -> AsyncIterator[None]:
    """Hold an exclusive lock on `path` (a sidecar lock file) across processes.

    Acquired in a worker thread so waiting never blocks the event loop, and held
    across awaits (a token refresh is an HTTP call made while holding it).
    """
    fd = await asyncio.to_thread(_acquire, path)
    try:
        yield
    finally:
        os.close(fd)  # releases the lock
