"""Synchronous admission at the transport boundary, shared by team descendants."""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar


class RequestBudgetExhausted(Exception):
    """No model call was admitted; finish using persisted evidence instead."""


REQUEST_ADMISSION: ContextVar[Callable[[], None] | None] = ContextVar(
    "crucible_request_admission", default=None
)
