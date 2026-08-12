from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from openai import APIConnectionError, AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport, narrow_schema_for_type
from agentd.providers.reasoning_effort import LADDER, EffortSupport, ReasoningEffort
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


_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"

# vLLM (which NVIDIA NIM is built on) reads a top-level `reasoning_effort` and
# auto-injects the low-level chat_template_kwargs.enable_thinking from it, so this
# is the front door for the whole OpenAI-compatible family. The ladder tops out at
# "high" there, which is why MAX maps onto "high" and is declared unsupported —
# the runtime clamps MAX to HIGH before it reaches this map, and the map stays
# total so a missed clamp still sends something valid rather than raising.
_OPENAI_COMPAT_EFFORT_WIRE: dict[ReasoningEffort, str] = {
    ReasoningEffort.OFF: "none",
    ReasoningEffort.LOW: "low",
    ReasoningEffort.MEDIUM: "medium",
    ReasoningEffort.HIGH: "high",
    ReasoningEffort.MAX: "high",
}

# Statuses that prove nothing about capability. Mirrors _RETRYABLE_STATUS_CODES in
# spirit and for the same reason the sticky JSON downgrade excludes them: a
# rate-limit blip that permanently pinned the session to a lower rung would be the
# same silent-degradation bug wearing a different hat.
_NON_PROBATIVE_STATUS: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})


