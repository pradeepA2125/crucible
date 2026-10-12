"""Every transport turns its provider's "too large for the window" error into
ContextWindowExceeded, raised once: not retried as transient, not wrapped beyond
recognition, and (openai_compatible) not taken as evidence against JSON mode.

Each fake raises the SDK's real exception type carrying a body captured live on
2026-10-12, or the documented one where no key or credits were available."""
from __future__ import annotations

import json
from typing import Any

import anthropic
import groq
import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from huggingface_hub.errors import HfHubHTTPError

from agentd.providers.anthropic_transport import AnthropicJsonTransport
from agentd.providers.availability import find_context_overflow
from agentd.providers.context_overflow import ContextWindowExceeded
from agentd.providers.gemini_transport import GeminiJsonTransport
from agentd.providers.groq_transport import GroqJsonTransport
from agentd.providers.huggingface_transport import HuggingFaceJsonTransport
from agentd.providers.ollama_transport import OllamaJsonTransport
from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport

_REQUEST = httpx.Request("POST", "https://example.test/v1/chat/completions")

NIM = {"error": {"message": "This model's maximum context length is 1048576 tokens. However, "
                            "your messages resulted in 2928928 tokens. Please reduce the "
                            "length of the messages.", "type": "Bad Request", "code": 400}}
GROQ = {"error": {"message": "Please reduce the length of the messages or completion.",
                  "type": "invalid_request_error", "param": "messages",
                  "code": "context_length_exceeded"}}
ANTHROPIC = {"type": "error", "error": {"type": "invalid_request_error",
                                        "message": "prompt is too long: 208310 tokens > "
                                                   "200000 maximum"}}
GEMINI_QUOTA = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                          "message": "You exceeded your current quota. * Quota exceeded for "
                                     "metric: generativelanguage.googleapis.com/generate_"
                                     "content_free_tier_input_token_count, limit: 250000, "
                                     "model: gemini-3.8-flash Please retry in 50s."}}
OLLAMA_LOCAL = ('{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"request (5379 tokens) '
                'exceeds the available context size (512 tokens), try increasing it\\",'
                '\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":5379,'
                '\\"n_ctx\\":512}}"}')
OLLAMA_CLOUD = ('{"error":"The prompt is too long: 404084, model maximum context length: '
                '262144 (ref: 4080b5aa)"}')


def _sdk_error(cls: type[Exception], status: int, body: dict[str, Any]) -> Exception:
    response = httpx.Response(status, json=body, request=_REQUEST)
    return cls(json.dumps(body), response=response, body=body)  # type: ignore[call-arg]


class _Raising:
    """A create()/generate_content() client that raises `exc` on every call."""

    def __init__(self, exc: Exception) -> None:
        self.exc, self.calls = exc, 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.exc

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.exc


async def _json(transport: Any, **kwargs: Any) -> Any:
    return await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"}, **kwargs)


# --- openai_compatible (NIM, vLLM) and OpenRouter ---------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_openai_compatible_overflow_compacts_and_keeps_strict_json(streamed: bool):
    fake = _Raising(_sdk_error(openai.BadRequestError, 400, NIM))
    transport = OpenAICompatibleTransport(completions_client=fake,
                                          base_url="https://example.test/v1", max_retries=3)
    transport._newline_probe_done = True
    usage_mode = transport._stream_usage_mode
    with pytest.raises(ContextWindowExceeded) as raised:
        await _json(transport, **({"on_thinking": lambda _t: None} if streamed else {}))

    assert raised.value.limit == 1_048_576
    assert fake.calls == 1                      # no retry, no json_object fallback
    assert transport.json_mode == "strict"      # not evidence against response_format
    assert transport._stream_usage_mode == usage_mode  # nor against stream usage


@pytest.mark.asyncio
async def test_openai_compatible_other_400_still_downgrades():
    body = {"error": {"message": "response_format json_schema is not supported", "code": 400}}
    fake = _Raising(_sdk_error(openai.BadRequestError, 400, body))
    transport = OpenAICompatibleTransport(completions_client=fake,
                                          base_url="https://example.test/v1", max_retries=0)
    transport._newline_probe_done = True
    with pytest.raises(RuntimeError):
        await _json(transport)
    assert transport.json_mode == "json_object"


# --- Groq --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_overflow_is_raised_once():
    fake = _Raising(_sdk_error(groq.BadRequestError, 400, GROQ))
    transport = GroqJsonTransport(api_key="k", completions_client=fake)
    with pytest.raises(ContextWindowExceeded):
        await _json(transport)
    assert fake.calls == 1


