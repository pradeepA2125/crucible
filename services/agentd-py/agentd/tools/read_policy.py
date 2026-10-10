"""Shared bounded read delivery and request-local result headroom."""

from __future__ import annotations

import os
from dataclasses import dataclass


def adaptive_reads_enabled() -> bool:
    return os.getenv("CRUCIBLE_ADAPTIVE_READS_ENABLED", "").lower() in {"1", "true", "yes"}


def compact_context_enabled() -> bool:
    return os.getenv("CRUCIBLE_COMPACT_CONTEXT_ENABLED", "").lower() in {"1", "true", "yes"}


def result_budget(capacity: int | None, input_tokens: int) -> int:
    if capacity is None:
        return 16_000  # deterministic conservative fallback, no model-name guessing
    headroom = min(8192, max(1024, capacity // 10))
    available = max(0, capacity - input_tokens - headroom)
    return min(100_000, available * 2)  # half of chars/4 headroom, allowing estimation error


@dataclass(frozen=True)
class DeliveredRead:
    path: str
    root: str
    revision: str | None
    requested_start: int
    requested_end: int | None
    delivered_start: int | None
    delivered_end: int | None
    total_lines: int | None
    truncated_by: str | None

    @property
    def has_content(self) -> bool:
        return self.delivered_start is not None and self.delivered_end is not None


class ResultBudgetObserver:
    """Budget from this request's final prepared body; never borrow main/child UI occupancy."""

    def __init__(self, plan_context: dict, observer=None):
        self.context = plan_context
        self.observer = observer

    def _budget(self, tokens: int):
        capacity = self.context.get("context_capacity")
        capacity = capacity if type(capacity) is int and capacity > 0 else None
        self.context["tool_result_budget"] = result_budget(capacity, tokens)

    def begin(self, model, body):
        if self.context.get("capacity_model") != model:
            self.context["context_capacity"] = None
            self.context["capacity_source"] = "unknown"
        from agentd.providers.context_observation import estimate_request_input

        self._budget(estimate_request_input(model, body))
        if self.observer:
            self.observer.begin(model, body)

    def reported(self, value):
        from agentd.providers.context_observation import valid_input_tokens

        tokens = valid_input_tokens(value)
        if tokens is not None:
            self._budget(tokens)
        if self.observer:
            self.observer.reported(value)

    def finish(self):
        if self.observer:
            self.observer.finish()

    def unavailable(self):
        self.context["tool_result_budget"] = 16_000
        if self.observer:
            unavailable = getattr(self.observer, "unavailable", None)
            if unavailable:
                unavailable()
