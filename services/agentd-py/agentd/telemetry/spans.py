"""Span helpers for the four traced seams (observability spec §6.1, §6.2).

Every helper is a no-op with tracing off and never lets a tracing error reach the caller:
attribute and exporter failures are logged at DEBUG and swallowed. OpenTelemetry is imported
lazily, inside functions that only run once a tracer exists. Attribute names are the
OpenInference conventions, written out so this module loads without that package
(`tests/test_telemetry_llm_spans.py` pins them to the package's constants)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from agentd.telemetry.context import CALL_PURPOSE
from agentd.telemetry.redact import cap_text
from agentd.telemetry.tracer import capture_content, get_tracer

logger = logging.getLogger(__name__)

SPAN_KIND = "openinference.span.kind"
SESSION_ID = "session.id"
INPUT_VALUE = "input.value"
OUTPUT_VALUE = "output.value"
LLM_MODEL = "llm.model_name"
LLM_PROVIDER = "llm.provider"
LLM_PROMPT = "llm.token_count.prompt"
LLM_COMPLETION = "llm.token_count.completion"
LLM_TOTAL = "llm.token_count.total"
LLM_CACHE_READ = "llm.token_count.prompt_details.cache_read"
TOOL_NAME = "tool.name"
TOOL_PARAMETERS = "tool.parameters"

PLAN_BILLED_BACKENDS = frozenset({"chatgpt", "claude_subscription"})
_PROVIDERS = {"openai": "openai", "chatgpt": "openai", "anthropic": "anthropic",
              "claude_subscription": "anthropic", "gemini": "google"}


def provider_for_backend(backend: str) -> str:
    """OpenInference's provider value where one exists, so Phoenix's price table matches;
    otherwise the backend id (groq and ollama are already OpenInference values)."""
    return _PROVIDERS.get(backend, backend)


def _json(value: object) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _start(name: str, kind: str, attributes: Mapping[str, object], *,
           context: Any = None, links: Any = None) -> Any:
    tracer = get_tracer()
    if tracer is None:
        return None
    try:
        span = tracer.start_span(name, context=context, links=links)
        span.set_attribute(SPAN_KIND, kind)
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)  # type: ignore[arg-type]
        return span
    except Exception:  # noqa: BLE001 — tracing never breaks a turn
        logger.debug("[telemetry] could not start span %s", name, exc_info=True)
        return None


def _set(span: Any, key: str, value: object) -> None:
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not set %s", key, exc_info=True)


def _mark_error(span: Any, exc: BaseException) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode
        if isinstance(exc, asyncio.CancelledError):
            span.set_status(Status(StatusCode.ERROR, "cancelled"))
            return
        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
        if capture_content():
            span.record_exception(exc)
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not mark span failed", exc_info=True)


def _mark_failed(span: Any, description: str) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode
        span.set_status(Status(StatusCode.ERROR, description))
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not mark span failed", exc_info=True)


def _end(span: Any) -> None:
    try:
        span.end()
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not end span", exc_info=True)


@contextmanager
def _current(span: Any) -> Iterator[None]:
    """Make `span` the parent of spans opened inside the block."""
    try:
        from opentelemetry import trace
        manager = trace.use_span(span, end_on_exit=False, record_exception=False,
                                 set_status_on_exception=False)
        manager.__enter__()
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not activate span", exc_info=True)
        yield
        return
    try:
        yield
    finally:
        manager.__exit__(None, None, None)


# ---- model calls --------------------------------------------------------------------------

class LlmSpan:
    def __init__(self, span: Any) -> None:
        self._span = span
        self.prompt = 0
        self.completion = 0
        self.cached = 0

    def set_queue_wait(self, ms: int) -> None:
        _set(self._span, "crucible.queue_wait_ms", ms)

    def set_output(self, value: object) -> None:
        if capture_content():
            _set(self._span, OUTPUT_VALUE, cap_text(value if isinstance(value, str)
                                                    else _json(value)))

    def _write_tokens(self) -> None:
        _set(self._span, LLM_PROMPT, self.prompt)
        _set(self._span, LLM_COMPLETION, self.completion)
        _set(self._span, LLM_TOTAL, self.prompt + self.completion)
        if self.cached:
            _set(self._span, LLM_CACHE_READ, self.cached)


_CURRENT_LLM: ContextVar[LlmSpan | None] = ContextVar("crucible_current_llm", default=None)


@contextmanager
def llm_span(*, backend: str, model: str, schema_name: str | None, system: str | None,
             payload: object) -> Iterator[LlmSpan | None]:
    attributes: dict[str, object] = {
        LLM_MODEL: model, LLM_PROVIDER: provider_for_backend(backend) if backend else None,
        "crucible.call_purpose": CALL_PURPOSE.get(), "crucible.schema": schema_name,
        "crucible.billing": "plan" if backend in PLAN_BILLED_BACKENDS else None,
    }
    if capture_content():
        # The payload first: its tail (the current step) matters more than the system prompt.
        attributes[INPUT_VALUE] = cap_text(_json({"payload": payload, "system": system}))
    span = _start(f"llm {schema_name or 'text'}", "LLM", attributes)
    if span is None:
        yield None
        return
    state = LlmSpan(span)
    token = _CURRENT_LLM.set(state)
    try:
        with _current(span):
            yield state
    except BaseException as exc:
        _mark_error(span, exc)
        raise
    finally:
        _CURRENT_LLM.reset(token)
        state._write_tokens()
        _end(span)


def note_usage(*, prompt: int = 0, completion: int = 0, cached: int = 0) -> None:
    """Add the provider's counts to the model call in progress (called by UsageMeter.record,
    so the meter and the span can never disagree)."""
    state = _CURRENT_LLM.get()
    if state is None:
        return
    state.prompt += prompt
    state.completion += completion
    state.cached += cached
