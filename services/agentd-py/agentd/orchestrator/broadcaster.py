"""
This module provides the PatchEventBroadcaster, which facilitates the distribution of
real-time events during the patch generation and orchestration process.

The PatchEventBroadcaster allows various components (subscribers) to listen for
specific task events, such as when a patch operation succeeds or fails. It maintains
a small replay buffer per task so that clients connecting slightly late still receive
recent events they missed.

Usage::

    broadcaster = PatchEventBroadcaster()

    # Producer: broadcast an event (e.g. from inside apply_patch_candidate)
    broadcaster.broadcast(task_id, {"type": "operation_success", "op_type": "search_replace", "path": "foo.py"})

    # Consumer: subscribe, drain replay buffer, then await new events
    queue = broadcaster.subscribe(task_id)
    try:
        while True:
            event = await queue.get()
            process(event)
            if event.get("type") == "done":
                break
    finally:
        broadcaster.unsubscribe(task_id, queue)
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

_REPLAY_BUFFER_SIZE = 50


class PatchEventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._replay: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_REPLAY_BUFFER_SIZE))

    def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self._replay[task_id]:
            queue.put_nowait(event)
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(task_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(task_id, None)

    def broadcast(self, task_id: str, event: dict[str, Any]) -> None:
        self._replay[task_id].append(event)
        for queue in self._subscribers.get(task_id, set()):
            queue.put_nowait(event)

    def clear_replay(self, task_id: str) -> None:
        self._replay.pop(task_id, None)