def _is_probative_effort_rejection(exc: Exception) -> bool:
    """True only when the endpoint PROVED it rejects the effort value we sent.

    Requires both a 4xx that is not in the transient set AND the parameter named
    in the message — a 400 about context length says nothing about effort.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or status in _NON_PROBATIVE_STATUS or status >= 500:
        return False
    text = str(exc).lower()
    return "reasoning_effort" in text or "reasoning effort" in text


# Minimum gap between on_progress emissions. Live-measured delta rate is ~29/sec;
# emitting per delta would put ~29 SSE frames/sec on the chat channel for a
# counter a human reads a few times a second. ~6/sec reads as continuous.
_PROGRESS_INTERVAL_SEC = 0.15

# Characters per token. A rough constant on purpose: the counts it feeds are a
# live progress readout, not billing, and a real tokenizer would mean loading a
# per-model vocabulary just to animate a number.
#
# What it replaces matters more than its accuracy. The counts used to be the
# number of stream DELTAS, which is not a property of the response at all — it
# is a property of how the server chose to chunk. A structured-output endpoint
# returns a whole JSON action in ONE delta, so every tool call reported "1"
# under a label reading "output tokens", while a long edit that happened to be
# chunked counted into the thousands. Same code, same call, 3-orders-of-
# magnitude different number.
_CHARS_PER_TOKEN = 4

# Usage-reporting rungs, richest first. `continuous_usage_stats` is a vLLM
# extension (NVIDIA NIM is built on vLLM): it puts a running completion_tokens on
# EVERY chunk instead of only the last, which is the difference between a counter
# that climbs with the model's own exact numbers and one that guesses until the
# call ends. Measured on NIM: 1968 of 1968 chunks carried usage.
_STREAM_USAGE_LADDER: dict[str, dict[str, bool]] = {
    "continuous": {"include_usage": True, "continuous_usage_stats": True},
    "final": {"include_usage": True},
}
_NEXT_USAGE_MODE = {"continuous": "final", "final": "off"}


def _approx_tokens(chars: int) -> int:
    """Characters to an approximate token count, rounding up so any produced
    text reads as at least one token."""
    return (chars + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _usage_token_counts(usage: Any) -> tuple[int | None, int | None]:
    """(completion_tokens, reasoning_tokens) out of a usage payload of either
    shape — an SDK object or a plain dict — with missing pieces as None."""

    def _get(obj: Any, name: str) -> Any:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    completion = _get(usage, "completion_tokens")
    details = _get(usage, "completion_tokens_details")
    reasoning = _get(details, "reasoning_tokens") if details is not None else None
    return (
        completion if isinstance(completion, int) else None,
        reasoning if isinstance(reasoning, int) else None,
    )


def _usage_prompt_tokens(usage: Any) -> int | None:
    """prompt_tokens off a usage payload of either shape, or None when absent.

    Separate from _usage_token_counts because that one answers "what did the
    model generate"; this answers "how big was what we sent", which is the
    number the compaction trigger runs on.
    """
    if isinstance(usage, dict):
        value = usage.get("prompt_tokens")
    else:
        value = getattr(usage, "prompt_tokens", None)
    return value if isinstance(value, int) else None


def _exact_counts(
    usage: Any, reasoning_chars: int, content_chars: int
) -> tuple[int, int] | None:
    """(reasoning, content) token counts anchored to a stream's usage totals, or
    None when there is no usable total and the estimates should stand.

    `completion_tokens` is the whole output INCLUDING reasoning, so it is only
    the content count outright when the response had no reasoning at all. Three
    cases, in descending order of confidence:

    1. The provider broke reasoning out — exact on both counters.
    2. No reasoning happened — the total IS the content count, exact.
    3. Reasoning happened with no breakdown (measured on NVIDIA NIM, which
       reports completion_tokens and no completion_tokens_details): apportion
       the exact total by the character ratio actually seen on the wire. The
       total is then right and only its division is derived — strictly better
       than discarding the one exact number in the call and showing two
       independent guesses.
    """
    completion, reasoning = _usage_token_counts(usage)
    if completion is None:
        return None
    if reasoning is not None:
        return reasoning, max(0, completion - reasoning)
    if reasoning_chars <= 0:
        return 0, completion
    total_chars = reasoning_chars + content_chars
    if total_chars <= 0:
        return None
    reasoning_share = round(completion * reasoning_chars / total_chars)
    # Subtract rather than round twice, so the parts always sum to the exact total.
    return reasoning_share, max(0, completion - reasoning_share)

# Streamed reasoning is not in the OpenAI spec, so the ecosystem settled on two
# different delta field names: `reasoning` (OpenRouter, Groq) and
# `reasoning_content` (DeepSeek, vLLM, NVIDIA NIM — confirmed live against
# nvidia/nemotron-3-ultra, whose deltas carry reasoning_content and no reasoning).
# Both are first-class here; neither is a vendor special case.
_REASONING_DELTA_FIELDS: tuple[str, ...] = ("reasoning", "reasoning_content")


def _first_reasoning_chunk(delta: Any) -> str | None:
    """The one non-empty reasoning chunk on a stream delta, or None.

    Returns at most ONE value even when an endpoint populates both field names,
    so the same text is never reported to on_thinking twice. Tolerates a delta
    that has neither field (or no attributes at all, incl. None).
    """
    for field in _REASONING_DELTA_FIELDS:
        value = getattr(delta, field, None)
        if isinstance(value, str) and value:
            return value
    return None


def normalize_base_url(raw: str | None) -> str | None:
    """Accept what a user actually pastes, and return a base URL the OpenAI SDK
    can append `/chat/completions` to.

    Vendors document the full endpoint URL, so pasting it (with or without a
    trailing slash) is the predictable mistake — strip it back to the base.
    Pure: no env reads, no I/O. Returns None for anything that isn't a URL at
    all, so the caller owns the "required" error message.
    """
    if raw is None:
        return None
    trimmed = raw.strip().rstrip("/")
    if trimmed.endswith(_CHAT_COMPLETIONS_SUFFIX):
        # rstrip again: a doubled separator ("…/v1//chat/completions") would
        # otherwise leak a trailing slash, breaking the one shape invariant
        # every returned value here has.
        trimmed = trimmed[: -len(_CHAT_COMPLETIONS_SUFFIX)].rstrip("/")
    return trimmed or None


class NonProbativeError(RuntimeError):
    """Marker: this failure says NOTHING about whether the endpoint can honor
    `response_format`, so it must never trip the sticky json_object downgrade.

    Subclasses RuntimeError so every existing `except RuntimeError` and message
    assertion keeps working; the type exists purely to carry that one bit to
    `proves_json_schema_unsupported`.
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


