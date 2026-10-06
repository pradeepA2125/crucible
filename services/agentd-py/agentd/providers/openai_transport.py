"""OpenAI Responses API transport, for an API key or a ChatGPT plan sign-in.

Every request streams with `store: false` and an `input` array. That is what the
ChatGPT plan route requires, and it is equally valid with an API key, so both
credential kinds share one request builder; `plan_route` only strips the fields the
plan route rejects. Success means a `response.completed` event — a stream that ends
without one is a transient failure, and `response.failed` / `response.incomplete` are
errors (https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

Schemas go through the strict-schema codec (`openai_strict_schema`), so the engine
sends and receives the same shapes it does on every other provider.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport
from agentd.providers.openai_compatible_transport import (
    TransientTransportError,
    _classify_retry_reason,
    _jittered,
    _within_deadline,
)
from agentd.providers.openai_strict_schema import encode_strict_schema
from agentd.providers.plan_access import (
    TRANSIENT_PLAN_CODES,
    PlanNotEligible,
    PlanSessionInvalid,
    PlanUnsupportedCapability,
    classify_plan_error,
)
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
from agentd.providers.token_progress import ProgressTicker, approx_prompt_tokens, int_or_none

logger = logging.getLogger(__name__)

# The only request fields sent on the plan route. Everything on the preview's
# unsupported list (temperature, max_output_tokens, metadata, truncation, user,
# previous_response_id, …) is absent by construction; this set makes that checkable.
PLAN_ROUTE_FIELDS = frozenset({
    "model", "instructions", "input", "stream", "store", "text", "reasoning", "include",
})
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_THINKING_EVENTS = frozenset({
    "response.reasoning_summary_text.delta", "response.reasoning_text.delta",
})
_SCHEMA_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
# Our rungs to the Responses API's values. OFF is "none": the live plan route lists
# none/low/medium/high/xhigh/max for gpt-5.6 models.
_EFFORT_WIRE: dict[ReasoningEffort, str] = {
    ReasoningEffort.OFF: "none", ReasoningEffort.LOW: "low",
    ReasoningEffort.MEDIUM: "medium", ReasoningEffort.HIGH: "high", ReasoningEffort.MAX: "max",
}


class BearerSource(Protocol):
    async def bearer(self) -> str: ...

    async def on_unauthorized(self) -> bool:
        """After a 401: get a fresh credential. True means retrying is worthwhile."""
        ...


class StaticBearer:
    """An API key. Nothing to refresh."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def bearer(self) -> str:
        return self._api_key

    async def on_unauthorized(self) -> bool:
        return False


class _Unauthorized(Exception):
    def __init__(self, cause: openai.APIStatusError) -> None:
        super().__init__(str(cause))
        self.cause = cause


