"""Startup work that must not delay serving.

The managed runtime treats GET /health as the readiness signal and stops a backend that
is not healthy within 60s (apps/vscode-extension/src/runtime/backend-process.ts), and
Starlette serves nothing until every startup handler has returned. Best-effort work that
calls out to a provider therefore runs as a background task instead of a startup step.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

# Strong references: the event loop keeps only weak ones, and an unreferenced task can
# be garbage-collected before it finishes.
_BACKGROUND: set[asyncio.Task[None]] = set()


def in_background(
    work: Callable[[], Awaitable[None]], name: str,
) -> Callable[[], Awaitable[None]]:
    """A startup handler that starts `work` and returns at once. `work` must handle its
    own errors: nothing awaits it."""

    async def start() -> None:
        task: asyncio.Task[None] = asyncio.create_task(work(), name=name)  # type: ignore[arg-type]
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)

    return start


def background_tasks() -> frozenset[asyncio.Task[None]]:
    return frozenset(_BACKGROUND)