def proves_json_schema_unsupported(
    exc: Exception, *, finish_reason: str | None = None
) -> bool:
    """Does this strict-call failure actually prove the endpoint can't do
    json_schema? Only then may the process-wide downgrade fire.

    Public because the validate-route capability probe faces the identical
    question and must reach the identical verdict — two copies of this asymmetry
    would drift, and the drift would be invisible until a user got told their
    good endpoint was degraded.

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
        json_mode: str = "strict",
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
        # How much usage reporting this endpoint tolerates. Steps down a rung the
        # first time a request is rejected, permanently for this instance — see
        # _open_stream for why that rejection must never escape.
        self._stream_usage_mode = "continuous"
        # Instance attribute, NOT a class attribute: OpenRouter must keep the
        # contracts default (False) while openai_compatible opts in to True.
        self.supports_oneof_grammar = supports_oneof
        # Advertises on_progress support so the engine can pass it WITHOUT every
        # other transport having to grow the kwarg (same getattr-defensive idiom as
        # supports_oneof_grammar / requires_all_fields).
        self.supports_token_progress = True
        # The EFFECTIVE rung (already clamped by ProviderRuntime), or None to send
        # no effort field at all — which is both the default and the escape hatch
        # back to pre-feature behavior.
        self._reasoning_effort: ReasoningEffort | None = None
        # Rungs this endpoint has PROVEN it rejects, learned from probative 400s
        # (Task 3). Per rung, never whole-capability: the sticky JSON-mode
        # downgrade's documented flaw is collapsing "cannot honor THIS" into
        # "cannot honor ANY", and the rungs here are independent.
        self._effort_rejected: dict[ReasoningEffort, str] = {}
        # "strict" | "json_object" | "none". Every generate_json starts by probing
        # strict json_schema; the first failure that PROVES the endpoint can't honor it
        # flips this for the rest of the process (see _downgrade_json_mode).
        #
        # "none" is a DEVELOPMENT escape hatch, only reachable by pinning it here (env
        # CRUCIBLE_OPENAI_COMPAT_JSON_MODE=none) — no failure ever downgrades INTO it.
        # It sends no response_format at all, so the endpoint applies no grammar, and
        # the schema reaches the model through the system prompt alone.
        #
        # Why it exists: NVIDIA NIM's grammar enforcement corrupts the JSON escape
        # `\"` when the next literal character is a closing bracket, turning
        # `pytest.main([__file__, "-v"])` into `"-v"})` — silently, as VALID JSON, so
        # nothing retries. Verified live on nemotron-3-ultra and -super; the same
        # weights on Ollama Cloud are clean, and unconstrained NIM is clean (4/4).
        # The existing json_object downgrade is no escape: json_object is itself
        # grammar-enforced and corrupts identically. Hence a third rung.
        #
        # The trade: nothing enforces the shape, so malformed JSON becomes the model's
        # responsibility. Survivable because the fallback loop already retries
        # malformed output and ControllerLoop has its own correct-and-continue path —
        # but it IS a real robustness downgrade. Do not make it a default.
        self._json_mode: str = json_mode

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

    @property
    def json_mode(self) -> str:
        """Current structured-output mode: "strict" until a probative failure
        downgrades it to "json_object" for the rest of this process.

        Read-only on purpose — `_downgrade_json_mode` stays the single writer.
        Exposed because "did strict actually work?" is not answerable from the
        OUTSIDE by watching for an exception: generate_json falls back to
        json_object and returns a valid dict, so a rejected strict call looks
        exactly like a successful one to a caller. The validate-route probe reads
        this to tell those two apart.
        """
        return self._json_mode

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
        if self._reasoning_effort is not None:
            extra_body["reasoning_effort"] = _OPENAI_COMPAT_EFFORT_WIRE[self._reasoning_effort]
        return extra_body

    async def _reasoning_config(self, model: str) -> tuple[bool, float]:
        """(is_reasoning, temperature). Base uses the name-substring heuristic."""
        is_reasoning = _is_reasoning_model(model)
        return is_reasoning, (1.0 if is_reasoning else 0.0)

    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        """Store the effective rung. None sends no effort field at all."""
        self._reasoning_effort = level

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        """What this endpoint can express for `model`.

        Deliberately pessimistic about knowledge, not about capability: an
        arbitrary base URL could be vLLM, LM Studio, Together, or DeepInfra, so
        every rung we have not disproven stays UNKNOWN and gets sent. MAX is the
        one rung we can rule out from the protocol alone.
        """
        is_reasoning, _ = await self._reasoning_config(model)
        if not is_reasoning:
            return EffortSupport(
                supported=frozenset({ReasoningEffort.OFF}),
                unsupported={
                    level: "this model does not expose reasoning"
                    for level in LADDER
                    if level is not ReasoningEffort.OFF
                },
            )
        unsupported = {
            ReasoningEffort.MAX: "OpenAI-compatible endpoints top out at 'high'",
            **self._effort_rejected,
        }
        return EffortSupport(unsupported=unsupported)

    def _note_effort_rejection(self, exc: Exception) -> bool:
        """Record a proven-bad rung and drop the field. True when it fired.

        Marks ONLY the rung that was in flight. The effort is cleared to None so
        the immediate retry (and every call until the next swap re-resolves) omits
        the field entirely rather than guessing at a replacement — the runtime owns
        clamping, and it will pick the right neighbour on the next resolve.
        """
        level = self._reasoning_effort
        if level is None or not _is_probative_effort_rejection(exc):
            return False
        self._effort_rejected[level] = f"this endpoint rejected '{level}'"
        self._reasoning_effort = None
        logger.warning(
            "[effort] %s rejected reasoning_effort=%s; dropping it for this process",
            self._label,
            level,
        )
        return True

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
        on_progress: Any = None,
        on_usage: Any = None,
        on_salvage: Any = None,
        unconstrained: bool = False,
    ) -> dict[str, object]:
        # on_usage is per LOGICAL call, but one call can stream more than once: a
        # strict attempt can complete, fail to parse, and fall back to json_object
        # (a second stream) — and controller_step_response can additionally retry
        # with a narrowed schema (up to two more). Each attempt measures a
        # DIFFERENT prompt — the fallback injects the schema into the system
        # prompt — so reporting every attempt would hand the compaction trigger a
        # size for a call whose result we discarded. `attempts` is not a log to
        # take the last entry of — it is cleared at each attempt boundary (here,
        # before the narrowed retry; the strict/fallback boundary is handled the
        # same way one level down in _generate_json_once) so a silent WINNING
        # attempt is never masked by an earlier attempt that happened to report.
        # Record under a local recorder and fire the caller's callback exactly
        # once, below, for the attempt that actually produced the returned result
        # — or not at all, if that attempt was silent.
        attempts: list[tuple[int, int]] = []
        recorder = (lambda p, c: attempts.append((p, c))) if on_usage is not None else None
        succeeded = False
        try:
            result = await self._generate_json_once(
                model=model,
                schema_name=schema_name,
                schema=schema,
                system_instructions=system_instructions,
                user_payload=user_payload,
                on_thinking=on_thinking,
                on_retry=on_retry,
                on_progress=on_progress,
                on_usage=recorder,
                on_salvage=on_salvage,
                unconstrained=unconstrained,
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
                    # The narrowed retry is a fresh attempt. If the FIRST call
                    # already recorded usage but this one's own attempt is silent,
                    # attempts must not still hold the first call's now-stale
                    # entry — clear before the retry, not just take-the-last.
                    attempts.clear()
                    result = await self._generate_json_once(
                        model=model,
                        schema_name=schema_name,
                        schema=narrowed,
                        system_instructions=system_instructions,
                        user_payload=user_payload,
                        on_thinking=on_thinking,
                        on_retry=on_retry,
                        on_progress=on_progress,
                        on_usage=recorder,
                        on_salvage=on_salvage,
                        unconstrained=unconstrained,
                    )
            succeeded = True
            return result
        finally:
            # succeeded is set only right before `return result` above, so an
            # exception propagating out of either _generate_json_once call (every
            # attempt exhausted, no result produced) leaves it False and skips the
            # firing entirely — there is no call left standing to attribute a
            # prompt size to.
            if succeeded and on_usage is not None and attempts:
                on_usage(*attempts[-1])

    async def _get_completion_output(
        self, create_kwargs: dict[str, Any], on_thinking: Any, on_retry: Any = None,
        on_progress: Any = None, on_usage: Any = None,
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
        # Gate on ANY callback. Gating on on_thinking alone meant a caller that
        # wanted only the token counter (or only usage accounting) got the
        # non-streaming path — no deltas, so no progress, and the whole response
        # arriving in one lump at the end with no usage ever observed.
        if callable(on_thinking) or callable(on_progress) or callable(on_usage):
            return await self._stream_with_finish_reason(
                create_kwargs, on_thinking=on_thinking, on_retry=on_retry,
                on_progress=on_progress, on_usage=on_usage,
            )
        response = await self._call_with_retry(create_kwargs, on_retry=on_retry)
        return self._extract_text(response), _response_finish_reason(response)

    async def _get_completion_text(
        self, create_kwargs: dict[str, Any], on_thinking: Any, on_retry: Any = None,
        on_progress: Any = None, on_usage: Any = None,
    ) -> str:
        """Text-only view of _get_completion_output, for callers that have no use
        for the finish_reason (the json_object fallback — it never decides a
        downgrade, so truncation there is just a malformed-JSON retry)."""
        text, _finish_reason = await self._get_completion_output(
            create_kwargs, on_thinking, on_retry, on_progress, on_usage
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
        on_progress: Any = None,
        on_usage: Any = None,
        on_salvage: Any = None,
        unconstrained: bool = False,
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

        # on_usage is per LOGICAL call at THIS layer too: strict and fallback are
        # two separate stream attempts inside a single _generate_json_once call,
        # and only one of them produces the result actually returned. Own a
        # recorder scoped to just this call (distinct from generate_json's own
        # recorder one level up, which this reports into via the caller's
        # on_usage) and fire exactly once, in the finally below, for whichever
        # attempt won — never a stale earlier attempt whose output was discarded.
        attempt_usage: list[tuple[int, int]] = []
        recorder = (lambda p, c: attempt_usage.append((p, c))) if on_usage is not None else None
        succeeded = False
        try:
            # One-shot escape hatch: the caller saw evidence that the grammar
            # corrupted the last response (preflight rejected the generated code),
            # so skip it for THIS call only. Per-call, never sticky —
            # `self._json_mode` is untouched, so the next call is constrained
            # again and a false positive costs one call.
            if self._json_mode == "strict" and not unconstrained:
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
                        create_kwargs, on_thinking, on_retry, on_progress, recorder
                    )
                    result = self._parse_output_object(
                        output_text, schema_name, on_salvage)
                    succeeded = True
                    return result
                except Exception as e:
                    # Fall back to json_object with schema injected into system prompt.
                    # Some models/providers don't support json_schema strict mode.
                    # The fallback itself is unconditional (unchanged behavior); only
                    # the PERMANENT downgrade needs the failure to be probative.
                    if proves_json_schema_unsupported(e, finish_reason=finish_reason):
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
                    # The fallback below is a SEPARATE stream attempt from the
                    # strict one just abandoned (strict streamed fine here; only
                    # the PARSE failed). If strict already recorded usage, that
                    # entry must not survive to be reported as if it described
                    # the fallback's — possibly silent — attempt.
                    attempt_usage.clear()

            result = await self._json_object_fallback(
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
                on_progress=on_progress,
                on_usage=recorder,
                on_salvage=on_salvage,
                unconstrained=unconstrained,
            )
            succeeded = True
            return result
        finally:
            # Same exception-safety shape as generate_json's own finally: succeeded
            # is set only right before each return, so an exception out of either
            # attempt (nothing left standing) skips the firing entirely.
            if succeeded and on_usage is not None and attempt_usage:
                on_usage(*attempt_usage[-1])

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
        try:
            # Inside the try on purpose: provider_debug_root resolves paths against
            # the cwd and can itself raise OSError, which would break "never raises".
            out_dir = provider_debug_root(self._vendor)
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
        on_progress: Any = None,
        on_usage: Any = None,
        on_salvage: Any = None,
        unconstrained: bool = False,
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
        }
        # json_object is grammar-enforced, so on an endpoint whose grammar corrupts
        # escapes it is no safer than json_schema. "none" omits response_format
        # entirely — the only way to get an unconstrained decode. See _json_mode.
        if self._json_mode != "none" and not unconstrained:
            fallback_kwargs["response_format"] = {"type": "json_object"}
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
                    fallback_kwargs, on_thinking, on_retry, on_progress, on_usage
                )
                return self._parse_output_object(
                    output_text, schema_name, on_salvage)
            except TransientTransportError as e2:
                # MUST precede `except RuntimeError` — TransientTransportError is a
                # RuntimeError subclass, so the base handler would shadow it.
                # Infrastructure, not the model: a timeout, a dropped stream, or a
                # provider-side capacity error (live: NIM "ResourceExhausted: Worker
                # local total request limit reached (32/32)", which carries no
                # status_code so _is_retryable misses it). Raising here burned one of
                # the controller's three attempts as if the model had misbehaved —
                # and the request never reached the model, so its thinking showed no
                # error to reason about. This is the one class that SHOULD retry.
                last_parse_exc = e2
                continue
            except RuntimeError as e2:
                if "not valid JSON" in str(e2) or "must be a JSON object" in str(e2):
                    # Do NOT retry: the messages are byte-identical every attempt, so a
                    # parse failure is input-determined and resampling reproduces it
                    # (measured live: 3 identical ~1500-token regenerations, all bad).
                    # Raise now, carrying the offending text, and let the loop layer —
                    # which knows the schema — correct the model an attempt later
                    # instead of four. Transient failures still retry above; only this
                    # deterministic class short-circuits.
                    raise
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
        on_usage: Any = None,
        max_tokens: int | None = None,
    ) -> str:
        is_reasoning, temperature = await self._reasoning_config(model)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            # Keyword-only override, default None = self._max_tokens (today's
            # behaviour unchanged for every existing caller). Exists for the
            # context-window probe: it needs ~10 output tokens, and sending the
            # anti-runaway default (4096) on top of a near-full-window prompt is
            # what makes an endpoint that validates prompt + max_tokens <=
            # context_length reject a CORRECTLY declared window.
            "max_completion_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "temperature": temperature,
        }
        extra_body = self._build_extra_body(model, is_reasoning, for_json=False)
        if extra_body:
            create_kwargs["extra_body"] = extra_body

        if callable(on_thinking):
            return await self._stream_with_thinking(
                create_kwargs, on_thinking=on_thinking, on_usage=on_usage
            )

        try:
            response = await self._call_with_retry(create_kwargs)
            # The non-streaming response already carries usage; it was simply being
            # dropped. Reading it is what lets the context test print the provider's
            # OWN prompt_tokens beside its verdict instead of only our estimate.
            #
            # _usage_prompt_tokens (not a bare getattr) for the same reason the
            # streaming path already uses it: a reported prompt_tokens of 0 means
            # the endpoint doesn't know, not that the prompt was empty (some
            # proxies/vLLM builds emit an all-zero usage on paths they don't fully
            # account for). Trusting a 0 here would render the context test's
            # verdict as an exact "0 tokens sent" instead of falling back to our
            # own chars-per-token estimate.
            if on_usage is not None:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    prompt_tokens = _usage_prompt_tokens(usage)
                    completion_tokens, _ = _usage_token_counts(usage)
                    if prompt_tokens:
                        on_usage(prompt_tokens, completion_tokens or 0)
            return self._extract_text(response)
        except Exception as e:
            if self._note_effort_rejection(e):
                # One bare retry with the field gone. The rung is now recorded, so
                # this cannot loop: a second failure has no effort left to blame.
                create_kwargs.pop("extra_body", None)
                retry_extra = self._build_extra_body(model, is_reasoning, for_json=False)
                if retry_extra:
                    create_kwargs["extra_body"] = retry_extra
                try:
                    response = await self._call_with_retry(create_kwargs)
                except Exception as retry_exc:
                    # The retry gets the SAME wrapping as every other failure path
                    # out of generate_text. Without this the second failure escapes
                    # raw, so the one error the user sees on a rejecting endpoint is
                    # the only one missing the provider label.
                    raise RuntimeError(f"{self._label} API error: {retry_exc}") from retry_exc
                return self._extract_text(response)
            raise RuntimeError(f"{self._label} API error: {e}") from e

    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
        on_progress: Any = None,
        on_usage: Any = None,
    ) -> str:
        """Text-only view of _stream_with_finish_reason (generate_text's entry
        point, and the long-standing public-ish shape of this method).

        on_progress is forwarded rather than dropped: without it this path could
        never report a count no matter how long the generation ran. on_usage rides
        along for the same reason — the exact count exists, and dropping it here
        would make the caller re-derive an estimate it does not need to."""
        text, _finish_reason = await self._stream_with_finish_reason(
            create_kwargs, on_thinking=on_thinking, on_retry=on_retry,
            on_progress=on_progress, on_usage=on_usage,
        )
        return text

    async def _open_stream(self, kwargs: dict[str, Any]) -> Any:
        """Open the stream, asking for the exact-usage chunk.

        `stream_options` is the only way to get that chunk — without it a
        spec-compliant endpoint sends no usage at all (verified against NVIDIA
        NIM: 0 usage chunks without the parameter, 1 with it). But it is an
        extra request parameter, and a strict endpoint can reject it.

        That rejection must not escape. It would travel up to
        `proves_json_schema_unsupported`, which reads a rejected request as
        evidence the endpoint cannot honour `response_format` and PERMANENTLY
        downgrades JSON mode for the rest of the process. A parameter added for
        a progress counter must never be able to degrade every later call, so
        it is dropped and retried here, once, before anyone can judge it.

        Retryable failures (429/5xx) are re-raised untouched so the caller's
        backoff still owns them and a rate limit never disables usage
        reporting for the process.
        """
        while self._stream_usage_mode != "off":
            probe = {**kwargs, "stream_options": _STREAM_USAGE_LADDER[self._stream_usage_mode]}
            try:
                return await asyncio.wait_for(
                    self._completions.create(**probe), timeout=self._timeout_sec
                )
            except TimeoutError:
                raise
            except Exception as exc:
                if _is_retryable(exc):
                    raise
                nxt = _NEXT_USAGE_MODE[self._stream_usage_mode]
                logger.info(
                    "%s: endpoint rejected stream usage mode %r, falling back to %r: %s",
                    self._label, self._stream_usage_mode, nxt, exc,
                )
                self._stream_usage_mode = nxt
        return await asyncio.wait_for(
            self._completions.create(**kwargs), timeout=self._timeout_sec
        )

    async def _stream_with_finish_reason(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
        on_progress: Any = None,
        on_usage: Any = None,
    ) -> tuple[str, str | None]:
        """Stream response forwarding reasoning chunks to on_thinking callback.

        There is no single reasoning field: an OpenAI-compatible endpoint may
        surface it as `delta.reasoning` (OpenRouter, Groq) OR as
        `delta.reasoning_content` (DeepSeek, vLLM, NVIDIA NIM). Both are read —
        see _first_reasoning_chunk.

        on_progress(reasoning_n, content_n) reports running delta counts DURING the
        call. Reasoning is already visible through on_thinking, but content deltas
        are accumulated silently and only surface when the call returns, so a long
        generation is indistinguishable from a hang (observed live: a 15-minute
        call behind a motionless UI). Emission is throttled — measured ~29
        deltas/sec, and one broadcast per delta would flood the SSE channel.

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
                stream = await self._open_stream(kwargs)
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
                # Characters, not deltas — see _approx_tokens.
                reasoning_chars = content_chars = 0
                usage_payload: Any = None
                # Exact accounting, when the endpoint reports running totals.
                # Each chunk's increment belongs to the phase that chunk carried;
                # measured on NIM, reasoning and content never share a chunk.
                usage_total: int | None = None
                counted_total = 0
                reasoning_tokens = content_tokens = 0
                last_progress = 0.0
                async for chunk in stream:
                    # A usage-reporting stream ends with one extra chunk that has
                    # NO choices and carries the exact totals. Read it before the
                    # empty-choices skip below, which would otherwise discard the
                    # only exact number the provider ever hands us.
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        usage_payload = chunk_usage
                        reported, _ = _usage_token_counts(chunk_usage)
                        if reported is not None:
                            usage_total = reported
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    chunk_reason = getattr(choices[0], "finish_reason", None)
                    if isinstance(chunk_reason, str) and chunk_reason:
                        finish_reason = chunk_reason
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    reasoning = _first_reasoning_chunk(delta)
                    if reasoning:
                        reasoning_chars += len(reasoning)
                        # Guarded: this path is now also reachable for a caller
                        # that wants progress but not the reasoning text itself.
                        if callable(on_thinking):
                            on_thinking(reasoning)
                    content = getattr(delta, "content", None) or ""
                    if content:
                        content_chars += len(content)
                        content_parts.append(content)
                    # Attribute this chunk's share of the running total.
                    if usage_total is not None and usage_total > counted_total:
                        gained = usage_total - counted_total
                        counted_total = usage_total
                        if reasoning:
                            reasoning_tokens += gained
                        elif content:
                            content_tokens += gained
                    if on_progress is not None and (reasoning or content):
                        now = time.monotonic()
                        if now - last_progress >= _PROGRESS_INTERVAL_SEC:
                            last_progress = now
                            if reasoning_tokens or content_tokens:
                                on_progress(reasoning_tokens, content_tokens)
                            else:
                                on_progress(
                                    _approx_tokens(reasoning_chars),
                                    _approx_tokens(content_chars),
                                )
                # Final emit: throttling can swallow the last tick, and the closing
                # number is the one a user actually reads. It also carries the only
                # count a single-delta response ever produces — and, when the
                # provider reported usage, the one exact count in the whole call.
                # The live ticks above stay estimated: the format carries no
                # per-delta token counts, so there is nothing exact to show until
                # the stream ends.
                if on_progress is not None and (reasoning_chars or content_chars):
                    if reasoning_tokens or content_tokens:
                        # Per-chunk attribution ran. The closing chunk carries
                        # neither phase, so its tokens are unattributed — content
                        # absorbs the remainder so the parts sum to the provider's
                        # own total rather than drifting a few tokens under it.
                        if usage_total is not None:
                            content_tokens = max(0, usage_total - reasoning_tokens)
                        on_progress(reasoning_tokens, content_tokens)
                    else:
                        exact = (
                            _exact_counts(usage_payload, reasoning_chars, content_chars)
                            if usage_payload is not None
                            else None
                        )
                        if exact is not None:
                            on_progress(*exact)
                        else:
                            on_progress(
                                _approx_tokens(reasoning_chars),
                                _approx_tokens(content_chars),
                            )
                # Accounting, not display: one call, unthrottled, and only when the
                # endpoint actually reported. A zero here would be indistinguishable
                # from a real measurement of an empty prompt.
                if on_usage is not None and usage_payload is not None:
                    prompt_n = _usage_prompt_tokens(usage_payload)
                    completion_n, _ = _usage_token_counts(usage_payload)
                    # Truthy, not `is not None`: a real prompt is never 0 tokens (the
                    # system prompt alone is ~14.5k), so a reported 0 is the endpoint
                    # saying "I don't know" (some proxies/vLLM builds emit
                    # {"prompt_tokens": 0, "completion_tokens": 0} on the terminal
                    # chunk), not a measurement of an empty prompt — the case the
                    # comment above already argues for. Trusting it would zero out
                    # input_tokens() and stop the compaction trigger from ever firing
                    # again this turn.
                    if prompt_n:
                        on_usage(prompt_n, completion_n or 0)
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

    def _parse_output_object(
        self, output_text: str, schema_name: str, on_salvage: Any = None,
    ) -> dict[str, object]:
        payload_text = _strip_json_code_fences(output_text)
        try:
            # raw_decode, NOT json.loads: it parses the FIRST complete JSON value and
            # returns where it stopped, tolerating trailing data. This transport was
            # the only one on bare json.loads (ollama/turboquant/watsonx already use
            # raw_decode), so a model emitting two actions in one reply cost the whole
            # generation — measured live at 19,772 / 20,878 / 26,714 / 30,681 / 39,225
            # chars discarded and regenerated, with the first object valid every time.
            #
            # Safe by protocol: exactly ONE action per response is the contract, so the
            # first object IS the action. Surplus after it is dropped; the loop re-derives
            # it next iteration. A first object that is itself invalid still raises below
            # with the full error window — salvage must never mask a real failure.
            payload, end = json.JSONDecoder().raw_decode(payload_text)
            trailing = payload_text.rstrip()[end:].strip()
            # ALWAYS salvage the first object, whatever follows it.
            #
            # A short remainder is ambiguous — `]}` follows a complete object (surplus
            # brackets), `"}` follows one that closed early because an unescaped quote
            # ended a string, leaving truncated content. The bytes look identical, so a
            # guard here can only guess.
            #
            # It does not need to guess, because PREFLIGHT is the real net: the live
            # truncation case was caught downstream as "unterminated string error in
            # game_loop.py at line 468" and the edit was rejected before touching disk.
            # So truncation is loud already, and salvaging costs nothing when the object
            # is fine — whereas raising costs a full regeneration (~10K tokens) in BOTH
            # cases. Residual risk: a file type with no syntax check (markdown, config)
            # could take truncated content; preflight cannot see those.
            if trailing:
                logger.warning(
                    "%s: %s had %d trailing chars after a complete object — salvaged "
                    "the first, discarded the rest",
                    self._label, schema_name, len(trailing))
                # Report it: a SILENT discard desynchronises the model from reality —
                # it emitted two actions, only the first ran, and its history shows
                # just that one's result. The loop owns telling it (this layer must
                # not author model-facing text).
                if callable(on_salvage):
                    on_salvage(len(trailing), trailing[:400])

        except json.JSONDecodeError as exc:
            # NO regex "repair" pass here, deliberately. The obvious one — quoting
            # unquoted property names, as ollama/turboquant/watsonx do — runs over the
            # whole text, string values included, and Python type annotations are
            # indistinguishable from unquoted JSON keys:
            #   def __init__(self, x: int = 0)  ->  def __init__(self, "x": int = 0)
            # Observed live corrupting two consecutive create_file edits, then
            # reporting the damage it had just caused ("after repair, Expecting ','").
            # Those transports carry file content in JSON strings too and have the
            # same latent bug. Report the payload AS THE MODEL WROTE IT — it can only
            # fix what it actually emitted.
            raise RuntimeError(
                f"{self._label} output is not valid JSON for {schema_name} "
                f"({exc}). Near the error: {_error_window(payload_text, exc.pos)}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self._label} output must be a JSON object")
        return payload