class OpenAIJsonTransport(ModelJsonTransport):
    supports_anyof_grammar: bool = True
    supports_token_progress: bool = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        bearer: BearerSource | None = None,
        plan_route: bool = False,
        max_output_tokens: int | None = None,
        timeout_sec: float = 120.0,
        stream_timeout_sec: float = 600.0,
        max_retries: int = 4,
        retry_base_delay: float = 5.0,
        responses_client: Any | None = None,
    ) -> None:
        self._plan_route = plan_route
        self._max_output_tokens = max_output_tokens
        self._timeout_sec = timeout_sec
        self._stream_timeout_sec = stream_timeout_sec
        self._max_retries = max(0, max_retries)
        self._retry_base_delay = retry_base_delay
        self._fixed_responses = responses_client
        self._effort: ReasoningEffort | None = None
        self._effort_rejected: dict[ReasoningEffort, str] = {}
        self._client_token: str | None = None
        self._client_responses: Any = None
        if bearer is not None:
            self._bearer: BearerSource = bearer
        else:
            resolved = api_key or os.getenv("OPENAI_API_KEY")
            if not resolved and responses_client is None:
                msg = "OPENAI_API_KEY is required for OpenAIJsonTransport"
                raise RuntimeError(msg)
            self._bearer = StaticBearer(resolved or "")

    # ------------------------------------------------------------ reasoning effort

    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        """The effective rung (already clamped by the runtime). None sends no field."""
        self._effort = level

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        """Every rung UNKNOWN until the endpoint proves otherwise: which rungs a model
        takes varies per model (live: gpt-5.6 rejects 'minimal' but takes
        none…max), so only a probative rejection marks one unsupported."""
        return EffortSupport(unsupported=dict(self._effort_rejected))

    def _note_effort_rejection(self, exc: Exception, body: dict[str, Any]) -> bool:
        """A 400 naming `reasoning.effort` proves THIS rung is unsupported: mark it,
        drop the field for this process, and let the caller retry once without it."""
        if self._effort is None or "reasoning" not in body:
            return False
        if getattr(exc, "status_code", None) != 400:
            return False
        if getattr(exc, "param", None) != "reasoning.effort":
            return False
        self._effort_rejected[self._effort] = f"this model rejected '{self._effort}'"
        logger.warning("[effort] OpenAI rejected reasoning effort %s; dropping it", self._effort)
        self._effort = None
        body.pop("reasoning", None)
        return True

    # ------------------------------------------------------------ public API

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
        on_progress: Callable[..., None] | None = None,
        on_usage: Callable[[int, int], None] | None = None,
    ) -> dict[str, object]:
        codec = encode_strict_schema(schema)
        body = self._body(model, system_instructions, user_payload)
        body["text"] = {"format": {
            "type": "json_schema",
            "name": _SCHEMA_NAME_RE.sub("_", schema_name)[:64],
            "schema": codec.schema,
            "strict": True,
        }}
        text = await self._run(body, on_thinking, on_retry, on_progress, on_usage,
                               first_action=True)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"OpenAI output is not valid JSON: {text[:500]}"
            raise RuntimeError(msg) from exc
        if not isinstance(payload, dict):
            msg = "OpenAI output must be a JSON object"
            raise RuntimeError(msg)
        return codec.decode(payload)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> str:
        body = self._body(model, system_instructions, user_payload)
        return (await self._run(body, on_thinking, None, None, None)).strip()

    # ------------------------------------------------------------ request

    def _body(
        self, model: str, system_instructions: str, user_payload: dict[str, object]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "instructions": system_instructions,
            "input": [{"role": "user", "content": json.dumps(user_payload)}],
            "stream": True,
            "store": False,
        }
        if self._max_output_tokens is not None and not self._plan_route:
            body["max_output_tokens"] = self._max_output_tokens
        if self._effort is not None:
            body["reasoning"] = {"effort": _EFFORT_WIRE[self._effort]}
        return body

    async def _responses(self) -> Any:
        if self._fixed_responses is not None:
            return self._fixed_responses
        token = await self._bearer.bearer()
        if token != self._client_token:
            # The SDK's own retries are off: one retry policy (ours) decides what a
            # failure means, including the plan errors that must never be retried.
            client = AsyncOpenAI(api_key=token, max_retries=0, timeout=self._timeout_sec)
            self._client_token, self._client_responses = token, client.responses
        return self._client_responses

    async def _run(
        self,
        body: dict[str, Any],
        on_thinking: Callable[[str], None] | None,
        on_retry: Callable[[int, int, str, str], None] | None,
        on_progress: Callable[..., None] | None,
        on_usage: Callable[[int, int], None] | None,
        *,
        first_action: bool = False,
    ) -> str:
        if self._plan_route:
            extra = set(body) - PLAN_ROUTE_FIELDS
            if extra:
                msg = f"plan route request carries unsupported fields: {sorted(extra)}"
                raise ValueError(msg)
        # A 401 gets exactly one refresh-and-retry, outside the transient retry budget.
        refreshed = False
        attempt = 0
        while True:
            try:
                return await self._stream_once(body, on_thinking, on_progress, on_usage,
                                               first_action=first_action)
            except _Unauthorized as exc:
                if not refreshed and await self._bearer.on_unauthorized():
                    refreshed = True
                    continue
                raise self._unauthorized_error(exc.cause) from exc.cause
            except Exception as exc:
                if self._note_effort_rejection(exc, body):
                    continue
                if not _is_transient(exc) or attempt >= self._max_retries:
                    raise
                attempt += 1
                await self._backoff(attempt, exc, on_retry)

    async def _backoff(
        self, attempt: int, exc: Exception,
        on_retry: Callable[[int, int, str, str], None] | None,
    ) -> None:
        delay = _jittered(min(self._retry_base_delay * (2 ** (attempt - 1)), 60.0))
        logger.warning("OpenAI transient error (attempt %d/%d), retrying in %.0fs: %s",
                       attempt, self._max_retries, delay, exc.__class__.__name__)
        if callable(on_retry):
            on_retry(attempt, self._max_retries, _classify_retry_reason(exc),
                     f"⏳ {exc.__class__.__name__} — retrying in {delay:.0f}s "
                     f"(attempt {attempt}/{self._max_retries})…")
        await asyncio.sleep(delay)

    async def _stream_once(
        self,
        body: dict[str, Any],
        on_thinking: Callable[[str], None] | None,
        on_progress: Callable[..., None] | None,
        on_usage: Callable[[int, int], None] | None,
        *,
        first_action: bool = False,
    ) -> str:
        ticker = ProgressTicker(on_progress, input_n=approx_prompt_tokens(
            body["instructions"], body["input"][0]["content"]))
        ticker.start()
        responses = await self._responses()
        try:
            stream = await asyncio.wait_for(responses.create(**body), timeout=self._timeout_sec)
        except TimeoutError as exc:
            msg = f"OpenAI responses.create timed out after {self._timeout_sec}s"
            raise TransientTransportError(msg) from exc
        except openai.APIStatusError as exc:
            raise self._status_error(exc) from exc

        texts: dict[str | None, list[str]] = {}  # per output item: never join items
        phases: dict[str | None, str | None] = {}
        completed: Any = None
        try:
            async for event in _within_deadline(stream, self._stream_timeout_sec):
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    texts.setdefault(getattr(event, "item_id", None), []).append(event.delta)
                    ticker.output(event.delta)
                elif kind == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "message":
                        item_id = getattr(item, "id", None)
                        phases[item_id] = getattr(item, "phase", None)
                        action = "".join(texts.get(item_id, []))
                        if first_action and _is_json_object(action):
                            # The one action we asked for. What follows is the model
                            # acting out results it never received; stop paying for it.
                            await _close(stream)
                            ticker.finish()
                            return action
                elif kind in _THINKING_EVENTS:
                    if callable(on_thinking):
                        on_thinking(event.delta)
                    ticker.thinking(event.delta)
                elif kind == "response.completed":
                    completed = event.response
                    break
                elif kind == "response.failed":
                    raise _failed_error(getattr(event.response, "error", None))
                elif kind == "response.incomplete":
                    details = getattr(event.response, "incomplete_details", None)
                    reason = getattr(details, "reason", None) or "unknown"
                    msg = f"OpenAI response incomplete ({reason}); output was cut off"
                    raise RuntimeError(msg)
                elif kind == "error":
                    # The plan route nests the details: {"type": "error", "error": {…}}.
                    raise _failed_error(getattr(event, "error", None) or event)
        except openai.APIStatusError as exc:
            raise self._status_error(exc) from exc
        except openai.APIError as exc:
            # An SSE `error` event inside an HTTP 200 stream: the SDK raises a plain
            # APIError carrying the event's code. Measured live: this is how the plan
            # route reports its usage limit, not with the documented 429.
            if isinstance(exc, openai.APIConnectionError):
                raise
            raise _failed_error(exc) from exc
        if completed is None:
            msg = "OpenAI stream ended before response.completed"
            raise TransientTransportError(msg)

        usage = getattr(completed, "usage", None)
        input_tokens = int_or_none(getattr(usage, "input_tokens", None))
        output_tokens = int_or_none(getattr(usage, "output_tokens", None))
        reasoning_tokens = int_or_none(getattr(
            getattr(usage, "output_tokens_details", None), "reasoning_tokens", None))
        ticker.finish(thinking_tokens=reasoning_tokens, output_tokens=output_tokens,
                      input_tokens=input_tokens)
        if on_usage is not None and input_tokens is not None and output_tokens is not None:
            on_usage(input_tokens, output_tokens)

        text = _answer_text(texts, phases)
        if not text.strip():
            msg = "OpenAI response contained no output_text"
            raise RuntimeError(msg)
        return text

    # ------------------------------------------------------------ errors

    def _status_error(self, exc: openai.APIStatusError) -> Exception:
        body = exc.body if isinstance(exc.body, dict) else {}
        code = getattr(exc, "code", None) or body.get("code")
        message = str(body.get("message") or body.get("detail") or exc.message)
        stopped = classify_plan_error(code, message, status=exc.status_code,
                                      request_id=exc.request_id,
                                      param=getattr(exc, "param", None) or body.get("param"))
        if stopped is not None:
            return stopped
        if code in TRANSIENT_PLAN_CODES:
            return TransientTransportError(f"{code}: {message}")
        if exc.status_code == 401:
            return _Unauthorized(exc)
        if self._plan_route and exc.status_code == 400 and code is None and "detail" in body:
            # A direct-route admission refusal ({"detail": …}), e.g. a model this account
            # can't use. Input-determined: a retry or a correction message can't fix it.
            return PlanUnsupportedCapability(message, status=400, request_id=exc.request_id)
        if self._plan_route and exc.status_code == 403:
            # A direct-route admission refusal ({"detail": …}): policy or region.
            return PlanNotEligible(message, status=403, request_id=exc.request_id)
        return exc

    def _unauthorized_error(self, exc: openai.APIStatusError) -> Exception:
        if not self._plan_route:
            return exc
        body = exc.body if isinstance(exc.body, dict) else {}
        message = str(body.get("message") or body.get("detail") or exc.message)
        return PlanSessionInvalid(message, status=401, request_id=exc.request_id,
                                  code=getattr(exc, "code", None))


