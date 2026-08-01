from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import APIConnectionError, AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport, narrow_schema_for_type
from agentd.runtime.artifacts import provider_debug_root

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503})


def _is_reasoning_model(model: str) -> bool:
    """Name-substring FALLBACK, used only when a live capability registry
    (e.g. OpenRouter's _ModelCapabilityCache) is unavailable or doesn't recognize
    the model — see its docstring for why that's the primary path now. Covers
    DeepSeek-R1 family, Qwen3 family (including qwen3-coder), and Nemotron.
    openrouter/free is intentionally excluded — it routes to whatever is available
    and reasoning params cause it to return empty choices.
    """
    m = model.lower()
    return any(x in m for x in ("deepseek-r1", "deepseek-r2", "qwen3", "nemotron"))


class NonProbativeError(RuntimeError):
    """Marker: this failure says NOTHING about whether the endpoint can honor
    `response_format`, so it must never trip the sticky json_object downgrade.

    Subclasses RuntimeError so every existing `except RuntimeError` and message
    assertion keeps working; the type exists purely to carry that one bit to
    `_proves_json_schema_unsupported`.
    """


class TransientTransportError(NonProbativeError):
    """Infrastructure failure: a timeout, or a stream that broke part-way through.
    Says nothing about the request's own shape."""


class EmptyResponseError(NonProbativeError):
    """The response carried no usable text at all (no choices, or empty content).

    No output is no evidence. This is a real, documented condition here that has
    nothing to do with schema support: `openrouter/free` returns empty choices as
    a routing artifact (see _is_reasoning_model), and qwen3-family models can burn
    the whole output budget on implicit thinking and emit nothing.
    """


def _is_retryable(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES


def _proves_json_schema_unsupported(
    exc: Exception, *, finish_reason: str | None = None
) -> bool:
    """Does this strict-call failure actually prove the endpoint can't do
    json_schema? Only then may the process-wide downgrade fire.

    `finish_reason` is the one from the response that produced `exc`, when there
    was one (None if the call never got that far).

    Deliberately a DENYLIST of non-probative failures rather than an allowlist of
    recognized "unsupported" errors, because the two mistakes are not symmetric:
      - a false negative (we fail to downgrade) costs one wasted strict attempt
        per call — exactly today's behavior, i.e. harmless;
      - a false positive (a blip downgrades permanently) silently degrades every
        structured call for the rest of the process, which is the bug this whole
        feature is supposed to be worth introducing.
    An unrecognized failure therefore keeps the downgrade, while anything we can
    positively identify as non-probative does not.
    """
    # Truncation at max_completion_tokens: a PERFECTLY enforced grammar still
    # yields unparseable JSON when the response is cut off mid-object. One
    # oversized file edit must not cost the session its schema enforcement.
    if finish_reason == "length":
        return False
    if isinstance(exc, NonProbativeError):
        return False
    # Rate limiting and every server-side/gateway fault (500/502/503/504 …), even
    # with retries already exhausted: a load/availability signal, never a statement
    # about response_format. Deliberately WIDER than _RETRYABLE_STATUS_CODES, which
    # governs whether to retry — a 502 is not worth retrying here but is equally
    # worthless as evidence. 501 Not Implemented is swept up too; that costs one
    # wasted strict attempt per call, the harmless direction.
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code >= 500 or status_code in (408, 429)):
        return False
    # DNS/TCP/TLS failures, and the SDK's own APITimeoutError (a subclass).
    if isinstance(exc, APIConnectionError):
        return False
    # TimeoutError is an OSError subclass on 3.11+; ConnectionError likewise.
    return not isinstance(exc, OSError)


