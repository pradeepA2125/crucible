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
from typing import Any, Literal

from agentd.telemetry.context import (
    CALL_PURPOSE,
    EVAL_TAGS,
    TurnKind,
    last_user_turn_link,
    remember_turn,
    turn_link,
)
from agentd.telemetry.redact import cap_text, redact_args
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


# ---- turns ----------------------------------------------------------------------------------

def _hex_ids(span: Any) -> tuple[str, str]:
    context = span.get_span_context()
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def _links(contexts: list[object | None]) -> list[Any] | None:
    found = [c for c in contexts if c is not None]
    if not found:
        return None
    from opentelemetry.trace import Link
    return [Link(c) for c in found]  # type: ignore[arg-type]


def _new_root() -> Any:
    """An empty context: a turn or an agent run is never the child of whatever task happened
    to start it (an ended turn's span would otherwise become its parent)."""
    from opentelemetry.context import Context
    return Context()


class TurnSpan:
    def __init__(self, span: Any) -> None:
        self._span = span
        self.ids: tuple[str, str] = _hex_ids(span)

    def set_outcome(self, kind: str, text: str, *, failed: bool = False) -> None:
        _set(self._span, "crucible.outcome_kind", kind)
        if capture_content():
            _set(self._span, OUTPUT_VALUE, cap_text(text))
        if failed:
            _mark_failed(self._span, "turn ended with a failure notice")


@contextmanager
def turn_span(*, thread_id: str, turn_id: str, request_id: str | None, turn_kind: TurnKind,
              goal: str) -> Iterator[TurnSpan | None]:
    if get_tracer() is None:
        yield None
        return
    attributes: dict[str, object] = {
        SESSION_ID: thread_id, "crucible.turn_id": turn_id or None,
        "crucible.request_id": request_id, "crucible.turn_kind": turn_kind,
        **EVAL_TAGS.get()}
    if capture_content():
        attributes[INPUT_VALUE] = cap_text(goal)
    try:
        links = _links([last_user_turn_link(thread_id)]) if turn_kind == "continuation" else None
        span = _start("chat.turn", "AGENT", attributes, context=_new_root(), links=links)
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not start turn span", exc_info=True)
        span = None
    if span is None:
        yield None
        return
    traced = TurnSpan(span)
    remember_turn(turn_id, span.get_span_context(), thread_id=thread_id,
                  is_user_turn=turn_kind == "user")
    try:
        with _current(span):
            yield traced
    except BaseException as exc:
        _mark_error(span, exc)
        raise
    finally:
        _end(span)


# ---- tools ----------------------------------------------------------------------------------

class ToolSpan:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set_result(self, output: str, is_error: bool) -> None:
        if capture_content():
            _set(self._span, OUTPUT_VALUE, cap_text(output))
        if is_error:
            _mark_failed(self._span, "tool returned an error")


@contextmanager
def tool_span(name: str, args: Mapping[str, object]) -> Iterator[ToolSpan | None]:
    if get_tracer() is None:
        yield None
        return
    attributes: dict[str, object] = {TOOL_NAME: name}
    if capture_content():
        attributes[TOOL_PARAMETERS] = cap_text(_json(redact_args(args)))
    span = _start(name, "TOOL", attributes)
    if span is None:
        yield None
        return
    try:
        with _current(span):
            yield ToolSpan(span)
    except BaseException as exc:
        _mark_error(span, exc)
        raise
    finally:
        _end(span)


# ---- sub-agents and team members ----------------------------------------------------------

@contextmanager
def agent_span(*, thread_id: str, agent_id: str, label: str,
               kind: Literal["subagent", "team_member"], team_name: str | None,
               link_turn_id: str | None, request_id: str | None) -> Iterator[None]:
    """One agent activation as the root of its own trace, linked to the turn that started it:
    agents outlive turns, so they are never a turn span's children (spec §6.1)."""
    if get_tracer() is None:
        yield
        return
    name = f"team:{team_name}/{label}" if kind == "team_member" else f"subagent:{label}"
    attributes: dict[str, object] = {
        SESSION_ID: thread_id, "crucible.agent_id": agent_id, "crucible.agent_label": label,
        "crucible.agent_kind": kind, "crucible.team": team_name,
        "crucible.request_id": request_id, **EVAL_TAGS.get()}
    try:
        links = _links([turn_link(link_turn_id)]) if link_turn_id else None
        span = _start(name, "AGENT", attributes, context=_new_root(), links=links)
    except Exception:  # noqa: BLE001
        logger.debug("[telemetry] could not start agent span", exc_info=True)
        span = None
    if span is None:
        yield
        return
    try:
        with _current(span):
            yield
    except BaseException as exc:
        _mark_error(span, exc)
        raise
    finally:
        _end(span)
