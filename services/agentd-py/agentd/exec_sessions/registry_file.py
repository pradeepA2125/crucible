# Task-3 stub; Task 4 implements for real.
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class SessionRegistryFile:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, sessions: Iterable[object]) -> None:
        pass

    def clear(self) -> None:
        pass

    def reap_orphans(self) -> int:
        return 0