# --- Anthropic ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_prompt_too_long_is_an_overflow():
    fake = _Raising(_sdk_error(anthropic.BadRequestError, 400, ANTHROPIC))
    transport = AnthropicJsonTransport(api_key="k", messages_client=fake)
    with pytest.raises(ContextWindowExceeded) as raised:
        await _json(transport)
    assert raised.value.limit == 200_000


class _StoppedAtWindow:
    async def create(self, **kwargs: Any) -> Any:
        return {"stop_reason": "model_context_window_exceeded",
                "content": [{"type": "text", "text": '{"partial": '}]}


@pytest.mark.asyncio
async def test_anthropic_reply_cut_at_the_window_is_an_overflow():
    transport = AnthropicJsonTransport(api_key="k", messages_client=_StoppedAtWindow())
    with pytest.raises(ContextWindowExceeded):
        await _json(transport)


# --- Gemini ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gemini_quota_429_for_a_request_bigger_than_the_quota_is_an_overflow():
    fake = _Raising(genai_errors.ClientError(429, GEMINI_QUOTA))
    transport = GeminiJsonTransport(models_client=fake, max_retries=3)
    with pytest.raises(ContextWindowExceeded) as raised:
        await transport.generate_json(
            model="m", schema_name="s", schema={"type": "object"},
            system_instructions="sys", user_payload={"text": "x" * 1_200_000})
    assert raised.value.limit == 250_000
    assert fake.calls == 1                      # not retried as a rate limit


@pytest.mark.asyncio
async def test_gemini_quota_429_for_a_small_request_stays_a_rate_limit(monkeypatch):
    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("agentd.providers.gemini_transport.asyncio.sleep", _no_sleep)
    fake = _Raising(genai_errors.ClientError(429, GEMINI_QUOTA))
    transport = GeminiJsonTransport(models_client=fake, max_retries=2)
    with pytest.raises(genai_errors.ClientError):
        await _json(transport)
    assert fake.calls == 3                      # retried as before


@pytest.mark.asyncio
async def test_gemini_window_400_is_an_overflow():
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                      "message": "The input token count (860489) exceeds the maximum number "
                                 "of tokens allowed (131072)."}}
    fake = _Raising(genai_errors.ClientError(400, body))
    with pytest.raises(ContextWindowExceeded) as raised:
        await _json(GeminiJsonTransport(models_client=fake))
    assert raised.value.limit == 131_072


# --- Hugging Face ------------------------------------------------------------------------

class _HfClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def text_generation(self, **kwargs: Any) -> Any:
        raise self.exc


@pytest.mark.asyncio
async def test_huggingface_backend_overflow_is_recognised():
    body = {"error": "Input validation error: `inputs` tokens + `max_new_tokens` must be "
                     "<= 4096. Given: 6644 `inputs` tokens and 100 `max_new_tokens`"}
    response = httpx.Response(422, json=body, request=_REQUEST)
    transport = HuggingFaceJsonTransport(
        api_key="hf", inference_client=_HfClient(HfHubHTTPError("422", response=response)))
    with pytest.raises(ContextWindowExceeded) as raised:
        await _json(transport)
    assert raised.value.limit == 4_096


# --- Ollama ------------------------------------------------------------------------------

class _OllamaResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status_code, self._text = status, text

    async def aread(self) -> bytes:
        return self._text.encode()

    async def aiter_lines(self):
        yield self._text


class _OllamaClient:
    def __init__(self, status: int, text: str) -> None:
        self.response, self.calls = _OllamaResponse(status, text), 0

    def stream(self, method: str, url: str, *, json: dict[str, Any]) -> Any:
        self.calls += 1
        response = self.response

        class _Ctx:
            async def __aenter__(self) -> Any:
                return response

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "limit"), [(OLLAMA_LOCAL, 512), (OLLAMA_CLOUD, 262_144)])
async def test_ollama_overflow_is_raised_once_with_its_window(body: str, limit: int):
    client = _OllamaClient(400, body)
    with pytest.raises(ContextWindowExceeded) as raised:
        await _json(OllamaJsonTransport(http_client=client))
    assert raised.value.limit == limit
    assert client.calls == 1


@pytest.mark.asyncio
async def test_an_ollama_error_line_after_the_200_is_not_swallowed():
    client = _OllamaClient(200, '{"error":"model runner has unexpectedly stopped"}')
    with pytest.raises(RuntimeError, match="unexpectedly stopped") as raised:
        await _json(OllamaJsonTransport(http_client=client))
    assert find_context_overflow(raised.value) is None
