"""The coordinator trace (spec v2 §8.11): append-only jsonl, best-effort."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class CoordinatorTrace:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, kind: str, **data: object) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"at": datetime.now(UTC).isoformat(), "kind": kind, **data},
                              default=str)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 — a trace must never fail the team
            logger.warning("[teams] trace write failed %s", self._path, exc_info=True)
