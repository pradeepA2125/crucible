"""Opt-in async wall-time spans and synchronous cProfile captures, without request data."""

from __future__ import annotations

import cProfile
import functools
import json
import logging
import pstats
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capture:
    path: Path
    trace: str


_capture: ContextVar[Capture | None] = ContextVar("performance_capture", default=None)
_parent: ContextVar[str | None] = ContextVar("performance_parent", default=None)


def _write(record: dict[str, object]) -> None:
    capture = _capture.get()
    if capture is None:
        return
    try:
        capture.path.parent.mkdir(parents=True, exist_ok=True)
        with capture.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"trace": capture.trace, **record}) + "\n")
    except OSError:
        log.debug("Performance capture unavailable", exc_info=True)


@contextmanager
def span(name: str) -> Iterator[None]:
    if _capture.get() is None:
        yield
        return
    identity = uuid.uuid4().hex
    parent = _parent.get()
    token = _parent.set(identity)
    started = time.perf_counter_ns()
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error_or_cancelled"
        raise
    finally:
        ended = time.perf_counter_ns()
        _parent.reset(token)
        _write({"kind": "span", "id": identity, "parent": parent, "name": name,
                "start_ns": started, "end_ns": ended, "status": status})


def mark(name: str) -> None:
    if _capture.get() is not None:
        _write({"kind": "mark", "parent": _parent.get(), "name": name,
                "at_ns": time.perf_counter_ns()})


def context_measurement(measurement: dict[str, object]) -> None:
    _write({"kind": "context", "parent": _parent.get(), **measurement})


def capturing() -> bool:
    return _capture.get() is not None


def profile_async(name: str) -> Callable:
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            with span(name):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate


def profile_sync(name: str) -> Callable:
    """Profile only synchronous work; never attribute awaited I/O to Python CPU work."""
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if _capture.get() is None:
                return fn(*args, **kwargs)
            profiler = cProfile.Profile()
            try:
                with span(name):
                    return profiler.runcall(fn, *args, **kwargs)
            finally:
                stats = pstats.Stats(profiler)
                rows = []
                for (file, line, function), (_, calls, own, cumulative, _) in stats.stats.items():
                    path = Path(file)
                    package = Path(__file__).resolve().parent
                    try:
                        label = "agentd/" + str(path.resolve().relative_to(package))
                    except ValueError:
                        label = path.name
                    rows.append({"function": f"{label}:{line}:{function}",
                                 "calls": calls, "self_ms": own * 1000,
                                 "inclusive_ms": cumulative * 1000})
                rows.sort(key=lambda row: row["self_ms"], reverse=True)
                _write({"kind": "functions", "name": name, "rows": rows[:40]})
        return wrapped
    return decorate


def profile_controller_run(fn: Callable) -> Callable:
    @functools.wraps(fn)
    async def wrapped(self: Any, plan_context: dict, *args: Any, **kwargs: Any) -> Any:
        if capturing():
            with span("controller.turn"):
                return await fn(self, plan_context, *args, **kwargs)
        workspace = plan_context.get("workspace_path")
        state = Path(workspace) / ".crucible" / "state" if workspace else None
        if state is None or not (state / "performance.enabled").is_file():
            return await fn(self, plan_context, *args, **kwargs)
        token = _capture.set(Capture(state / "performance.jsonl", uuid.uuid4().hex))
        parent_token = _parent.set(None)
        try:
            with span("controller.turn"):
                return await fn(self, plan_context, *args, **kwargs)
        finally:
            _parent.reset(parent_token)
            _capture.reset(token)
    return wrapped


def profile_workspace_turn(fn: Callable) -> Callable:
    """Include controller setup/retrieval, before ControllerLoop.run starts."""
    @functools.wraps(fn)
    async def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        workspace = getattr(self, "_workspace_path", None)
        state = Path(workspace) / ".crucible" / "state" if workspace else None
        if capturing() or state is None or not (state / "performance.enabled").is_file():
            return await fn(self, *args, **kwargs)
        token = _capture.set(Capture(state / "performance.jsonl", uuid.uuid4().hex))
        parent_token = _parent.set(None)
        try:
            with span("controller.backend_turn"):
                return await fn(self, *args, **kwargs)
        finally:
            _parent.reset(parent_token)
            _capture.reset(token)
    return wrapped


def interval_union(intervals: list[tuple[int, int]]) -> int:
    total = 0
    end = 0
    for left, right in sorted(intervals):
        total += max(0, right - max(left, end))
        end = max(end, right)
    return total


def summarize(records: list[dict]) -> dict[str, object]:
    spans = [r for r in records if r.get("kind") == "span"]
    by_id = {(r["trace"], r["id"]): r for r in spans}
    children_by_parent: dict[tuple[str, str], list[dict]] = {}
    for row in spans:
        children_by_parent.setdefault((row["trace"], row.get("parent")), []).append(row)
    grouped: dict[str, list[tuple[float, float]]] = {}
    for row in spans:
        start, end = row["start_ns"], row["end_ns"]
        children = children_by_parent.get((row["trace"], row["id"]), [])
        covered = interval_union([(max(start, c["start_ns"]), min(end, c["end_ns"]))
                                  for c in children if c["start_ns"] < end
                                  and c["end_ns"] > start])
        grouped.setdefault(row["name"], []).append(
            ((end - start) / 1e6, max(0, end - start - covered) / 1e6))
    milestones: dict[str, list[float]] = {}
    for row in records:
        if row.get("kind") == "mark":
            parent = by_id.get((row["trace"], row.get("parent")))
            if parent:
                duration = (row["at_ns"] - parent["start_ns"]) / 1e6
                milestones.setdefault(row["name"], []).append(duration)
    result = []
    for name, samples in grouped.items():
        values = sorted(s[0] for s in samples)
        result.append({"block": name, "calls": len(values),
                       "inclusive_ms": sum(values), "self_ms": sum(s[1] for s in samples),
                       "p50_ms": values[(len(values) - 1) // 2],
                       "p95_ms": values[max(0, (95 * len(values) + 99) // 100 - 1)],
                       "max_ms": values[-1]})
    result.sort(key=lambda r: r["self_ms"], reverse=True)
    functions: dict[str, dict[str, object]] = {}
    for record in records:
        if record.get("kind") != "functions":
            continue
        for row in record["rows"]:
            target = functions.setdefault(row["function"], {
                "function": row["function"], "calls": 0, "self_ms": 0.0, "inclusive_ms": 0.0})
            for key in ("calls", "self_ms", "inclusive_ms"):
                target[key] += row[key]
    return {"context": [r for r in records if r.get("kind") == "context"],
            "milestones": {name: {"calls": len(values), "mean_ms": sum(values) / len(values),
                            "max_ms": max(values)} for name, values in milestones.items()},
            "blocks": result, "functions": sorted(functions.values(),
            key=lambda r: r["self_ms"], reverse=True),
            "note": "Self time excludes direct child spans, but includes awaits. It is not CPU "
                    "time. Concurrent span totals may overlap. First-content latency is a "
                    "milestone, not an additional execution block. Function profiles cover "
                    "synchronous prompt construction only; timings include profiler overhead."}
