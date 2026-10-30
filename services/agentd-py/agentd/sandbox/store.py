"""This workspace's sandbox settings: remembered hosts and extra writable folders.

Kept per workspace under the home folder, outside the wall, never in the repository: a file
in a cloned repo or written by a command must not be able to widen the sandbox.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from agentd.sandbox.hosts import normalize_host

logger = logging.getLogger(__name__)


class WorkspaceSandboxStore:
    def __init__(self, workspace: Path, home: Path) -> None:
        key = hashlib.sha256(os.path.realpath(workspace).encode()).hexdigest()[:16]
        root = home / ".crucible" / "sandbox" / key
        self._hosts_path = root / "hosts.json"
        self._config_path = root / "folders.json"

    def _read(self, path: Path, key: str) -> list[str]:
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning("[sandbox] ignoring unreadable %s", path)
            return []
        values = data.get(key) if isinstance(data, dict) else None
        if not isinstance(values, list):
            logger.warning("[sandbox] ignoring malformed %s", path)
            return []
        return [v for v in values if isinstance(v, str)]

    def _write(self, path: Path, key: str, values: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name)
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps({key: values}, indent=2) + "\n")
        os.replace(tmp, path)

    def hosts(self) -> list[str]:
        result: list[str] = []
        for raw in self._read(self._hosts_path, "hosts"):
            try:
                host = normalize_host(raw)
            except ValueError:
                continue
            if host not in result:
                result.append(host)
        return result

    def set_hosts(self, hosts: list[str]) -> None:
        normalized = list(dict.fromkeys(normalize_host(h) for h in hosts))
        self._write(self._hosts_path, "hosts", normalized)

    def add_host(self, host: str) -> None:
        self.set_hosts([*self.hosts(), host])

    def remove_host(self, host: str) -> None:
        target = normalize_host(host)
        self.set_hosts([h for h in self.hosts() if h != target])

    def folders(self) -> list[Path]:
        return [Path(p) for p in self._read(self._config_path, "writable")]

    def set_folders(self, folders: list[Path]) -> None:
        resolved: list[str] = []
        for folder in folders:
            if not folder.is_dir():
                raise ValueError(f"not a folder: {folder}")
            resolved.append(str(folder.resolve()))
        self._write(self._config_path, "writable", list(dict.fromkeys(resolved)))
