"""In-memory per-running-task control channel for cooperative abort and the live-mutable
step-review preference. Single-process asyncio: check+set with no await between is race-safe."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


class TaskAborted(Exception):
    """Raised inside the execution loop when an abort signal is observed."""


@dataclass
class TaskControl:
    step_review_auto_accept: bool
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    abort_revert: bool = False
