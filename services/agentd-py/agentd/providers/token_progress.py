"""Shared live-token-progress accumulator for streaming transports.

Every transport with a delta loop has the same job: separate reasoning from output,
report running counts often enough that the UI looks alive but rarely enough not to
flood the SSE channel, and replace the estimate with the provider's own numbers once
the stream ends. Written out per transport that is four near-identical copies of a
throttle and a division, each free to drift.

Counts are CHARACTERS divided by four while streaming. No streaming format here
carries per-delta token counts, so the live figure is unavoidably an estimate; the
`exact` flag is what lets the UI say so rather than presenting a guess as settled.

Deliberately not retrofitted onto openai_compatible_transport or ollama_transport,
which already have working inline versions — swapping known-good code for a shared
abstraction is a change with risk and no user-visible benefit.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

# Matches the interval already used by openai_compatible/ollama so every provider
# feels identical in the UI. Measured ~29 deltas/sec there; one broadcast per delta
# would swamp the channel.
DEFAULT_PROGRESS_INTERVAL_SEC = 0.15

# Streaming formats carry no per-delta token counts, so tokens are estimated from
# characters. Four is the usual English+code approximation and matches
# `_approx_tokens` in openai_compatible_transport.
_CHARS_PER_TOKEN = 4


class ProgressTicker:
    """Accumulate reasoning/output characters and emit throttled progress.

    A None callback makes every method a no-op, so callers never need to guard
    each call site — the capability gate upstream already decides whether progress
    is wanted at all.
    """

    def __init__(
        self,
        on_progress: Callable[..., None] | None,
        *,
        interval_sec: float = DEFAULT_PROGRESS_INTERVAL_SEC,
        input_n: int | None = None,
    ) -> None:
        self._on_progress = on_progress if callable(on_progress) else None
        self._interval = interval_sec
        self._input_n = input_n
        self._thinking_chars = 0
        self._output_chars = 0
        # None until the first tick: monotonic() counts from boot, so a 0.0 here throttled
        # the first tick on a host up for less than the interval.
        self._last_emit: float | None = None

    def start(self) -> None:
        """Report the prompt size before any delta arrives.

        Prefill generates nothing and is most of the wall time on a large prompt, so
        without this the counter reads zero for minutes and a slow call cannot be
        told apart from a wedged one.
        """
        if self._on_progress is not None and self._input_n:
            self._on_progress(0, 0, input_n=self._input_n, exact=False)

    def thinking(self, text: str) -> None:
        if text:
            self._thinking_chars += len(text)
            self._maybe_emit()

    def output(self, text: str) -> None:
        if text:
            self._output_chars += len(text)
            self._maybe_emit()

    def finish(
        self,
        *,
        thinking_tokens: int | None = None,
        output_tokens: int | None = None,
        input_tokens: int | None = None,
    ) -> None:
        """Closing tick. Always fires — throttling can swallow the last live update,
        and this is the only point where exact numbers exist.

        A provider count supersedes the corresponding estimate; `exact` is true only
        when the provider actually supplied the output count, since that is the
        figure a user reads as the cost of the call.
        """
        if self._on_progress is None:
            return
        self._on_progress(
            thinking_tokens if thinking_tokens is not None else self._est(self._thinking_chars),
            output_tokens if output_tokens is not None else self._est(self._output_chars),
            input_n=input_tokens if input_tokens is not None else self._input_n,
            exact=output_tokens is not None,
        )

    def _est(self, chars: int) -> int:
        return chars // _CHARS_PER_TOKEN

    def _maybe_emit(self) -> None:
        if self._on_progress is None:
            return
        now = time.monotonic()
        if self._last_emit is not None and now - self._last_emit < self._interval:
            return
        self._last_emit = now
        self._on_progress(
            self._est(self._thinking_chars),
            self._est(self._output_chars),
            input_n=self._input_n,
            exact=False,
        )


def int_or_none(value: Any) -> int | None:
    """Coerce a provider-reported count, or None when it is absent/garbage.

    Usage fields are optional across every provider here and some emit them as
    strings or nulls. A bad value must degrade to "estimate" rather than raise —
    a token counter is never worth failing a call over.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a token count
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def approx_prompt_tokens(*texts: Any) -> int | None:
    """Rough prompt size from the strings a transport is about to send.

    Returns None rather than 0 for an empty prompt so callers can treat "unknown"
    and "nothing to send" the same way — the UI omits the figure either way.
    """
    total = sum(len(t) for t in texts if isinstance(t, str))
    return (total // _CHARS_PER_TOKEN) or None
