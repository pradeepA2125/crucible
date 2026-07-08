"""Per-workspace backend lockfile: <workspace>/.crucible/state/agentd.lock (JSON pid/port/
started_at). The extension reuses a live backend and reaps stale locks — this file
is what makes one-workspace-one-backend hold by construction. Written only when
CRUCIBLE_PORT is set (managed spawns); the dev script flow is unaffected."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LockInfo:
    pid: int
    port: int
    started_at: float


def _lock_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".crucible/state" / "agentd.lock"


def write_lock(workspace: str | Path, *, port: int, pid: int | None = None) -> None:
    path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": pid or os.getpid(), "port": port, "started_at": time.time()}),
        encoding="utf-8",
    )


def read_lock(workspace: str | Path) -> LockInfo | None:
    try:
        raw = json.loads(_lock_path(workspace).read_text(encoding="utf-8"))
        return LockInfo(
            pid=int(raw["pid"]), port=int(raw["port"]), started_at=float(raw["started_at"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_lock(workspace: str | Path) -> None:
    try:
        _lock_path(workspace).unlink()
    except OSError:
        pass


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError):
        return True  # exists but not ours / unprobeable — treat as alive (conservative)
    return True
