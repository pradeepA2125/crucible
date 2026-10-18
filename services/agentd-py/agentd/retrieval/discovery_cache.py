"""One bounded, file-versioned discovery snapshot; never caches query results."""
from __future__ import annotations

import json
import math
import os
import sys
import threading
from pathlib import Path
from typing import Any

DEFAULT_MAX_MB = 256
# The client builds id/path/edge indexes over the cached graph (`_DiscoveryGraph`).
# Measured with tracemalloc on this repo's snapshot (26k nodes, 71k edges): payload
# 23.9 MB + indexes 12.3 MB = 36.2 MB. Scaling per-row cost by 1.7 estimates 36.7 MB,
# so the budget covers both and errs high.
_DERIVED_OVERHEAD = 1.7


def _max_bytes_from_env() -> int:
    """`CRUCIBLE_DISCOVERY_CACHE_MAX_MB`; 0 never caches; invalid means the default."""
    raw = os.environ.get("CRUCIBLE_DISCOVERY_CACHE_MAX_MB")
    try:
        value = float(raw) if raw is not None else DEFAULT_MAX_MB
    except ValueError:
        value = DEFAULT_MAX_MB
    if not math.isfinite(value) or value < 0:
        value = DEFAULT_MAX_MB
    return int(value * 1024 * 1024)


class _OverBudget(Exception):
    pass


def snapshot_version(path: Path) -> tuple[str, int, int, int, int, int]:
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def _compact(items: list[Any], keys: tuple[str, ...], strings: dict[str, str],
             budget: list[float]) -> list[dict[str, Any]]:
    """Copy the retained keys, sharing repeated strings, and charge the estimated
    memory to `budget[0]`; raises _OverBudget as soon as it goes negative so an
    oversized graph is not compacted to the end only to be dropped."""
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = {}
        cost = 0
        for key in keys:
            if key in item:
                value = item[key]
                if isinstance(value, str):
                    shared = strings.get(value)
                    if shared is None:
                        strings[value] = shared = value
                        cost += sys.getsizeof(value)
                    value = shared
                row[key] = value
        result.append(row)
        # Row dict + its slot in the list, scaled for the indexes built over it.
        budget[0] -= (sys.getsizeof(row) + 8) * _DERIVED_OVERHEAD + cost
        if budget[0] < 0:
            raise _OverBudget
    return result


class DiscoverySnapshotCache:
    """Retain one compact metadata graph; failures never return an older revision.

    Repeated IDs, paths, names and kinds share strings within this snapshot.
    Diagnostics and arbitrary extra payload fields are not retained. A graph whose
    estimated memory exceeds `max_bytes` uses the ordinary uncached path, re-parsed
    per call. The lock serializes publication, not scoring.
    """

    def __init__(self, max_bytes: int | None = None) -> None:
        self.max_bytes = _max_bytes_from_env() if max_bytes is None else max_bytes
        self._key: tuple[str, int, int, int, int, int] | None = None
        self._payload: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def load(self, path: Path) -> dict[str, Any]:
        with self._lock:
            try:
                for _ in range(2):
                    before = snapshot_version(path)
                    if before == self._key and self._payload is not None:
                        return self._payload
                    # Drop this payload reference before decoding its replacement.
                    self._key, self._payload = None, None
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Retrieval snapshot must be an object")
                    payload.pop("_discovery_cache_key", None)
                    if snapshot_version(path) != before:
                        continue
                    graph = payload.get("graph")
                    if not isinstance(graph, dict):
                        return payload
                    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
                    if not isinstance(nodes, list) or not isinstance(edges, list):
                        return payload
                    strings: dict[str, str] = {}
                    budget = [float(self.max_bytes)]
                    try:
                        compact_nodes = _compact(
                            nodes, ("id", "path", "name", "kind", "line"), strings, budget)
                        compact_edges = _compact(edges, ("from", "to", "kind"), strings, budget)
                    except _OverBudget:
                        return payload
                    retained = {
                        "workspace_root": payload.get("workspace_root"),
                        "generated_at_ms": payload.get("generated_at_ms"),
                        "graph": {"nodes": compact_nodes, "edges": compact_edges},
                        "_discovery_cache_key": before,
                    }
                    self._key, self._payload = before, retained
                    return retained
                raise OSError("Retrieval snapshot changed while loading")
            except (OSError, ValueError):
                self._key, self._payload = None, None
                raise