_ERROR_WINDOW_RADIUS = 70


def _trailing_remainder_message(n: int) -> str:
    """Describe unparsed trailing characters WITHOUT guessing the cause.

    A short remainder has two indistinguishable causes, both seen live:
      `"}`  — a string ended early (unescaped quote), so the object closed before its
              content finished; the file written was truncated ("unterminated string
              literal at line 468").
      `]}`  — the object closed correctly and surplus brackets followed; the content
              was complete.
    The bytes look the same. Naming one is wrong half the time, so name both and let
    the model check. Raising either way is the safe default: salvaging a truncated
    object writes a silently corrupt file, while raising costs one regeneration.
    """
    return (
        f"{n} unparsed character(s) followed the JSON object. Either a string ended "
        "early (an unescaped \" inside it) so the object closed before its content "
        "was finished, or you emitted extra closing brackets after it. Check that "
        "every \" inside a string value is escaped and that your brackets balance, "
        "then send the complete object again."
    )


def _error_window(text: str, pos: int, radius: int = _ERROR_WINDOW_RADIUS) -> str:
    """The bytes AROUND a parse failure, with the offending position marked.

    The head of a malformed response is the least useful part — '{"type":"answer",
    "thought":"…' is always well-formed. Live, a control character at char 217 was
    reported to the model alongside output_text[:500], which the loop then capped to
    300, leaving 178 chars: the error was in the truncated-away remainder. It got an
    exact coordinate into text it could not see, and failed four times running.

    Control characters are escaped in the excerpt so the marker stays on one line —
    a raw newline is the most common offender and would otherwise be invisible.
    """
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    def _vis(chunk: str) -> str:
        return chunk.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    before, after = _vis(text[start:pos]), _vis(text[pos + 1:end])
    at = _vis(text[pos:pos + 1])
    lead = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{lead}{before}◀HERE▶{at}{after}{tail}"


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
