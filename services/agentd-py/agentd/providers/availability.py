"""Is this exception the provider being unavailable, rather than the model misbehaving?
(spec §3.3). Classified by predicate, not class: the transport re-raises the raw SDK
exception when its retries run out, and StreamDeadlineExceeded subclasses TimeoutError."""
from __future__ import annotations

from collections.abc import Iterator

from agentd.providers.context_overflow import ContextWindowExceeded
from agentd.providers.openai_compatible_transport import TransientTransportError
from agentd.providers.plan_access import ProviderAccessStopped

_UNAVAILABLE_STATUSES = frozenset({408, 429})


class ProviderUnavailable(RuntimeError):
    """The provider kept failing after the loop's own retries. A sub-agent's activation
    ends `failed_transient` (not `failed`): a correction message cannot fix an outage."""


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def find_context_overflow(exc: BaseException) -> ContextWindowExceeded | None:
    for current in _chain(exc):
        if isinstance(current, ContextWindowExceeded):
            return current
    return None


def _is_connection_error(exc: BaseException) -> bool:
    try:
        import openai
    except ImportError:  # the SDK is optional for some providers
        return False
    return isinstance(exc, openai.APIConnectionError)


def is_provider_unavailable(exc: BaseException) -> bool:
    # A plan usage limit is a 429 too, but waiting it out is the user's call (spec
    # 2026-10-06 §5.4): re-queueing would just keep hitting the same limit.
    if any(isinstance(current, ProviderAccessStopped) for current in _chain(exc)):
        return False
    # Gemini's quota overflow is raised from a 429: waiting it out can never make the
    # request fit, compacting can.
    if find_context_overflow(exc) is not None:
        return False
    for current in _chain(exc):
        status = getattr(current, "status_code", None)
        if isinstance(status, int) and (status in _UNAVAILABLE_STATUSES or status >= 500):
            return True
        if isinstance(current, (TimeoutError, TransientTransportError)):
            return True
        if _is_connection_error(current):
            return True
    return False
