from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

try:
    from groq import AsyncGroq as AsyncGroqClient
except ImportError:
    AsyncGroqClient = None

from agentd.providers.contracts import ModelJsonTransport
from agentd.runtime.artifacts import provider_debug_root

logger = logging.getLogger(__name__)

# Status codes that indicate transient server-side pressure — safe to retry.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503})


def _is_gpt_oss(model: str) -> bool:
    """True for models that use include_reasoning / reasoning_effort (not reasoning_format).

    Covers DeepSeek and OpenAI GPT-OSS families served through Groq.
    """
    m = model.lower()
    return "deepseek" in m or "gpt-oss" in m


def _is_retryable(exc: Exception) -> bool:
    """Return True for transient Groq errors (503 high demand, 429 rate limit)."""
    # Groq SDK raises groq.APIStatusError with a .status_code attribute.
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES:
        return True
    return False


class GroqJsonTransport(ModelJsonTransport):
    supports_anyof_grammar: bool = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        max_tokens: int = 4096,
        timeout_sec: float = 60.0,
        max_retries: int = 4,
        reasoning_effort: str | None = None,
        completions_client: Any | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        self._reasoning_effort = reasoning_effort or os.getenv(
            "CRUCIBLE_GROQ_REASONING_EFFORT", "high"
        )

        if completions_client is not None:
            self._completions: Any = completions_client
            return

        resolved_api_key = api_key or os.getenv("GROQ_API_KEY")
        if not resolved_api_key:
            msg = "GROQ_API_KEY is required for GroqJsonTransport"
            raise RuntimeError(msg)
        if AsyncGroqClient is None:
            msg = "groq package is required for GroqJsonTransport"
            raise RuntimeError(msg)

        client_kwargs: dict[str, Any] = {"api_key": resolved_api_key}
        if endpoint:
            client_kwargs["base_url"] = endpoint.rstrip("/")

        client = AsyncGroqClient(**client_kwargs)
        self._completions = client.chat.completions

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
        on_retry: object = None,
    ) -> dict[str, object]:
        # Normalize schema name to alphanumeric for Groq
        safe_schema_name = "".join(c for c in schema_name if c.isalnum())

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": safe_schema_name,
                    "schema": schema,
                },
            },
            "max_completion_tokens": self._max_tokens,
            "temperature": 0,
        }
        if _is_gpt_oss(model):
            create_kwargs["include_reasoning"] = False
            if self._reasoning_effort:
                create_kwargs["reasoning_effort"] = self._reasoning_effort

        # Debug: dump request
        out_dir = provider_debug_root("groq")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"debug-req-{safe_schema_name}.json").write_text(
                json.dumps(create_kwargs, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

        last_parse_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Groq malformed JSON for %s (attempt %d/%d), retrying in %.0fs",
                    schema_name, attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            try:
                response = await self._call_with_retry(create_kwargs)
                output_text = self._extract_text(response)
                return self._parse_output_object(output_text, schema_name)
            except RuntimeError as exc:
                if "not valid JSON" in str(exc) or "must be a JSON object" in str(exc):
                    last_parse_exc = exc
                    continue
                raise
            except Exception as exc:
                if hasattr(exc, "body"):
                    try:
                        (out_dir / f"debug-err-{safe_schema_name}.json").write_text(
                            json.dumps(exc.body, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
                raise
        assert last_parse_exc is not None
        raise last_parse_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
    ) -> str:
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "max_completion_tokens": self._max_tokens,
            "temperature": 0,
        }
        if self._reasoning_effort and _is_gpt_oss(model):
            create_kwargs["reasoning_effort"] = self._reasoning_effort

        if callable(on_thinking):
            return await self._stream_with_thinking(create_kwargs, model=model, on_thinking=on_thinking)

        try:
            response = await self._call_with_retry(create_kwargs)
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"Groq API error: {e}") from e

    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        model: str,
        on_thinking: Any,
    ) -> str:
        """Stream response, calling on_thinking(chunk) for each reasoning chunk.

        Uses reasoning_format='parsed' (Qwen-family) or include_reasoning=True
        (DeepSeek/GPT-OSS) so thinking arrives in delta.reasoning, answer in
        delta.content.  Falls back gracefully if neither field is present.
        """
        kwargs = dict(create_kwargs)
        if _is_gpt_oss(model):
            kwargs["include_reasoning"] = True
        elif "qwen" in model.lower():
            kwargs["reasoning_format"] = "parsed"
        kwargs["stream"] = True

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Groq transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            try:
                content_parts: list[str] = []
                stream = await asyncio.wait_for(
                    self._completions.create(**kwargs),
                    timeout=self._timeout_sec,
                )
                async for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    reasoning = getattr(delta, "reasoning", None)
                    if reasoning:
                        on_thinking(reasoning)
                    content = getattr(delta, "content", None) or ""
                    if content:
                        content_parts.append(content)
                return "".join(content_parts).strip()
            except TimeoutError as exc:
                msg = f"Groq streaming timed out after {self._timeout_sec}s"
                raise RuntimeError(msg) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    async def _call_with_retry(self, create_kwargs: dict[str, Any]) -> Any:
        """Call chat.completions.create with timeout and exponential backoff for transient errors."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Groq transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)

            try:
                return await asyncio.wait_for(
                    self._completions.create(**create_kwargs),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                msg = f"Groq chat.completions timed out after {self._timeout_sec}s"
                raise RuntimeError(msg) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    def _extract_text(self, response: Any) -> str:
        choices = read_value(response, "choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Groq response missing choices")

        first_choice = choices[0]
        message = read_value(first_choice, "message")
        content = read_value(message, "content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq response contained no text output")
        return content.strip()

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            msg = f"Groq output is not valid JSON for {schema_name}: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            msg = "Groq output must be a JSON object"
            raise RuntimeError(msg)

        return payload


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
