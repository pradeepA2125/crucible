"""Crash-reap registry (the agentd.lock pattern): pids on disk so a restarted
backend can kill sessions a crashed predecessor leaked. Best-effort throughout —
registry IO must never break a turn or startup."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionRegistryFile:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, sessions: Iterable[Any]) -> None:
        entries = [{
            "session_id": s.session_id,
            "pid": s.proc.pid,
            "pgid": s.proc.pgid,
            "thread_id": s.thread_id,
            "command": s.command_line,
            # Recorded separately: command_line joins with spaces, which is
            # ambiguous when the executable path itself contains one.
            "executable": s.executable,
            "started_at": s.started_at,
        } for s in sessions]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("[exec-sessions] registry write failed", exc_info=True)

    def clear(self) -> None:
        self.record([])

    def reap_orphans(self) -> int:
        """Kill recorded pgids still alive from a crashed prior run. Guarded
        against pid reuse: the recorded executable's basename must appear in
        the live process's `ps` command line (basename, not full path — macOS
        `ps` shows the symlink-resolved binary, e.g. a venv `python` appears
        as `.../Python.app/Contents/MacOS/Python`, so a full-path match would
        never fire and orphans would silently survive). Always ends by
        clearing the file."""
        try:
            entries = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        killed = 0
        if sys.platform != "win32":
            for entry in entries if isinstance(entries, list) else []:
                killed += self._reap_one(entry)
        self.clear()
        return killed

    @staticmethod
    def _reap_one(entry: dict[str, Any]) -> int:
        try:
            pid, pgid = int(entry["pid"]), int(entry["pgid"])
            recorded = str(entry.get("command", ""))
            executable = str(entry.get("executable", "")) or recorded.split(" ", 1)[0]
        except (KeyError, TypeError, ValueError):
            return 0
        try:
            live_cmd = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 0
        # Basename-prefix match, both directions: a venv `python3.13` symlink
        # resolves to macOS's `.../MacOS/Python`, so neither full paths nor
        # exact basenames ever agree ("python3.13" vs "python").
        rec_base = Path(executable).name.lower()
        live_base = Path(live_cmd.split(" ", 1)[0]).name.lower()
        matched = bool(rec_base) and bool(live_base) and (
            rec_base.startswith(live_base) or live_base.startswith(rec_base))
        if not matched:
            return 0  # gone, or pid reused by something else — leave it alone
        try:
            os.killpg(pgid, signal.SIGTERM)
            logger.warning("[exec-sessions] reaped orphan pgid=%s (%s)", pgid, recorded)
            return 1
        except (ProcessLookupError, PermissionError):
            return 0
