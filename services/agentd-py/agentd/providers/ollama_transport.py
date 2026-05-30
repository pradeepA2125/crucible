"""Ollama transport — local LLM inference, OpenAI-style protocol.

Mirrors the GeminiJsonTransport feature set:
- schema-constrained JSON via Ollama's `format=<JSON Schema>` (Ollama 0.5+)
- system instructions
- exponential-backoff retry on transient errors
- per-request timeout
- per-call usage logging (prompt_eval_count, eval_count, total_duration)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from agentd.providers.contracts import ModelJsonTransport

logger = logging.getLogger(__name__)

# HTTP statuses that warrant a retry (Ollama itself doesn't rate-limit since it's
# local, but a model load or request flood can transiently 503; 5xx in general).
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Network errors that warrant a retry (Ollama may be reloading a model, etc.).
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

_DEFAULT_HOST = "http://localhost:11434"




class OllamaJsonTransport(ModelJsonTransport):
    """JSON transport backed by a local Ollama server."""

    def __init__(
        self,
        *,
        host: str | None = None,
        keep_alive: str | None = None,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._host = (host or os.getenv("OLLAMA_HOST") or _DEFAULT_HOST).rstrip("/")
        self._keep_alive = keep_alive
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout_sec)

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
        on_thinking: object = None,
    ) -> dict[str, object]:
        contents = json.dumps(user_payload)
        user_content = contents
        body = self._build_body(
            model=model,
            system=system_instructions,
            user_content=user_content,
            json_format=schema,
            num_predict=-1,  # unlimited — thinking tokens can be large
        )
        response = await self._call_with_retry(body)
        self._log_usage(model, schema_name, system_instructions, contents, response)
        output_text = self._extract_text(response)
        logger.warning("ollama raw output (%s): %s", schema_name, output_text[:600])
        return self._parse_output_object(output_text, schema_name)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        contents = json.dumps(user_payload)
        body = self._build_body(
            model=model,
            system=system_instructions,
            user_content=contents,
            json_format=None,
            num_predict=2048,
        )
        response = await self._call_with_retry(body)
        self._log_usage(model, "text", system_instructions, contents, response)
        return self._extract_text(response)

    def _build_body(
        self,
        *,
        model: str,
        system: str,
        user_content: str,
        json_format: dict[str, object] | None,
        num_predict: int = -1,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            # num_ctx: context window (input + output tokens).
            # num_predict: max output tokens (-1 = no limit). For structured-output
            # calls, thinking models like qwen3 can consume thousands of tokens on
            # implicit reasoning before emitting the JSON response, so callers pass
            # a larger budget here.  Text generation calls cap at 4096 to avoid
            # runaway responses.
            "options": {"temperature": 0, "num_ctx": 32768, "num_predict": num_predict},
        }
        if json_format is not None:
            # Ollama 0.5+ accepts a full JSON Schema in `format`; older versions
            # accept the literal string "json".
            body["format"] = json_format
        if self._keep_alive is not None:
            body["keep_alive"] = self._keep_alive
        return body

    async def _call_with_retry(self, body: dict[str, object]) -> dict[str, Any]:
        """POST /api/chat with timeout + exponential backoff on transient errors."""
        url = f"{self._host}/api/chat"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Ollama transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                await asyncio.sleep(delay)

            try:
                response = await asyncio.wait_for(
                    self._client.post(url, json=body),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                msg = f"Ollama request timed out after {self._timeout_sec}s (model={body.get('model')})"
                raise RuntimeError(msg) from exc
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                continue
            except Exception:
                raise

            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_exc = httpx.HTTPStatusError(
                    f"Ollama returned {response.status_code}: {response.text[:200]}",
                    request=response.request,
                    response=response,
                )
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Ollama returned {response.status_code}: {response.text[:500]}"
                )
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Ollama returned non-JSON body: {response.text[:200]}"
                ) from exc

        assert last_exc is not None
        raise RuntimeError(
            f"Ollama request failed after {self._max_retries} retries: {last_exc}"
        ) from last_exc

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        message = response.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            logger.warning(
                "ollama: empty message.content; message keys=%s content=%r",
                list(message.keys()),
                content,
            )
        # /api/generate fallback shape (just in case)
        text = response.get("response")
        if isinstance(text, str) and text.strip():
            return text.strip()
        raise RuntimeError("Ollama response contained no text content")

    @staticmethod
    def _parse_output_object(output_text: str, schema_name: str) -> dict[str, object]:
        payload_text = strip_json_code_fences(output_text)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            msg = f"Ollama output is not valid JSON for {schema_name}: {output_text[:500]}"
            raise RuntimeError(msg) from exc

        if not isinstance(payload, dict):
            msg = "Ollama output must be a JSON object"
            raise RuntimeError(msg)

        return payload

    @staticmethod
    def _log_usage(
        model: str,
        schema_name: str,
        system_instructions: str,
        contents: str,
        response: dict[str, Any],
    ) -> None:
        prompt_tokens = response.get("prompt_eval_count")
        output_tokens = response.get("eval_count")
        total_duration_ns = response.get("total_duration")
        total_duration_ms = (
            int(total_duration_ns / 1_000_000) if isinstance(total_duration_ns, int) else None
        )
        total_tokens = (
            (prompt_tokens or 0) + (output_tokens or 0)
            if isinstance(prompt_tokens, int) and isinstance(output_tokens, int)
            else None
        )
        logger.info(
            "ollama call: model=%s schema=%s sys_chars=%d user_chars=%d "
            "prompt_tokens=%s output_tokens=%s total_tokens=%s duration_ms=%s",
            model, schema_name,
            len(system_instructions), len(contents),
            prompt_tokens, output_tokens, total_tokens, total_duration_ms,
        )


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
