from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

try:
    from google import genai as google_genai
    from google.genai import errors as google_genai_errors
except ImportError:
    google_genai = None
    google_genai_errors = None  # type: ignore[assignment]

from agentd.providers.contracts import ModelJsonTransport

logger = logging.getLogger(__name__)

# Status codes that indicate transient server-side pressure — safe to retry.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503})


def _is_retryable(exc: Exception) -> bool:
    """Return True for transient Gemini errors (503 high demand, 429 rate limit)."""
    if google_genai_errors is not None:
        server_error = getattr(google_genai_errors, "ServerError", None)
        client_error = getattr(google_genai_errors, "ClientError", None)
        for cls in (server_error, client_error):
            if cls is not None and isinstance(exc, cls):
                # SDK stores HTTP status as .code (not .status_code)
                status_code = getattr(exc, "code", None)
                if isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES:
                    return True
    return False


class GeminiJsonTransport(ModelJsonTransport):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        thinking_enabled: bool = False,
        thinking_budget: int | None = None,
        thinking_level: str | None = None,
        include_thoughts: bool = False,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        models_client: Any | None = None,
    ) -> None:
        self._client: Any | None = None
        self._thinking_enabled = thinking_enabled
        self._thinking_budget = thinking_budget
        self._thinking_level = normalize_thinking_level(thinking_level)
        self._include_thoughts = include_thoughts
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        # Gemini now accepts a JSON-schema `oneOf` discriminated union via the newer
        # `response_json_schema` field (verified live against gemini-3.1-flash-lite-preview:
        # the tight controller schema is accepted as-is and yields valid constrained output).
        # The older "Gemini deadlocks on oneOf" note predates response_json_schema. Enabling
        # this routes the controller to the TIGHT schema, which fixes the flat-schema failure
        # mode where Gemini emitted tool_calls missing required fields.
        self.supports_oneof_grammar: bool = True
        if models_client is not None:
            self._models: Any = models_client
            return

        resolved_api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not resolved_api_key:
            msg = "GEMINI_API_KEY (or GOOGLE_API_KEY) is required for GeminiJsonTransport"
            raise RuntimeError(msg)
        if google_genai is None:
            msg = "google-genai package is required for GeminiJsonTransport"
            raise RuntimeError(msg)

        client = google_genai.Client(api_key=resolved_api_key)
        # Keep a strong reference to the SDK client for the transport lifetime.
        # The async models handle is backed by this client and can fail if it is collected/closed.
        self._client = client
        self._models = client.aio.models

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
    ) -> dict[str, object]:
        config: dict[str, object] = {
            "temperature": 0,
            "system_instruction": system_instructions,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
        }
        thinking_config = self._build_thinking_config()
        if thinking_config is not None:
            if callable(on_thinking):
                # include_thoughts required so the stream contains thought parts
                thinking_config["include_thoughts"] = True
            config["thinking_config"] = thinking_config

        contents = json.dumps(user_payload)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Gemini malformed JSON (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            try:
                if callable(on_thinking) and self._thinking_enabled:
                    response = await self._stream_with_thinking(
                        model=model, contents=contents, config=config, on_thinking=on_thinking
                    )
                else:
                    response = await self._call_with_retry(model=model, contents=contents, config=config)
                self._log_usage(model, schema_name, system_instructions, contents, response)
                output_text = self._extract_text(response)
                return self._parse_output_object(output_text, schema_name)
            except RuntimeError as exc:
                if "not valid JSON" in str(exc) or "must be a JSON object" in str(exc):
                    last_exc = exc
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,  # noqa: ARG002 — not yet used for text calls
    ) -> str:
        config: dict[str, object] = {
            "temperature": 0,
            "system_instruction": system_instructions,
        }
        thinking_config = self._build_thinking_config()
        if thinking_config is not None:
            config["thinking_config"] = thinking_config

        contents = json.dumps(user_payload)
        response = await self._call_with_retry(model=model, contents=contents, config=config)
        self._log_usage(model, "text", system_instructions, contents, response)
        return self._extract_text(response)

    @staticmethod
    def _log_usage(
        model: str,
        schema_name: str,
        system_instructions: str,
        contents: str,
        response: Any,
    ) -> None:
        """Log per-call token + char usage for context-length analysis."""
        usage = read_value(response, "usage_metadata")
        prompt_tokens = read_value(usage, "prompt_token_count") if usage is not None else None
        cand_tokens = read_value(usage, "candidates_token_count") if usage is not None else None
        total_tokens = read_value(usage, "total_token_count") if usage is not None else None
        logger.info(
            "gemini call: model=%s schema=%s sys_chars=%d user_chars=%d "
            "prompt_tokens=%s output_tokens=%s total_tokens=%s",
            model, schema_name,
            len(system_instructions), len(contents),
            prompt_tokens, cand_tokens, total_tokens,
        )

    async def _stream_with_thinking(
        self,
        *,
        model: str,
        contents: str,
        config: dict[str, object],
        on_thinking: Any,
    ) -> Any:
        """Stream response, calling on_thinking(chunk) for each thinking part.

        Returns a synthetic response object with a .text attribute so callers
        can use the same _extract_text / _log_usage path as non-streaming calls.
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Gemini transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            try:
                thinking_buf: list[str] = []
                output_buf: list[str] = []
                async for chunk in await asyncio.wait_for(
                    self._models.generate_content_stream(
                        model=model, contents=contents, config=config
                    ),
                    timeout=self._timeout_sec,
                ):
                    # Extract parts to separate thinking from output
                    candidates = read_value(chunk, "candidates") or []
                    for cand in (candidates if isinstance(candidates, list) else [candidates]):
                        content = read_value(cand, "content")
                        parts = read_value(content, "parts") or []
                        for part in (parts if isinstance(parts, list) else []):
                            text = read_value(part, "text") or ""
                            if not text:
                                continue
                            if read_value(part, "thought"):
                                thinking_buf.append(text)
                                on_thinking(text)
                            else:
                                output_buf.append(text)

                # Return a simple object that _extract_text / _log_usage can consume
                class _FakeResponse:
                    text = "".join(output_buf)
                    usage_metadata = None

                return _FakeResponse()
            except TimeoutError as exc:
                msg = f"Gemini streaming timed out after {self._timeout_sec}s (model={model})"
                raise RuntimeError(msg) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def _call_with_retry(self, *, model: str, contents: str, config: dict[str, object]) -> Any:
        """Call generate_content with timeout and exponential backoff for transient errors."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                # Exponential backoff: 5s, 10s, 20s, 40s … capped at 60s
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Gemini transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)

            try:
                return await asyncio.wait_for(
                    self._models.generate_content(model=model, contents=contents, config=config),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                msg = f"Gemini generate_content timed out after {self._timeout_sec}s (model={model})"
                raise RuntimeError(msg) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    def _extract_text(self, response: Any) -> str:
        text = read_value(response, "text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        raise RuntimeError("Gemini response contained no text output")

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            msg = f"Gemini output is not valid JSON for {schema_name}: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            msg = "Gemini output must be a JSON object"
            raise RuntimeError(msg)

        return payload

    def _build_thinking_config(self) -> dict[str, object] | None:
        if not self._thinking_enabled:
            return None

        thinking_config: dict[str, object] = {}
        if self._thinking_budget is not None:
            thinking_config["thinking_budget"] = self._thinking_budget
        if self._thinking_level is not None:
            thinking_config["thinking_level"] = self._thinking_level
        if self._include_thoughts:
            thinking_config["include_thoughts"] = True

        if not thinking_config:
            # Dynamic thinking budget when enabled but no explicit params were set.
            thinking_config["thinking_budget"] = -1

        return thinking_config


def strip_json_code_fences(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw

    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def read_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def normalize_thinking_level(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return normalized
