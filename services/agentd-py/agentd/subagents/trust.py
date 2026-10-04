"""Trust records for agent definition files (spec §3.12). Stored outside the workspace so an
agent cannot grant itself trust, and keyed by content hash so any edit revokes it."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


class TrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".crucible" / "trust.json"
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".trust-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, self._path)

    def is_trusted(self, workspace: str, file_path: str, sha256: str) -> bool:
        return self._read().get(workspace, {}).get(file_path) == sha256

    def trust(self, workspace: str, file_path: str, sha256: str) -> None:
        with self._lock:
            data = self._read()
            data.setdefault(workspace, {})[file_path] = sha256
            self._write(data)

    def revoke(self, workspace: str, file_path: str) -> None:
        with self._lock:
            data = self._read()
            data.get(workspace, {}).pop(file_path, None)
            self._write(data)

    def version(self) -> int:
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return 0
