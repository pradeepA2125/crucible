"""Recognise a provider refusing a request as larger than the model's context window.

Error formats follow the serving backend, not the provider: `openai_compatible` can front
vLLM (NVIDIA NIM), OpenRouter or llama.cpp, and local and cloud Ollama word it
differently. So this is one matcher over any error text, called by every transport where
it turns a failure into an exception, rather than an adapter per provider.

Formats, measured live on 2026-10-12 unless marked documented:
  OpenAI, Groq        code `context_length_exceeded`
  vLLM/NIM, OpenRouter "maximum context length is N tokens. However, … M tokens"
  llama.cpp (Ollama local with truncate=false)  type `exceed_context_size_error`
  Ollama cloud        "The prompt is too long: M, model maximum context length: N"
  Anthropic (doc)     "prompt is too long: M tokens > N maximum"; older models
                      "input length and `max_tokens` exceed context limit: a + b > N"
  Gemini (doc)        "The input token count (M) exceeds the maximum number of tokens
                      allowed (N)"
  TGI (doc)           "`inputs` tokens + `max_new_tokens` must be <= N. Given: M …"
  SGLang (doc)        "The input (M tokens) is longer than the model's context length (N …)"

Gemini's free tier is the odd one: a request larger than its per-minute input-token quota
comes back as a 429 before the window is ever checked, and retrying it as a rate limit
fails forever. It counts as an overflow only when the request alone exceeds the quota."""
from __future__ import annotations

import json
import re
from typing import Any

# Statuses a refused-as-too-long request comes back with: 400 almost everywhere, 413 from
# some gateways, 422 from TGI's validation layer. None is a stream error event (OpenAI's
# `response.failed`, an Ollama error line), which carries no HTTP status.
_REQUEST_STATUSES = frozenset({400, 413, 422})

_OVERFLOW_MARKERS = re.compile(
    r"context_length_exceeded"
    r"|exceed_context_size_error"
    r"|maximum context length"
    r"|prompt is too long"
    r"|exceed context limit"
    r"|input token count \(\d+\) exceeds the maximum"
    r"|`max_new_tokens` must be <="
    r"|longer than the model's context length",
    re.IGNORECASE,
)

_LIMIT_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"maximum context length is (\d+)",
    r"maximum context length:\s*(\d+)",
    r"tokens\s*>\s*(\d+)\s*maximum",
    r"exceed context limit:\s*\d+\s*\+\s*\d+\s*>\s*(\d+)",
    r"available context size \((\d+) tokens\)",
    r"n_ctx\\*\"\s*:\s*(\d+)",
    r"maximum number of tokens allowed \((\d+)\)",
    r"must be <=\s*(\d+)",
    r"model's context length \((\d+) tokens\)",
))

_PROMPT_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"resulted in (\d+) tokens",
    r"requested about (\d+) tokens",
    r"prompt is too long:\s*(\d+)",
    r"request \((\d+) tokens\)",
    r"n_prompt_tokens\\*\"\s*:\s*(\d+)",
    r"input token count \((\d+)\)",
    r"input \((\d+) tokens\)",
    r"given:\s*(\d+) `inputs` tokens",
))

_GEMINI_TOKEN_QUOTA = re.compile(r"input_token_count, limit:\s*(\d+)", re.IGNORECASE)


class ContextWindowExceeded(RuntimeError):
    """The provider refused the request as larger than the model's context window.

    Input-determined: resending it unchanged fails the same way, so the loop compacts the
    history and retries once instead. `limit` is the window the provider stated, when it
    stated one, and lets the compactor trigger earlier from then on."""

    def __init__(self, message: str, *, limit: int | None = None,
                 prompt_tokens: int | None = None) -> None:
        super().__init__(message)
        self.limit = limit
        self.prompt_tokens = prompt_tokens


def _first_int(patterns: tuple[re.Pattern[str], ...], text: str) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def detect_overflow(status: int | None, text: str, *,
                    request_tokens: int | None = None) -> ContextWindowExceeded | None:
    """The overflow `text` describes, or None. `request_tokens` (an estimate of the
    request's size) is needed only for Gemini's quota 429, to tell a request that can
    never fit apart from an ordinary rate limit."""
    if status == 429:
        quota = _GEMINI_TOKEN_QUOTA.search(text)
        limit = int(quota.group(1)) if quota else 0
        if limit > 0 and request_tokens is not None and request_tokens >= limit:
            return ContextWindowExceeded(
                f"The request (~{request_tokens} tokens) exceeds the provider's input-token "
                f"quota of {limit} per minute: {text[:300]}",
                limit=limit, prompt_tokens=request_tokens)
        return None
    if status is not None and status not in _REQUEST_STATUSES:
        return None
    if not _OVERFLOW_MARKERS.search(text):
        return None
    return ContextWindowExceeded(
        text[:500], limit=_first_int(_LIMIT_PATTERNS, text),
        prompt_tokens=_first_int(_PROMPT_PATTERNS, text))


def _status_of(exc: BaseException) -> int | None:
    for value in (getattr(exc, "status_code", None), getattr(exc, "code", None),
                  getattr(getattr(exc, "response", None), "status_code", None)):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _text_of(exc: BaseException) -> str:
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(body if isinstance(body, str) else json.dumps(body, default=str))
    response: Any = getattr(exc, "response", None)
    if response is not None:
        try:  # httpx raises on .text for a streamed response that was never read
            text = response.text
        except Exception:
            text = None
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def overflow_from_exception(exc: BaseException, *,
                            request_tokens: int | None = None) -> ContextWindowExceeded | None:
    """Read an SDK or HTTP exception (openai, groq and anthropic carry `status_code` and a
    parsed `body`; google-genai carries the status as `code`; httpx and huggingface_hub
    carry a `response`)."""
    if isinstance(exc, ContextWindowExceeded):
        return exc
    return detect_overflow(_status_of(exc), _text_of(exc), request_tokens=request_tokens)


def raise_if_overflow(exc: BaseException, *, request_tokens: int | None = None) -> None:
    """Re-raise `exc` as a ContextWindowExceeded when it is one, chained to the original."""
    overflow = overflow_from_exception(exc, request_tokens=request_tokens)
    if overflow is None:
        return
    if overflow is exc:
        raise exc
    raise overflow from exc