def _response_finish_reason(response: Any) -> str | None:
    """`choices[0].finish_reason` if present. Defensive: any endpoint that omits
    it just yields None, which the downgrade check treats as "not truncated"."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        return None
    reason = getattr(choices[0], "finish_reason", None)
    return reason if isinstance(reason, str) else None


def _classify_retry_reason(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limited"
    if isinstance(status_code, int):
        return "server_error"
    return "network_error"


class OpenAICompatibleTransport(ModelJsonTransport):
    """Generic OpenAI `/chat/completions` client against a configurable base URL.

    Vendor-neutral: subclasses add their own headers, extra_body, and reasoning
    detection through the three hooks below. Used directly by the
    `openai_compatible` backend and subclassed by OpenRouter.
    """

    supports_anyof_grammar: bool = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str,
        vendor: str = "openai_compatible",
        label: str = "OpenAI-compatible",
        max_tokens: int = 4096,
        json_max_tokens: int = 16384,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        supports_oneof: bool = False,
        default_headers: dict[str, str] | None = None,
        completions_client: Any | None = None,
    ) -> None:
        self._vendor = vendor
        self._label = label
        # max_tokens (generate_text): small, deliberately anti-runaway. json_max_tokens
        # (generate_json/controller_step_response): a JSON payload carrying a full
        # file's content (create_file/search_replace) plus schema/escaping overhead
        # routinely exceeds 4096 tokens — confirmed live on the Ollama transport,
        # where the equivalent under-provisioned budget silently truncated real
        # file writes into invalid JSON (Finding #11). Split like Ollama's
        # json_num_predict vs its fixed small text num_predict, same reasoning.
        self._max_tokens = max_tokens
        self._json_max_tokens = json_max_tokens
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        # Instance attribute, NOT a class attribute: OpenRouter must keep the
        # contracts default (False) while openai_compatible opts in to True.
        self.supports_oneof_grammar = supports_oneof
        # "strict" | "json_object". Every generate_json starts by probing strict
        # json_schema; the first failure that PROVES the endpoint can't honor it
        # flips this for the rest of the process (see _downgrade_json_mode).
        self._json_mode: str = "strict"

        if completions_client is not None:
            self._completions: Any = completions_client
            return

        client_kwargs: dict[str, Any] = {
            "base_url": base_url,
            "timeout": timeout_sec,
        }
        # Local endpoints (vLLM, LM Studio) legitimately have no key. The OpenAI
        # SDK requires *some* api_key value, so send a placeholder rather than
        # refusing to construct — the header is meaningless to a keyless server.
        client_kwargs["api_key"] = api_key or "not-required"
        headers = default_headers or self._default_headers()
        if headers:
            client_kwargs["default_headers"] = headers

        client = AsyncOpenAI(**client_kwargs)
        self._completions = client.chat.completions

    # ---------------------------------------------------------------- hooks

    def _default_headers(self) -> dict[str, str] | None:
        """Vendor-specific headers. Base sends none."""
        return None

    def _build_extra_body(
        self, model: str, is_reasoning: bool, *, for_json: bool
    ) -> dict[str, Any]:
        """Vendor-specific request body extras. Base sends only reasoning.

        `for_json` distinguishes a structured-output call (json_schema or the
        json_object fallback) from a plain text completion. The base ignores it —
        it has no JSON-only extras — but it is the seam a vendor uses to attach
        something that is only meaningful when a `response_format` is in play.
        OpenRouter's `provider.require_parameters` is exactly that: it guards
        response_format routing, so sending it on a text call would restrict
        routing for no benefit.
        """
        extra_body: dict[str, Any] = {}
        if is_reasoning:
            extra_body["reasoning"] = {"enabled": True}
        return extra_body

    async def _reasoning_config(self, model: str) -> tuple[bool, float]:
        """(is_reasoning, temperature). Base uses the name-substring heuristic."""
        is_reasoning = _is_reasoning_model(model)
        return is_reasoning, (1.0 if is_reasoning else 0.0)

    async def aclose(self) -> None:
        """Subclasses with owned resources override this."""
        return None

    # ------------------------------------------------------------- requests

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        result = await self._generate_json_once(
            model=model,
            schema_name=schema_name,
            schema=schema,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
            on_retry=on_retry,
        )
        # Type-specific narrowing: the tight anyOf schema enforces each variant's
        # required fields at the token level, but if the grammar was ignored (an
        # underlying provider that silently dropped response_format, or the json_object
        # fallback fired), the model can still return a valid `type` with its action
        # fields missing. Narrow `required` to just that type's fields and retry once.
        if schema_name == "controller_step_response":
            narrowed = narrow_schema_for_type(schema, result)
            if narrowed is not None:
                logger.warning(
                    "%s: %s returned type=%r but missing action fields — "
                    "retrying with narrowed schema",
                    self._vendor, schema_name, result.get("type"),
                )
                result = await self._generate_json_once(
                    model=model,
                    schema_name=schema_name,
                    schema=narrowed,
                    system_instructions=system_instructions,
                    user_payload=user_payload,
                    on_thinking=on_thinking,
                    on_retry=on_retry,
                )
        return result

    async def _get_completion_output(
        self, create_kwargs: dict[str, Any], on_thinking: Any, on_retry: Any = None,
    ) -> tuple[str, str | None]:
        """Route through the streaming path (forwarding reasoning deltas to
        on_thinking live, as they arrive) when a callback is given, else the plain
        non-streaming call. Previously on_thinking was accepted by generate_json
        but silently never used — every controller_step_response call (the one
        driving every turn of a live-driven session) rendered nothing until the
        whole call completed, identical to the gap fixed on the Ollama transport.

        Returns (text, finish_reason). The finish_reason is what lets the caller
        tell a truncated response apart from a genuinely malformed one.
        """
        if callable(on_thinking):
            return await self._stream_with_finish_reason(
                create_kwargs, on_thinking=on_thinking, on_retry=on_retry
            )
        response = await self._call_with_retry(create_kwargs, on_retry=on_retry)
        return self._extract_text(response), _response_finish_reason(response)

    async def _get_completion_text(
        self, create_kwargs: dict[str, Any], on_thinking: Any, on_retry: Any = None,
    ) -> str:
        """Text-only view of _get_completion_output, for callers that have no use
        for the finish_reason (the json_object fallback — it never decides a
        downgrade, so truncation there is just a malformed-JSON retry)."""
        text, _finish_reason = await self._get_completion_output(
            create_kwargs, on_thinking, on_retry
        )
        return text

    async def _generate_json_once(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        safe_schema_name = "".join(c for c in schema_name if c.isalnum())

        # Reasoning models require temperature=1 per OpenRouter docs (confirmed
        # live via the model's own default_parameters where the registry knows it).
        is_reasoning, temperature = await self._reasoning_config(model)

        extra_body = self._build_extra_body(model, is_reasoning, for_json=True)

        base_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            # A JSON payload carrying a full file's content (create_file/
            # search_replace) plus schema/escaping overhead routinely exceeds a
            # few thousand tokens — json_max_tokens (default 16384) is deliberately
            # much larger than generate_text's anti-runaway self._max_tokens.
            "max_completion_tokens": self._json_max_tokens,
            "temperature": temperature,
            "extra_body": extra_body,
        }

        if self._json_mode == "strict":
            create_kwargs = {
                **base_kwargs,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": safe_schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            self._dump_debug_request(create_kwargs, safe_schema_name)
            # Bound before the try so the except can still read it when the parse
            # (not the request) is what failed; stays None if we never got a response.
            finish_reason: str | None = None
            try:
                output_text, finish_reason = await self._get_completion_output(
                    create_kwargs, on_thinking, on_retry
                )
                return self._parse_output_object(output_text, schema_name)
            except Exception as e:
                # Fall back to json_object with schema injected into system prompt.
                # Some models/providers don't support json_schema strict mode.
                # The fallback itself is unconditional (unchanged behavior); only
                # the PERMANENT downgrade needs the failure to be probative.
                if _proves_json_schema_unsupported(e, finish_reason=finish_reason):
                    logger.warning(
                        "%s: strict json_schema failed for %s — downgrading to "
                        "json_object for the rest of this process: %s",
                        self._label, schema_name, e,
                    )
                    self._downgrade_json_mode()
                else:
                    logger.warning(
                        "%s json_schema call failed transiently for %s, falling back "
                        "to json_object for this call only: %s",
                        self._label, schema_name, e,
                    )

        return await self._json_object_fallback(
            base_kwargs=base_kwargs,
            model=model,
            is_reasoning=is_reasoning,
            schema=schema,
            schema_name=schema_name,
            safe_schema_name=safe_schema_name,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
            on_retry=on_retry,
        )

    def _downgrade_json_mode(self) -> None:
        """The single place `_json_mode` is ever written after construction.

        Permanent for this process. A restart re-probes, so an endpoint that gains
        strict support recovers with no cache to invalidate.
        """
        self._json_mode = "json_object"
        # An endpoint that cannot honor response_format cannot be trusted with a
        # tight union schema either — stop offering one. Assigning on the instance
        # shadows the class-level supports_anyof_grammar; that is per-instance by
        # design, so one degraded endpoint never mutates another transport.
        self.supports_oneof_grammar = False
        self.supports_anyof_grammar = False

    def _dump_debug_request(self, create_kwargs: dict[str, Any], name: str) -> None:
        """Best-effort artifact of the exact request bytes. Never raises: a debug
        dump must not be able to fail a live call."""
        out_dir = provider_debug_root(self._vendor)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"debug-req-{name}.json").write_text(
                json.dumps(create_kwargs, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

    async def _json_object_fallback(
        self,
        *,
        base_kwargs: dict[str, Any],
        model: str,
        is_reasoning: bool,
        schema: dict[str, object],
        schema_name: str,
        safe_schema_name: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        """json_object mode with the schema injected into the system prompt.

        Reached either after a failed strict attempt, or directly once the process
        has downgraded. Behaviorally identical to the inline version it replaced.
        """
        # The fallback must be permissive: it has to be able to route to ANY
        # provider, because the strict path already failed precisely because no
        # provider honored response_format. Any routing guard a vendor pins for
        # the strict call would be inherited here and 404 too — defeating the
        # fallback's whole purpose. So rather than filtering keys out, rebuild
        # the extras from scratch with for_json=False, which asks the vendor for
        # its unguarded set. Non-routing extras (e.g. reasoning) come back
        # unchanged; the base stays ignorant of any vendor's key names.
        fallback_extra_body = self._build_extra_body(model, is_reasoning, for_json=False)
        fallback_kwargs: dict[str, Any] = {
            **base_kwargs,
            "extra_body": fallback_extra_body,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system_instructions}\n\n"
                        f"You MUST return a JSON object matching this schema:\n"
                        f"{json.dumps(schema, indent=2)}"
                    ),
                },
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "response_format": {"type": "json_object"},
        }
        # Once downgraded, the strict request is never built, so without this the
        # provider debug artifact would freeze at the last strict attempt forever.
        if self._json_mode != "strict":
            self._dump_debug_request(fallback_kwargs, f"{safe_schema_name}-fallback")
        last_parse_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "%s malformed JSON for %s (attempt %d/%d), retrying in %.0fs",
                    self._label, schema_name, attempt, self._max_retries, delay,
                )
                # A malformed-JSON retry cycle can run for minutes with the UI
                # otherwise showing nothing — on_retry (structured, distinct
                # from on_thinking) lets the caller show a retry is happening.
                if callable(on_retry):
                    on_retry(
                        attempt, self._max_retries, "malformed_response",
                        f"⏳ Malformed JSON response — retrying in {delay:.0f}s "
                        f"(attempt {attempt}/{self._max_retries})…",
                    )
                await asyncio.sleep(delay)
            try:
                output_text = await self._get_completion_text(
                    fallback_kwargs, on_thinking, on_retry
                )
                return self._parse_output_object(output_text, schema_name)
            except RuntimeError as e2:
                if "not valid JSON" in str(e2) or "must be a JSON object" in str(e2):
                    last_parse_exc = e2
                    continue
                raise RuntimeError(
                    f"{self._label} API error for {schema_name} (fallback also failed): {e2}"
                ) from e2
            except Exception as e2:
                raise RuntimeError(
                    f"{self._label} API error for {schema_name} (fallback also failed): {e2}"
                ) from e2
        assert last_parse_exc is not None
        raise RuntimeError(
            f"{self._label} API error for {schema_name} "
            f"(fallback malformed JSON after retries): {last_parse_exc}"
        ) from last_parse_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
    ) -> str:
        is_reasoning, temperature = await self._reasoning_config(model)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "max_completion_tokens": self._max_tokens,
            "temperature": temperature,
        }
        extra_body = self._build_extra_body(model, is_reasoning, for_json=False)
        if extra_body:
            create_kwargs["extra_body"] = extra_body

        if callable(on_thinking):
            return await self._stream_with_thinking(create_kwargs, on_thinking=on_thinking)

        try:
            response = await self._call_with_retry(create_kwargs)
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"{self._label} API error: {e}") from e

    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
    ) -> str:
        """Text-only view of _stream_with_finish_reason (generate_text's entry
        point, and the long-standing public-ish shape of this method)."""
        text, _finish_reason = await self._stream_with_finish_reason(
            create_kwargs, on_thinking=on_thinking, on_retry=on_retry
        )
        return text

    async def _stream_with_finish_reason(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
    ) -> tuple[str, str | None]:
        """Stream response forwarding reasoning chunks to on_thinking callback.

        OpenAI-compatible endpoints surface reasoning in delta.reasoning
        (OpenRouter does — same field as Groq).

        Opening the stream and consuming it are deliberately separate try blocks:
        a failure to OPEN can be probative (a 400 rejecting response_format), while
        a failure part-way THROUGH never is.
        """
        kwargs = {**create_kwargs, "stream": True}
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "%s transient error (attempt %d/%d), retrying in %.0fs",
                    self._label, attempt, self._max_retries, delay,
                )
                if callable(on_retry):
                    reason = _classify_retry_reason(last_exc) if last_exc else "network_error"
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)
            try:
                stream = await asyncio.wait_for(
                    self._completions.create(**kwargs),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                # TransientTransportError subclasses RuntimeError: same type for
                # every existing handler, but distinguishable by the downgrade check.
                raise TransientTransportError(
                    f"{self._label} streaming timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                # The request was rejected before a single byte came back — that
                # CAN be a genuine "I don't support response_format". Leave it
                # unwrapped so the downgrade check can still see it.
                raise
            try:
                content_parts: list[str] = []
                finish_reason: str | None = None
                async for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    chunk_reason = getattr(choices[0], "finish_reason", None)
                    if isinstance(chunk_reason, str) and chunk_reason:
                        finish_reason = chunk_reason
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    reasoning = getattr(delta, "reasoning", None)
                    if reasoning:
                        on_thinking(reasoning)
                    content = getattr(delta, "content", None) or ""
                    if content:
                        content_parts.append(content)
                return "".join(content_parts).strip(), finish_reason
            except TimeoutError as exc:
                raise TransientTransportError(
                    f"{self._label} streaming timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                # The stream opened and then broke (httpx disconnect, SDK read
                # error). httpx exceptions derive from httpx.HTTPError — NOT
                # OSError — and carry no status_code, so without this wrap a mid-
                # response disconnect would look probative and downgrade for good.
                # This is the PRIMARY chat path: every controller turn passes
                # on_thinking, so every controller turn streams.
                raise TransientTransportError(
                    f"{self._label} stream failed mid-response: {exc}"
                ) from exc

        assert last_exc is not None
        raise last_exc

    async def _call_with_retry(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_retry: Any = None,
    ) -> Any:
        """Call chat.completions.create with timeout and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "%s transient error (attempt %d/%d), retrying in %.0fs",
                    self._label, attempt, self._max_retries, delay,
                )
                if callable(on_retry):
                    reason = _classify_retry_reason(last_exc) if last_exc else "network_error"
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)
            try:
                return await asyncio.wait_for(
                    self._completions.create(**create_kwargs),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                raise TransientTransportError(
                    f"{self._label} chat.completions timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    def _extract_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            try:
                raw = response.model_dump() if hasattr(response, "model_dump") else vars(response)
                logger.warning(
                    "%s response missing choices — full response: %s", self._label, raw
                )
            except Exception:
                logger.warning(
                    "%s response missing choices — response: %r", self._label, response
                )
            # EmptyResponseError, not a bare RuntimeError: no output is no evidence
            # about response_format support. Same message, same RuntimeError base.
            raise EmptyResponseError(f"{self._label} response missing choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise EmptyResponseError(f"{self._label} response contained no text output")
        return content.strip()

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = _strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{self._label} output is not valid JSON for {schema_name}: {output_text[:500]}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self._label} output must be a JSON object")
        return payload


def _strip_json_code_fences(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
