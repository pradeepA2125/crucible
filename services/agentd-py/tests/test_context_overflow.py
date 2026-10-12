"""One classifier for every provider's "request is larger than the context window" error.

Formats follow the serving backend, not the provider: `openai_compatible` can front vLLM
(NIM), OpenRouter or llama.cpp, and local and cloud Ollama disagree with each other. So
one matcher reads any error text instead of one adapter per provider.

The LIVE bodies were captured on 2026-10-12 by sending each provider a request larger
than its window. The DOCUMENTED ones come from provider docs and public reports (no key
or no credits to reproduce them here)."""
from __future__ import annotations

import pytest

from agentd.providers.availability import is_provider_unavailable
from agentd.providers.context_overflow import (
    ContextWindowExceeded,
    detect_overflow,
    overflow_from_exception,
)

# --- live captures ---------------------------------------------------------------------
NIM = ('{"error":{"message":"This model\'s maximum context length is 1048576 tokens. '
       'However, your messages resulted in 2928928 tokens. Please reduce the length of the '
       'messages.","type":"Bad Request","code":400}}')
OPENROUTER = ('{"error":{"message":"This endpoint\'s maximum context length is 262144 tokens. '
              'However, you requested about 282447 tokens (282383 of text input, 64 in the '
              'output). Please reduce the length of either one, or use the '
              'context-compression plugin to compress your prompt automatically.","code":400,'
              '"metadata":{"provider_name":null}}}')
GROQ = ('{"error":{"message":"Please reduce the length of the messages or completion.",'
        '"type":"invalid_request_error","param":"messages","code":"context_length_exceeded"}}')
OLLAMA_LOCAL = ('{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"request (5379 tokens) '
                'exceeds the available context size (512 tokens), try increasing it\\",'
                '\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":5379,'
                '\\"n_ctx\\":512}}"}')
OLLAMA_CLOUD = ('{"error":"The prompt is too long: 404084, model maximum context length: 262144 '
                '(ref: 4080b5aa-fcf8-4a22-9845-7bf4d143a912)"}')
GEMINI_QUOTA = ('{"error": {"code": 429, "message": "You exceeded your current quota, please '
                'check your plan and billing details. For more information on this error, head '
                'to: https://ai.google.dev/gemini-api/docs/rate-limits. \\n* Quota exceeded for '
                'metric: generativelanguage.googleapis.com/'
                'generate_content_free_tier_input_token_count, limit: 250000, model: '
                'gemini-3.8-flash\\nPlease retry in 50.782770011s.", '
                '"status": "RESOURCE_EXHAUSTED"}}')

# --- documented ------------------------------------------------------------------------
ANTHROPIC = ('{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too '
             'long: 208310 tokens > 200000 maximum"}}')
ANTHROPIC_OLDER = ('{"type":"error","error":{"type":"invalid_request_error","message":"input '
                   'length and `max_tokens` exceed context limit: 188240 + 21333 > 200000, '
                   'decrease input length or `max_tokens` and try again"}}')
GEMINI = ("400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'The input token count "
          "(860489) exceeds the maximum number of tokens allowed (131072).', 'status': "
          "'INVALID_ARGUMENT'}}")
TGI = ('{"error":"Input validation error: `inputs` tokens + `max_new_tokens` must be <= 4096. '
       'Given: 6644 `inputs` tokens and 100 `max_new_tokens`","error_type":"validation"}')
SGLANG = ('{"object":"error","message":"The input (40000 tokens) is longer than the model\'s '
          'context length (32768 tokens).","type":"BadRequestError","code":400}')


@pytest.mark.parametrize(("status", "body", "limit", "prompt"), [
    (400, NIM, 1_048_576, 2_928_928),
    (400, OPENROUTER, 262_144, 282_447),
    (400, GROQ, None, None),
    (400, OLLAMA_LOCAL, 512, 5_379),
    (400, OLLAMA_CLOUD, 262_144, 404_084),
    (400, ANTHROPIC, 200_000, 208_310),
    (400, ANTHROPIC_OLDER, 200_000, None),
    (400, GEMINI, 131_072, 860_489),
    (422, TGI, 4_096, 6_644),
    (400, SGLANG, 32_768, 40_000),
])
def test_each_known_format_is_an_overflow_with_its_numbers(status, body, limit, prompt):
    overflow = detect_overflow(status, body)
    assert overflow is not None
    assert (overflow.limit, overflow.prompt_tokens) == (limit, prompt)


def test_a_stream_error_with_no_status_is_still_recognised():
    # OpenAI's `response.failed` event carries a code but no HTTP status.
    assert detect_overflow(None, "context_length_exceeded: Your input exceeds the window")


@pytest.mark.parametrize(("status", "body"), [
    (400, '{"error":{"message":"Invalid schema for response_format","code":400}}'),
    (400, '{"error":"This model does not support assistant message prefill."}'),
    (402, '{"error":"You have no remaining credits."}'),
    # A 5xx that mentions the window is the server failing, not our request's size.
    (500, '{"error":"internal error near maximum context length is 4096"}'),
    (404, '{"error":{"message":"The model `groq/compound` does not exist","code":'
          '"model_not_found"}}'),
])
def test_other_errors_are_not_overflows(status, body):
    assert detect_overflow(status, body) is None


def test_a_gemini_token_quota_429_is_an_overflow_only_when_the_request_alone_exceeds_it():
    # The free tier's per-minute input-token quota: a request bigger than the quota on its
    # own 429s instantly and forever, so retrying it as a rate limit can never succeed.
    big = detect_overflow(429, GEMINI_QUOTA, request_tokens=400_000)
    assert big is not None and big.limit == 250_000

    # The same 429 for a small request is a real rate limit (earlier calls used the quota).
    assert detect_overflow(429, GEMINI_QUOTA, request_tokens=20_000) is None
    assert detect_overflow(429, GEMINI_QUOTA) is None


def test_a_quota_of_zero_is_not_a_window():
    # Free-tier `limit: 0` means the model has no free quota at all: an access problem.
    body = GEMINI_QUOTA.replace("limit: 250000", "limit: 0")
    assert detect_overflow(429, body, request_tokens=400_000) is None


class _SdkError(Exception):
    """Shaped like the openai/groq/anthropic SDK errors: status_code + parsed body."""

    def __init__(self, status_code: int, body: object) -> None:
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code
        self.body = body


class _GenaiError(Exception):
    """Shaped like google.genai.errors.APIError: the HTTP status is `.code`."""

    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code


def test_an_sdk_exception_is_read_from_its_status_and_body():
    exc = _SdkError(400, {"error": {"message": "prompt is too long: 208310 tokens > 200000 "
                                               "maximum", "type": "invalid_request_error"}})
    overflow = overflow_from_exception(exc)
    assert overflow is not None and overflow.limit == 200_000


def test_a_genai_exception_is_read_from_its_code():
    overflow = overflow_from_exception(_GenaiError(429, GEMINI_QUOTA), request_tokens=300_000)
    assert overflow is not None and overflow.limit == 250_000


def test_an_overflow_is_never_reported_as_the_provider_being_unavailable():
    # The Gemini case is raised from a 429; the loop must compact, not wait out an "outage".
    try:
        try:
            raise _GenaiError(429, GEMINI_QUOTA)
        except _GenaiError as exc:
            raise ContextWindowExceeded("over", limit=250_000) from exc
    except ContextWindowExceeded as exc:
        assert is_provider_unavailable(exc) is False
