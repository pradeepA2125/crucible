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
import re
from collections.abc import Callable
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


class _RetryableHttpStatus(Exception):
    """Internal marker: an HTTP status Ollama treats as transiently retryable,
    raised from inside the streamed-response context manager and caught by
    _call_with_retry's loop (mirrors the pre-streaming code's plain status check,
    which can't be a simple if/continue anymore once the check lives inside a
    separate `async with self._client.stream(...)` coroutine)."""




class OllamaJsonTransport(ModelJsonTransport):
    """JSON transport backed by a local Ollama server."""

    # Ollama passes `format` directly to llama-server's json_schema field — the
    # identical GBNF path as TurboQuant. llama.cpp's GBNF converter enforces oneOf
    # cleanly (no deadlock, zero cross-variant bleed), same as measured for TurboQuant.
    supports_oneof_grammar: bool = True

    def __init__(
        self,
        *,
        host: str | None = None,
        keep_alive: str | None = None,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        num_ctx: int = 32768,
        json_predict_frac: float = 0.5,
        think: bool | str | None = None,
        temperature: float = 0.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._host = (host or os.getenv("OLLAMA_HOST") or _DEFAULT_HOST).rstrip("/")
        self._keep_alive = keep_alive
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        # temperature=0 (greedy decoding) is deterministic — a retry after a
        # malformed/empty response tends to reproduce a similar failure since the
        # input barely changed (just a correction appended). Configurable so a
        # retry-heavy path (e.g. controller_loop's consecutive_malformed correction)
        # has a real chance of sampling something different instead of grinding the
        # same failure. Default 0 preserves prior behavior.
        self._temperature = temperature
        # think is Ollama's actual lever for reasoning length (a top-level request
        # field, bool or a level string "low"/"medium"/"high"/"max" depending on the
        # model) — unlike num_predict, it isn't a shared token pool with the output, so
        # it's the closest analog here to Codex's separate reasoning_effort dial.
        # Default None omits the field entirely (today's behavior, model decides) —
        # this repo previously ripped a blanket think=False out because qwen3 ignores
        # the flag and emits implicit thinking regardless, so this stays opt-in per
        # deployment rather than a default that could silently do nothing for one
        # model family while working for another.
        self._think = think
        # num_ctx bounds prompt + output combined, so num_predict must leave headroom
        # for the prompt rather than equal num_ctx outright (the pre-fix flat 32768 for
        # both meant the model's real output budget was already num_ctx minus whatever
        # the prompt consumed — tighter than it looked). json_predict_frac is a fraction
        # of num_ctx, not an absolute token count, so raising num_ctx for a cloud model
        # with a bigger window (e.g. Nemotron) grows the output budget proportionally
        # instead of needing a second hardcoded constant kept in sync by hand.
        self._num_ctx = max(1, num_ctx)
        self._json_num_predict = max(1, int(self._num_ctx * json_predict_frac))
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
        on_thinking: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        contents = json.dumps(user_payload)
        body = self._build_body(
            model=model,
            system=system_instructions,
            user_content=contents,
            json_format=schema,
            num_predict=self._json_num_predict,  # cloud backends reject -1 (unlimited)
        )
        # on_chunk forwards each streamed `message.thinking` delta live, as it
        # arrives — this is what lets the UI show real progress during a call that
        # can take minutes, instead of a blank "Working…" wait (see _stream_chat).
        response = await self._call_with_retry(body, on_chunk=on_thinking)
        self._log_usage(model, schema_name, system_instructions, contents, response)
        output_text = self._extract_text(response)
        logger.warning("ollama raw output (%s): %s", schema_name, output_text[:600])

        # Strip <think> blocks that some models (e.g. Qwen3) may emit INLINE in
        # content rather than the structured message.thinking field streamed above
        # — a separate signal, so this can still fire in addition to on_chunk.
        thinking, output_text = _split_thinking(output_text)
        if thinking and on_thinking:
            on_thinking(thinking)

        return _parse_output_object(output_text, schema_name)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
    ) -> str:
        contents = json.dumps(user_payload)
        body = self._build_body(
            model=model,
            system=system_instructions,
            user_content=contents,
            json_format=None,
            num_predict=2048,
        )
        response = await self._call_with_retry(body, on_chunk=on_thinking)
        self._log_usage(model, "text", system_instructions, contents, response)
        raw = self._extract_text(response)
        thinking, text = _split_thinking(raw)
        if thinking and on_thinking:
            on_thinking(thinking)
        return text or raw

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
            # stream: True — see _stream_chat. Chunks are merged into a single
            # response dict shaped like the old non-streaming body, so
            # _extract_text/_log_usage are unaffected; the point is on_chunk firing
            # live per `message.thinking` delta instead of the whole call being a
            # blank wait until it completes (which can take minutes — see
            # ollama_call: duration_ms logged live, 2026-07-13 campaign found a
            # single call taking 191s with zero UI feedback the whole time).
            "stream": True,
            # num_ctx: context window (input + output tokens combined), configurable
            # per-instance so a cloud model with a bigger window (e.g. Nemotron) can be
            # given more room without a code change.
            # num_predict: max output tokens (-1 = no limit). For structured-output
            # calls, thinking models like qwen3/Nemotron can consume thousands of
            # tokens on implicit reasoning before emitting the JSON response, so the
            # JSON path uses a fraction of num_ctx (self._json_num_predict) rather than
            # a flat constant — it scales with num_ctx instead of needing to be kept in
            # sync by hand, and leaves headroom below num_ctx for the prompt itself.
            # Text generation calls cap at a small fixed budget to avoid runaway
            # responses.
            "options": {
                "temperature": self._temperature,
                "num_ctx": self._num_ctx,
                "num_predict": num_predict,
            },
        }
        if json_format is not None:
            # Ollama 0.5+ accepts a full JSON Schema in `format`; older versions
            # accept the literal string "json".
            body["format"] = json_format
        if self._keep_alive is not None:
            body["keep_alive"] = self._keep_alive
        if self._think is not None:
            # Top-level field (not inside `options`) per Ollama's chat API — see
            # __init__ for why this defaults to omitted rather than a blanket False.
            body["think"] = self._think
        return body

    async def _call_with_retry(
        self, body: dict[str, object], *, on_chunk: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """POST /api/chat (streamed) with timeout + exponential backoff on transient
        errors. See _stream_chat for the line-parsing/merge; on_chunk is threaded
        through so callers (generate_json/generate_text) get live thinking deltas."""
        url = f"{self._host}/api/chat"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Ollama transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                # A transient-error retry cycle can run for minutes (a 429 storm
                # is 4 attempts x up to 60s backoff each) with the UI otherwise
                # showing nothing — reuse the thinking-chunk channel so the user
                # sees a retry is happening rather than a stuck-looking silence.
                if on_chunk is not None:
                    on_chunk(
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                await asyncio.sleep(delay)

            try:
                return await asyncio.wait_for(
                    self._stream_chat(url, body, on_chunk),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                msg = f"Ollama request timed out after {self._timeout_sec}s (model={body.get('model')})"
                raise RuntimeError(msg) from exc
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                continue
            except _RetryableHttpStatus as exc:
                last_exc = exc
                continue
            except Exception:
                raise

        assert last_exc is not None
        raise RuntimeError(
            f"Ollama request failed after {self._max_retries} retries: {last_exc}"
        ) from last_exc

    async def _stream_chat(
        self,
        url: str,
        body: dict[str, object],
        on_chunk: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        """Consume Ollama's streamed /api/chat response: newline-delimited JSON,
        each line a partial {message: {content, thinking}, done} object. Chunks are
        merged into a single dict shaped like the pre-streaming non-streaming
        response (content/thinking concatenated, final chunk's usage stats kept),
        so _extract_text/_log_usage downstream are unchanged. on_chunk (when given)
        fires with each individual `message.thinking` delta AS IT ARRIVES — this is
        the actual point of streaming: live progress during a call that can take
        minutes, instead of the caller seeing nothing until the whole thing lands."""
        async with self._client.stream("POST", url, json=body) as response:
            if response.status_code in _RETRYABLE_STATUS_CODES:
                text = (await response.aread()).decode("utf-8", errors="replace")
                raise _RetryableHttpStatus(f"Ollama returned {response.status_code}: {text[:200]}")
            if response.status_code >= 400:
                text = (await response.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"Ollama returned {response.status_code}: {text[:500]}")

            content_parts: list[str] = []
            thinking_parts: list[str] = []
            final: dict[str, Any] = {}
            saw_any_line = False
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                saw_any_line = True
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = chunk.get("message") if isinstance(chunk, dict) else None
                if isinstance(message, dict):
                    c = message.get("content")
                    if isinstance(c, str) and c:
                        content_parts.append(c)
                    t = message.get("thinking")
                    if isinstance(t, str) and t:
                        thinking_parts.append(t)
                        if on_chunk is not None:
                            on_chunk(t)
                if isinstance(chunk, dict) and chunk.get("done"):
                    final = chunk

            if not saw_any_line:
                raise RuntimeError("Ollama returned an empty streamed response")

            merged: dict[str, Any] = dict(final)
            merged["message"] = {
                "role": "assistant",
                "content": "".join(content_parts),
                "thinking": "".join(thinking_parts),
            }
            return merged

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


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------

def _split_thinking(response: str) -> tuple[str, str]:
    """Extract <think>…</think> block. Returns (thinking, remainder)."""
    if "<think>" not in response:
        return "", response
    if "</think>" in response:
        start = response.find("<think>") + 7
        end = response.find("</think>")
        thinking = response[start:end].strip()
        remainder = response[end + 8:].strip()
    else:
        start = response.find("<think>") + 7
        thinking = response[start:].strip()
        remainder = ""
    return thinking, remainder


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


def _repair_json(text: str) -> str:
    """Repair known JSON malformations before parsing."""
    # Repair: unquoted property names — {type: "foo"} → {"type": "foo"}
    text = re.sub(
        r'([{,]\s*)([a-zA-Z_]\w*)(\s*:(?!\s*/))',
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        text,
    )
    return text


def _parse_output_object(text: str, schema_name: str) -> dict[str, object]:
    """Strip fences, extract first JSON object, attempt repair on failure."""
    text = strip_json_code_fences(text)
    start = text.find("{")
    if start == -1:
        raise RuntimeError(
            f"Ollama output is not valid JSON for {schema_name}: {text[:500]}"
        )
    text = text[start:]
    try:
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        repaired = _repair_json(text)
        if repaired == text:
            raise RuntimeError(
                f"Ollama output is not valid JSON for {schema_name}: {text[:500]}"
            )
        logger.warning("Ollama malformed JSON for %s — applied repair", schema_name)
        try:
            payload, _ = json.JSONDecoder().raw_decode(repaired)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama output is not valid JSON for {schema_name} "
                f"(after repair): {repaired[:500]}"
            ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Ollama output must be a JSON object")
    return payload
