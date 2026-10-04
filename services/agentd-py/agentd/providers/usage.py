"""Per-owner request and token counters (spec §3.11). The owner is set by whoever runs a
loop (an activation: its agent id; a main turn: thread:<id>) and read by the transport
wrapper and the loop's usage callback."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

USAGE_OWNER: ContextVar[str | None] = ContextVar("crucible_usage_owner", default=None)


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wait_ms: int = 0


class UsageMeter:
    def __init__(self) -> None:
        self._by_owner: dict[str, Usage] = {}

    def record(self, owner: str | None, *, requests: int = 0, prompt: int = 0,
               completion: int = 0, wait_ms: int = 0) -> None:
        if owner is None:
            return
        usage = self._by_owner.setdefault(owner, Usage())
        usage.requests += requests
        usage.prompt_tokens += prompt
        usage.completion_tokens += completion
        usage.wait_ms += wait_ms

    def take(self, owner: str) -> Usage:
        return self._by_owner.pop(owner, Usage())


METER = UsageMeter()
