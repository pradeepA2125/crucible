from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from openai import AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport
from agentd.runtime.artifacts import provider_debug_root

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503})


def _is_reasoning_model(model: str) -> bool:
    """True for models that support OpenRouter's reasoning extension (extra_body.reasoning).

    Covers DeepSeek-R1 family and Qwen3 family (including qwen3-coder).
    openrouter/free is intentionally excluded — it routes to whatever is available
    and reasoning params cause it to return empty choices.
    """
    m = model.lower()
    return any(x in m for x in ("deepseek-r1", "deepseek-r2", "qwen3"))


def _is_retryable(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES


class OpenRouterJsonTransport(ModelJsonTransport):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str | None = None,
        site_name: str | None = None,
        max_tokens: int = 4096,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        completions_client: Any | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)

        if completions_client is not None:
            self._completions: Any = completions_client
            return

        resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not resolved_api_key:
            msg = "OPENROUTER_API_KEY is required for OpenRouterJsonTransport"
            raise RuntimeError(msg)

        resolved_site_url = site_url or os.getenv("AI_EDITOR_OPENROUTER_SITE_URL")
        resolved_site_name = site_name or os.getenv("AI_EDITOR_OPENROUTER_SITE_NAME")
        extra_headers: dict[str, str] = {}
        if resolved_site_url:
            extra_headers["HTTP-Referer"] = resolved_site_url
        if resolved_site_name:
            extra_headers["X-Title"] = resolved_site_name

        client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=base_url,
            timeout=timeout_sec,
            default_headers=extra_headers or None,
        )
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
    ) -> dict[str, object]:
        safe_schema_name = "".join(c for c in schema_name if c.isalnum())

        # Reasoning models require temperature=1 per OpenRouter docs.
        temperature = 1 if _is_reasoning_model(model) else 0

        base_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "max_completion_tokens": self._max_tokens,
            "temperature": temperature,
        }
        if _is_reasoning_model(model):
            base_kwargs["extra_body"] = {"reasoning": {"enabled": True}}

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

        out_dir = provider_debug_root("openrouter")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"debug-req-{safe_schema_name}.json").write_text(
                json.dumps(create_kwargs, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

        try:
            response = await self._call_with_retry(create_kwargs)
            output_text = self._extract_text(response)
            return self._parse_output_object(output_text, schema_name)
        except Exception as e:
            # Fall back to json_object with schema injected into system prompt.
            # Some models/providers don't support json_schema strict mode.
            logger.warning(
                "OpenRouter json_schema failed for %s, falling back to json_object: %s",
                schema_name, e,
            )
            fallback_kwargs: dict[str, Any] = {
                **base_kwargs,
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
            last_parse_exc: Exception | None = None
            for attempt in range(self._max_retries + 1):
                if attempt > 0:
                    delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                    logger.warning(
                        "OpenRouter malformed JSON for %s (attempt %d/%d), retrying in %.0fs",
                        schema_name, attempt, self._max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                try:
                    response = await self._call_with_retry(fallback_kwargs)
                    output_text = self._extract_text(response)
                    return self._parse_output_object(output_text, schema_name)
                except RuntimeError as e2:
                    if "not valid JSON" in str(e2) or "must be a JSON object" in str(e2):
                        last_parse_exc = e2
                        continue
                    raise RuntimeError(
                        f"OpenRouter API error for {schema_name} (fallback also failed): {e2}"
                    ) from e2
                except Exception as e2:
                    raise RuntimeError(
                        f"OpenRouter API error for {schema_name} (fallback also failed): {e2}"
                    ) from e2
            assert last_parse_exc is not None
            raise RuntimeError(
                f"OpenRouter API error for {schema_name} (fallback malformed JSON after retries): {last_parse_exc}"
            ) from last_parse_exc

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
    ) -> str:
        temperature = 1 if _is_reasoning_model(model) else 0

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            "max_completion_tokens": self._max_tokens,
            "temperature": temperature,
        }
        if _is_reasoning_model(model):
            create_kwargs["extra_body"] = {"reasoning": {"enabled": True}}

        if callable(on_thinking):
            return await self._stream_with_thinking(create_kwargs, on_thinking=on_thinking)

        try:
            response = await self._call_with_retry(create_kwargs)
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"OpenRouter API error: {e}") from e

    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
    ) -> str:
        """Stream response forwarding reasoning chunks to on_thinking callback.

        OpenRouter surfaces reasoning in delta.reasoning (same field as Groq).
        """
        kwargs = {**create_kwargs, "stream": True}
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "OpenRouter transient error (attempt %d/%d), retrying in %.0fs",
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
                raise RuntimeError(
                    f"OpenRouter streaming timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    async def _call_with_retry(self, create_kwargs: dict[str, Any]) -> Any:
        """Call chat.completions.create with timeout and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "OpenRouter transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
            try:
                return await asyncio.wait_for(
                    self._completions.create(**create_kwargs),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                raise RuntimeError(
                    f"OpenRouter chat.completions timed out after {self._timeout_sec}s"
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
                logger.warning("OpenRouter response missing choices — full response: %s", raw)
            except Exception:
                logger.warning("OpenRouter response missing choices — response: %r", response)
            raise RuntimeError("OpenRouter response missing choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter response contained no text output")
        return content.strip()

    def _parse_output_object(self, output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = _strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenRouter output is not valid JSON for {schema_name}: {output_text[:500]}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("OpenRouter output must be a JSON object")
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
