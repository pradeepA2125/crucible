"""Per-workspace backend lockfile: <workspace>/.crucible/state/agentd.lock (JSON pid/port/
started_at). The extension reuses a live backend and reaps stale locks — this file
is what makes one-workspace-one-backend hold by construction. Written by
`agentd.serve --workspace-lock` (spec §3.1); a hint that reuse verifies (§3.7), never
deleted at shutdown."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LockInfo:
    pid: int
    port: int
    started_at: float


def _lock_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".crucible/state" / "agentd.lock"


def write_lock(
    workspace: str | Path, *, port: int, pid: int, started_at: float,
) -> None:
    """Atomic and symlink-safe: a fresh O_EXCL|O_NOFOLLOW temp file, then rename (which
    replaces a planted symlink rather than writing through it)."""
    path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".agentd.lock.{secrets.token_hex(8)}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o644)
    try:
        os.write(fd, json.dumps(
            {"pid": pid, "port": port, "started_at": started_at}).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def read_lock(workspace: str | Path) -> LockInfo | None:
    try:
        raw = json.loads(_lock_path(workspace).read_text(encoding="utf-8"))
        return LockInfo(
            pid=int(raw["pid"]), port=int(raw["port"]), started_at=float(raw["started_at"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError):
        return True  # exists but not ours / unprobeable — treat as alive (conservative)
    return True
