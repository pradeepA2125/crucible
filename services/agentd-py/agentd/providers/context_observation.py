"""Physical request input observation, deliberately separate from usage accounting.

Observers are synchronous and optional. Each begin replaces the previous physical
request; finish settles an unreported request as an estimate, never provider usage.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Protocol


class ContextInputObserver(Protocol):
    def begin(self, model: str, body: dict[str, Any]) -> None: ...
    def reported(self, value: object) -> None: ...
    def finish(self) -> None: ...


def valid_input_tokens(value: object) -> int | None:
    """Unlike accounting coercions, zero is valid and bool is not an integer."""
    return value if type(value) is int and value >= 0 else None


def estimate_request_input(model: str, body: dict[str, Any]) -> int:
    """Approximate all model-visible input using serialized characters / four.

    This is explicitly a heuristic, not provider accuracy. Include framing,
    instructions, history, native tool tails and every tool/grammar schema.
    Exclude dispatch controls and output reservations. The model argument leaves
    room for model-specific tokenizers without changing the observer interface.
    """
    fields = ('instructions', 'input', 'messages', 'tools', 'tool_choice',
              'functions', 'function_call', 'text', 'response_format')
    visible = {key: body[key] for key in fields if key in body}
    extra = body.get('extra_body')
    if isinstance(extra, dict):
        visible.update({key: extra[key] for key in fields if key in extra})
    serialized = json.dumps(visible, ensure_ascii=False, separators=(',', ':'), default=str)
    return (len(serialized) + 3) // 4


def _safe(observer: ContextInputObserver | None, method: str, *args: Any) -> None:
    if observer is None:
        return
    try:
        getattr(observer, method)(*args)
    except Exception:
        # Telemetry must not break a chat or change retry/accounting semantics.
        pass


def begin_context(observer: ContextInputObserver | None, body: dict[str, Any]) -> None:
    if observer is None:
        return
    try:
        # Neither observer mutations nor later retry mutations may affect the
        # actual dispatched body / the previous request snapshot.
        snapshot = copy.deepcopy(body)
    except Exception:
        return
    _safe(observer, 'begin', str(body.get('model', '')), snapshot)


def report_context(observer: ContextInputObserver | None, usage: Any, field: str) -> None:
    if observer is None:
        return
    try:
        raw = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        value = valid_input_tokens(raw)
        if value is not None:
            _safe(observer, 'reported', value)
    except Exception:
        pass


def finish_context(observer: ContextInputObserver | None) -> None:
    _safe(observer, 'finish')
