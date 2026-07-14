"""TurboQuant transport — llama-cpp-turboquant server, OpenAI chat-completions protocol.

Design
------
Strategy pattern (ModelProfile)
  Every model family encapsulates its own sampling params and two behavioural hooks:
    augment_body(body)     — inject model-specific request fields (template kwargs, budgets)
    post_process_text(text) — clean model-specific artifacts from raw output (think blocks)

  TurboQuantTransport depends only on the ModelProfile abstraction (Dependency Inversion).
  Adding a new model = subclass ModelProfile + register in PROFILES. Transport never changes.

Built-in profiles
  QWEN3    — Qwen3-family: JINJA thinking template, think-block stripping, anti-loop sampling
  DEVSTRAL — Devstral/Mistral-family: no thinking, standard coding defaults

Factory
  TurboQuantTransport.from_env()        reads TURBOQUANT_MODEL_FAMILY (default: devstral),
                                        then applies per-param env overrides
  TurboQuantTransport.for_model("qwen3") selects a named profile with no env reads
  TurboQuantTransport(profile=...)       fully explicit for tests / custom profiles

Extending
  To add Llama4 (hypothetical):
    @dataclass(frozen=True)
    class Llama4Profile(ModelProfile):
        def augment_body(self, body): body["custom_field"] = "value"
    PROFILES["llama4"] = Llama4Profile(temperature=0.4, top_p=0.9, top_k=50, min_p=0.0, ...)
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from agentd.providers.contracts import ModelJsonTransport

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

_DEFAULT_HOST = "http://localhost:11435"
_DEFAULT_MAX_TOKENS = int(os.environ.get("TURBOQUANT_MAX_TOKENS", "32768"))
_DEFAULT_CHUNK_TIMEOUT = float(os.environ.get("TURBOQUANT_STREAM_CHUNK_TIMEOUT_SEC", "600.0"))


# ---------------------------------------------------------------------------
# Strategy: model profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelProfile:
    """Sampling parameters and model-specific request/response hooks.

    Subclass and override augment_body / post_process_text to add model-specific
    behaviour. All other fields are shared and overridable via dataclasses.replace().

    thinking_budget: 0 = disabled. >0 = cap the model's reasoning block to N tokens.
                     Only meaningful for models that support a thinking mode (e.g. Qwen3).
    """
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    presence_penalty: float
    thinking_budget: int = 0

    def augment_body(self, body: dict[str, object]) -> None:  # noqa: B027
        """Inject model-specific fields into the request body in-place. No-op by default."""

    def post_process_text(self, text: str) -> str:
        """Remove model-specific artifacts from raw output before returning to caller.

        Base implementation strips <think>…</think> blocks defensively — any model
        could theoretically emit them. Subclasses should call super() and extend.
        """
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        idx = text.find("<think>")
        if idx != -1:
            text = text[:idx].strip()
        return text


@dataclass(frozen=True)
class Qwen3Profile(ModelProfile):
    """Qwen3-family: JINJA thinking template. post_process_text inherited from base."""

    def augment_body(self, body: dict[str, object]) -> None:
        # Qwen3 JINJA template controls whether the model emits a <think> block.
        if self.thinking_budget > 0:
            body["thinking_budget_tokens"] = self.thinking_budget
            body["chat_template_kwargs"] = {"enable_thinking": True, "preserve_thinking": True}
        else:
            body["chat_template_kwargs"] = {"enable_thinking": False}


@dataclass(frozen=True)
class DevstralProfile(ModelProfile):
    """Devstral/Mistral-family: no thinking mode, standard coding defaults."""
    # augment_body and post_process_text are no-ops — ModelProfile defaults apply.


# ---------------------------------------------------------------------------
# Named profile constants and registry
# ---------------------------------------------------------------------------

QWEN3 = Qwen3Profile(
    temperature=0.6,
    top_p=0.95,
    top_k=20,
    min_p=0.0,
    presence_penalty=1.0,
    thinking_budget=0,
)

DEVSTRAL = DevstralProfile(
    temperature=0.3,
    top_p=0.95,
    top_k=40,
    min_p=0.0,
    presence_penalty=0.0,
)

# Register new model families here. Keys are matched against TURBOQUANT_MODEL_FAMILY env var.
PROFILES: dict[str, ModelProfile] = {
    "qwen3": QWEN3,
    "devstral": DEVSTRAL,
}


def _infer_family(model: str) -> str:
    """Best-effort family guess from the model name, used only when
    TURBOQUANT_MODEL_FAMILY is not set explicitly. Keeps the sampling/thinking-
    template profile in sync with whichever model string is configured (e.g. via
    CRUCIBLE_TURBOQUANT_MODEL) instead of silently defaulting to devstral for a
    qwen model — a real footgun the runtime setup wizard hit in practice.
    """
    return "qwen3" if "qwen" in model.lower() else "devstral"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class TurboQuantTransport(ModelJsonTransport):
    """JSON transport backed by a llama-cpp-turboquant server.

    Receives a ModelProfile via constructor — does not know or care which model
    family is active. All model-specific logic lives in the profile.
    """

    def __init__(
        self,
        *,
        profile: ModelProfile,
        host: str | None = None,
        timeout_sec: float = 600.0,
        max_retries: int = 4,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        http_client: httpx.AsyncClient | None = None,
        strict_json: bool | None = None,
    ) -> None:
        self._profile = profile
        self._host = (host or os.getenv("TURBOQUANT_HOST") or _DEFAULT_HOST).rstrip("/")
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        self._max_tokens = max_tokens
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout_sec)
        # Use llama.cpp's strict json_schema grammar (token-level constraint) instead of
        # the loose json_object mode. Set TURBOQUANT_JSON_SCHEMA=0 to fall back if your
        # llama-server build rejects the response_format shape.
        if strict_json is None:
            strict_json = os.environ.get("TURBOQUANT_JSON_SCHEMA", "true").strip().lower() \
                not in ("0", "false", "no", "off")
        self._strict_json = strict_json
        # True iff this transport enforces a JSON-schema `oneOf` at the token level, so
        # the controller may use the tight discriminated-union schema. Measured
        # (2026-06-21): llama.cpp's GBNF converter enforces `oneOf` cleanly (no deadlock,
        # zero cross-variant bleed) — UNLIKE Gemini. But enforcement only holds when the
        # strict json_schema grammar is actually applied: thinking ON makes llama.cpp
        # silently fall back to loose json_object (grammar dropped). MUST mirror the gate
        # in `_build_body` (strict + thinking_budget == 0). Fixed at construction, like
        # `_strict_json`.
        self.supports_oneof_grammar: bool = strict_json and profile.thinking_budget == 0

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "TurboQuantTransport":
        """Build from environment variables.

        TURBOQUANT_MODEL_FAMILY  — profile to use (default: inferred from
                                    CRUCIBLE_TURBOQUANT_MODEL / the resolved model
                                    name — "qwen3" if it contains "qwen", else
                                    "devstral")
        TURBOQUANT_TEMPERATURE   — override profile temperature
        TURBOQUANT_TOP_P         — override profile top_p
        TURBOQUANT_TOP_K         — override profile top_k
        TURBOQUANT_MIN_P         — override profile min_p
        TURBOQUANT_PRESENCE_PENALTY — override profile presence_penalty
        TURBOQUANT_THINKING_BUDGET  — override thinking_budget (Qwen3)
        TURBOQUANT_MAX_TOKENS    — total output token budget (default 8192)
        TURBOQUANT_HOST          — server URL (default http://localhost:11435)
        """
        from agentd.providers.factory import default_model

        family_override = os.environ.get("TURBOQUANT_MODEL_FAMILY")
        if family_override:
            family = family_override.lower()
        else:
            model = os.environ.get("CRUCIBLE_TURBOQUANT_MODEL") or default_model("turboquant")
            family = _infer_family(model)
        if family not in PROFILES:
            raise ValueError(
                f"Unknown TURBOQUANT_MODEL_FAMILY={family!r}. "
                f"Known profiles: {sorted(PROFILES)}"
            )
        profile = cls._apply_sampling_env_overrides(PROFILES[family])
        return cls(
            profile=profile,
            host=os.getenv("TURBOQUANT_HOST"),
            max_tokens=int(os.environ.get("TURBOQUANT_MAX_TOKENS", str(_DEFAULT_MAX_TOKENS))),
        )

    @classmethod
    def for_model(cls, family: str, **kwargs: Any) -> "TurboQuantTransport":
        """Convenience factory: select a named profile with no env reads.

        Example:
            transport = TurboQuantTransport.for_model("qwen3")
            transport = TurboQuantTransport.for_model("devstral", max_tokens=4096)
        """
        if family not in PROFILES:
            raise ValueError(
                f"Unknown model family {family!r}. Known: {sorted(PROFILES)}"
            )
        return cls(profile=PROFILES[family], **kwargs)

    @staticmethod
    def _apply_sampling_env_overrides(profile: ModelProfile) -> ModelProfile:
        """Return a copy of profile with any TURBOQUANT_* env vars applied."""
        overrides: dict[str, Any] = {}
        for field, env_var, cast in (
            ("temperature",       "TURBOQUANT_TEMPERATURE",        float),
            ("top_p",             "TURBOQUANT_TOP_P",              float),
            ("top_k",             "TURBOQUANT_TOP_K",              int),
            ("min_p",             "TURBOQUANT_MIN_P",              float),
            ("presence_penalty",  "TURBOQUANT_PRESENCE_PENALTY",   float),
            ("thinking_budget",   "TURBOQUANT_THINKING_BUDGET",    int),
        ):
            raw = os.environ.get(env_var)
            if raw is not None:
                overrides[field] = cast(raw)  # type: ignore[operator]
        return dataclasses.replace(profile, **overrides) if overrides else profile

    # ------------------------------------------------------------------
    # ModelJsonTransport interface
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

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
    ) -> dict[str, object]:
        # Constrained decoding: when thinking is OFF (our default) we send the schema as
        # a strict llama.cpp json_schema grammar (see _build_body) so the model CANNOT
        # emit prose or malformed JSON. We still also embed the schema in the user content
        # below — the grammar enforces STRUCTURE, the prompt guides VALUES.
        #
        # KV-CACHE: the schema is per-turn-variable (the execution loop filters its
        # `type` enum per SM state). Keeping it in the system message — the prompt's
        # prefix root — would invalidate the cached prefix on every state transition and
        # re-prefill the ENTIRE history. So the system message is held CONSTANT and the
        # schema/format directive is appended to the END of the user content (after the
        # history), where a per-turn change only re-prefills the short trailing block.
        system = system_instructions
        contents = (
            f"{json.dumps(user_payload)}\n\n"
            "REQUIRED OUTPUT FORMAT — return ONLY a JSON object matching this schema "
            "(no markdown fences, no commentary):\n"
            f"{json.dumps(schema, indent=2)}"
        )
        body = self._build_body(model=model, system=system, user_content=contents,
                                schema=schema, schema_name=schema_name)
        last_parse_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "TurboQuant malformed JSON for %s (attempt %d/%d), retrying in %.0fs",
                    schema_name, attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            try:
                thinking_buf, output_text = await self._stream_with_retry(body, schema_name, on_thinking)
                if thinking_buf:
                    logger.info("turboquant think (%s): %s", schema_name, thinking_buf[:300])
                logger.info(
                    "turboquant call: model=%s schema=%s sys_chars=%d user_chars=%d",
                    model, schema_name, len(system), len(contents),
                )
                return self._parse_output_object(output_text, schema_name)
            except RuntimeError as exc:
                if "not valid JSON" in str(exc) or "must be a JSON object" in str(exc):
                    last_parse_exc = exc
                    continue
                raise
        assert last_parse_exc is not None
        raise last_parse_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> str:
        contents = json.dumps(user_payload)
        body = self._build_body(model=model, system=system_instructions, user_content=contents)
        _, raw_text = await self._stream_with_retry(body, "text", on_thinking)
        logger.info(
            "turboquant call: model=%s schema=text sys_chars=%d user_chars=%d",
            model, len(system_instructions), len(contents),
        )
        clean = self._profile.post_process_text(raw_text)
        return clean or raw_text  # guard against stripping everything

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_body(
        self,
        *,
        model: str,
        system: str,
        user_content: str,
        schema: dict[str, object] | None = None,
        schema_name: str = "",
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": self._profile.temperature,
            "top_p": self._profile.top_p,
            "top_k": self._profile.top_k,
            "min_p": self._profile.min_p,
            "presence_penalty": self._profile.presence_penalty,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        self._profile.augment_body(body)
        if schema is not None:
            # llama.cpp applies a JSON-schema GBNF grammar ONLY when thinking is OFF;
            # with thinking enabled it silently disables grammar enforcement
            # (ggml-org/llama.cpp#20345), so fall back to loose json_object there.
            # With thinking off, the strict grammar makes malformed/prose output
            # IMPOSSIBLE at the token level — and it hard-enforces the per-turn
            # SM-filtered `type`/`tool` enums, not just as a prompt hint.
            if self._strict_json and self._profile.thinking_budget == 0:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name or "response",
                        "schema": schema,
                        "strict": True,
                    },
                }
            else:
                body["response_format"] = {"type": "json_object"}
        return body

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        # Profile cleans model-specific artifacts (think blocks, etc.) first.
        text = self._profile.post_process_text(output_text.strip())
        text = text or output_text.strip()
        return _extract_json_object(text, schema_name)

    async def _stream_with_retry(
        self,
        body: dict[str, object],
        schema_name: str,
        on_thinking: Callable[[str], None] | None,
    ) -> tuple[str, str]:
        """Stream a completion. Returns (thinking_content, output_content)."""
        stream_body = {**body, "stream": True}
        url = f"{self._host}/v1/chat/completions"
        timeout = httpx.Timeout(connect=10.0, read=_DEFAULT_CHUNK_TIMEOUT, write=10.0, pool=5.0)
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning("TurboQuant stream retry %d/%d in %.0fs",
                               attempt, self._max_retries, delay)
                await asyncio.sleep(delay)

            try:
                thinking_parts: list[str] = []
                content_parts: list[str] = []
                async with self._client.stream("POST", url, json=stream_body,
                                               timeout=timeout) as response:
                    if response.status_code in _RETRYABLE_STATUS_CODES:
                        last_exc = RuntimeError(f"TurboQuant returned {response.status_code}")
                        continue
                    if response.status_code >= 400:
                        body_text = (await response.aread()).decode("utf-8", errors="replace")
                        raise RuntimeError(
                            f"TurboQuant returned {response.status_code}: {body_text[:500]}"
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        # llama.cpp's final stream chunk carries prefix-cache telemetry
                        # (choices empty here): prompt_n = tokens actually evaluated this
                        # turn, cache_n = tokens reused from the KV prefix cache. Logging
                        # this makes KV-cache reuse measurable instead of assumed.
                        if tm := chunk.get("timings"):
                            logger.info(
                                "turboquant timings: schema=%s prompt_n=%s cache_n=%s "
                                "prompt_ms=%.0f predicted_n=%s",
                                schema_name, tm.get("prompt_n"), tm.get("cache_n"),
                                tm.get("prompt_ms", 0.0), tm.get("predicted_n"),
                            )
                        choices = chunk.get("choices")
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if reasoning := delta.get("reasoning_content"):
                            thinking_parts.append(reasoning)
                            if on_thinking:
                                on_thinking(reasoning)
                        if text := delta.get("content"):
                            content_parts.append(text)
                return "".join(thinking_parts), "".join(content_parts)

            except httpx.ReadTimeout as exc:
                last_exc = RuntimeError(
                    f"TurboQuant stream: no chunk for {_DEFAULT_CHUNK_TIMEOUT:.0f}s "
                    f"({schema_name})"
                )
                logger.warning("TurboQuant stream read timeout for %s: %s", schema_name, exc)
                continue
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                continue

        assert last_exc is not None
        raise RuntimeError(
            f"TurboQuant stream failed after {self._max_retries} retries: {last_exc}"
        ) from last_exc


# ---------------------------------------------------------------------------
# Module-level pure functions (no model-specific knowledge)
# ---------------------------------------------------------------------------

def _repair_json(text: str) -> str:
    """Repair known llama-server JSON malformations before parsing."""
    # Repair 1: model omits "args" key before the args object.
    # {..., "tool": "read_file", {"path": ...}} → {..., "tool": "read_file", "args": {"path": ...}}
    text = re.sub(
        r'("tool"\s*:\s*"[^"]*"\s*),\s*(\{)',
        r'\1, "args": \2',
        text,
    )
    # Repair 2: unquoted property names (seen in Qwen3 no-think mode).
    # {type: "tool_call", ...} → {"type": "tool_call", ...}
    text = re.sub(
        r'([{,]\s*)([a-zA-Z_]\w*)(\s*:(?!\s*/))',
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        text,
    )
    return text


def _extract_json_object(text: str, schema_name: str) -> dict[str, object]:
    """Extract and parse the first JSON object from text; attempt repair on failure."""
    start = text.find("{")
    if start == -1:
        raise RuntimeError(
            f"TurboQuant output contains no JSON object for {schema_name}: {text[:500]}"
        )
    text = text[start:]
    try:
        # raw_decode stops after the first complete value — ignores trailing garbage.
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        repaired = _repair_json(text)
        if repaired == text:
            raise RuntimeError(
                f"TurboQuant output is not valid JSON for {schema_name}: {text[:500]}"
            )
        logger.warning("TurboQuant malformed JSON for %s — applied repair", schema_name)
        try:
            payload, _ = json.JSONDecoder().raw_decode(repaired)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"TurboQuant output is not valid JSON for {schema_name} "
                f"(after repair): {repaired[:500]}"
            ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("TurboQuant output must be a JSON object")
    return payload
