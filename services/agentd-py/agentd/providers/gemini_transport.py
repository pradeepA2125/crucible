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
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 503})


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
    ) -> dict[str, object]:
        config: dict[str, object] = {
            "temperature": 0,
            "system_instruction": system_instructions,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
        }
        thinking_config = self._build_thinking_config()
        if thinking_config is not None:
            config["thinking_config"] = thinking_config

        response = await self._call_with_retry(model=model, contents=json.dumps(user_payload), config=config)
        output_text = self._extract_text(response)
        return self._parse_output_object(output_text, schema_name)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        config: dict[str, object] = {
            "temperature": 0,
            "system_instruction": system_instructions,
        }
        thinking_config = self._build_thinking_config()
        if thinking_config is not None:
            config["thinking_config"] = thinking_config

        response = await self._call_with_retry(model=model, contents=json.dumps(user_payload), config=config)
        return self._extract_text(response)

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