def _answer_text(texts: dict[str | None, list[str]], phases: dict[str | None, str | None]) -> str:
    """Plain-text reply: the `final_answer` message, else the last message.

    A response can hold several message items (`commentary` ones, then `final_answer`)
    and `response.completed` carries no output to read instead, so deltas are never
    joined across items. Structured output doesn't get here: it takes the FIRST
    complete action and stops (see `_stream_once`).
    """
    if not texts:
        return ""
    finals = [item for item, phase in phases.items() if phase == "final_answer" and item in texts]
    chosen = finals[-1] if finals else list(texts)[-1]
    return "".join(texts[chosen])


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except json.JSONDecodeError:
        return False


async def _close(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            await close()
        except Exception:  # noqa: BLE001 — abandoning the stream anyway
            logger.debug("closing an abandoned response stream failed", exc_info=True)


def _failed_error(error: Any) -> Exception:
    code = getattr(error, "code", None)
    message = str(getattr(error, "message", None) or code or "response failed")
    stopped = classify_plan_error(code, message, param=getattr(error, "param", None))
    if stopped is not None:
        return stopped
    if code in TRANSIENT_PLAN_CODES or code in ("server_error", "rate_limit_exceeded"):
        return TransientTransportError(f"{code}: {message}")
    return RuntimeError(f"OpenAI response failed ({code}): {message}")


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, TransientTransportError | TimeoutError | openai.APIConnectionError):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in _RETRYABLE_STATUS
