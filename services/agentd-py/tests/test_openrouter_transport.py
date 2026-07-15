from __future__ import annotations

import json

import pytest

from agentd.providers.openrouter_transport import OpenRouterJsonTransport

# ---------------------------------------------------------------------------
# Fakes for the OpenAI-compatible chat.completions client
# ---------------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Records every create() call and replays a scripted list of contents."""

    def __init__(self, contents: list[object]) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        item = self._contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def _transport(contents: list[object]) -> tuple[OpenRouterJsonTransport, _FakeCompletions]:
    fake = _FakeCompletions(contents)
    return OpenRouterJsonTransport(completions_client=fake), fake


# ---------------------------------------------------------------------------
# Streaming fakes (generate_json with on_thinking — Ollama-parity fix)
# ---------------------------------------------------------------------------

class _FakeDelta:
    def __init__(self, content: str | None = None, reasoning: str | None = None) -> None:
        self.content = content
        self.reasoning = reasoning


class _FakeStreamChoice:
    def __init__(self, content: str | None, reasoning: str | None) -> None:
        self.delta = _FakeDelta(content, reasoning)


class _FakeStreamChunk:
    def __init__(self, content: str | None = None, reasoning: str | None = None) -> None:
        self.choices = [_FakeStreamChoice(content, reasoning)]


class _FakeStream:
    def __init__(self, chunks: list[_FakeStreamChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


class _FakeCompletionsStreaming(_FakeCompletions):
    """Extends the non-streaming fake: a stream=True call returns a fake
    async-iterable stream of chunks instead of popping a scripted response."""

    def __init__(self, contents: list[object], stream_chunks: list[_FakeStreamChunk] | None = None) -> None:
        super().__init__(contents)
        self._stream_chunks = stream_chunks or []

    async def create(self, **kwargs: object):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return _FakeStream(self._stream_chunks)
        item = self._contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.mark.asyncio
async def test_generate_json_streams_reasoning_when_on_thinking_given() -> None:
    """Ollama-parity fix: on_thinking was accepted by generate_json but silently
    unused — every controller_step_response call (the one driving every turn of a
    live-driven session) rendered nothing until the whole call completed."""
    chunks = [
        _FakeStreamChunk(reasoning="weighing options"),
        _FakeStreamChunk(content='{"answer"'),
        _FakeStreamChunk(content=": 1}"),
    ]
    fake = _FakeCompletionsStreaming([], stream_chunks=chunks)
    transport = OpenRouterJsonTransport(completions_client=fake)
    seen: list[str] = []

    result = await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=seen.append,
    )

    assert result == {"answer": 1}
    assert seen == ["weighing options"]
    assert fake.calls[0]["stream"] is True


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FlakyStreamCompletions:
    def __init__(self, chunks: list[_FakeStreamChunk], status_code: int = 503) -> None:
        self._chunks = chunks
        self._first = True
        self._status_code = status_code
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        if self._first:
            self._first = False
            raise _StatusError(self._status_code)
        return _FakeStream(self._chunks)


@pytest.mark.asyncio
async def test_stream_with_thinking_calls_on_retry_not_on_thinking() -> None:
    """A transient-error retry cycle can run for minutes with the UI otherwise
    showing nothing — on_retry must fire a status update per retry attempt, and
    retry text must NOT go through on_thinking (reserved for real model reasoning)."""
    fake = _FlakyStreamCompletions([_FakeStreamChunk(content="hi")])
    transport = OpenRouterJsonTransport(completions_client=fake)
    thinking_chunks: list[str] = []
    retries: list[tuple[int, int, str, str]] = []

    # _stream_with_thinking directly — generate_text's public signature doesn't
    # expose on_retry (nothing in the controller-chat scope calls generate_text
    # with a retry-relevant need), but the shared retry machinery still supports it.
    result = await transport._stream_with_thinking(
        {"model": "some/model", "messages": []},
        on_thinking=thinking_chunks.append,
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert result == "hi"
    assert not any("retrying" in c for c in thinking_chunks), thinking_chunks
    assert len(retries) == 1, retries
    attempt, max_attempts, reason, message = retries[0]
    assert (attempt, max_attempts, reason) == (1, 4, "server_error")  # default max_retries=4
    assert "attempt 1/4" in message


class _FlakyCompletions:
    """Non-streaming completions fake: raises once with a given status, then
    returns a real _FakeResponse — mirrors _FlakyStreamCompletions but for the
    plain (non-stream=True) _call_with_retry path."""

    def __init__(self, content: str, status_code: int = 503) -> None:
        self._content = content
        self._first = True
        self._status_code = status_code
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        if self._first:
            self._first = False
            raise _StatusError(self._status_code)
        return _FakeResponse(self._content)


@pytest.mark.asyncio
async def test_call_with_retry_calls_on_retry() -> None:
    """_call_with_retry previously had no callback parameter at all — it retried
    silently. This is the one case in this file where on_retry is a genuinely
    new parameter, not a swap of an existing callback."""
    fake = _FlakyCompletions("ignored", status_code=500)
    transport = OpenRouterJsonTransport(completions_client=fake)
    retries: list[tuple[int, int, str, str]] = []

    result = await transport._call_with_retry(
        {"model": "m", "messages": []},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert result is not None
    assert len(retries) == 1
    attempt, max_attempts, reason, message = retries[0]
    assert (attempt, reason) == (1, "server_error")


@pytest.mark.asyncio
async def test_call_with_retry_classifies_429_as_rate_limited() -> None:
    fake = _FlakyCompletions("ignored", status_code=429)
    transport = OpenRouterJsonTransport(completions_client=fake)
    retries: list[tuple[int, int, str, str]] = []

    await transport._call_with_retry(
        {"model": "m", "messages": []},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert retries[0][2] == "rate_limited"


@pytest.mark.asyncio
async def test_malformed_json_fallback_calls_on_retry() -> None:
    """The malformed-JSON fallback loop is a transport-level retry on a JSON-parse
    failure — distinct from the controller-level corrective retry, but shares
    reason='malformed_response' per the spec's unified taxonomy."""
    fake = _FakeCompletions([
        "not json at all",       # primary strict-schema attempt -> triggers fallback
        "still not json either", # fallback attempt 0 -> malformed, retries
        json.dumps({"ok": True}),  # fallback attempt 1 -> succeeds
    ])
    transport = OpenRouterJsonTransport(completions_client=fake, max_retries=2)
    retries: list[tuple[int, int, str, str]] = []

    result = await transport.generate_json(
        model="some/model", schema_name="controller_step_response",
        schema={"type": "object"}, system_instructions="s", user_payload={},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert result == {"ok": True}
    assert any(r[2] == "malformed_response" for r in retries), retries


@pytest.mark.asyncio
async def test_generate_json_non_streaming_when_no_on_thinking() -> None:
    """Without on_thinking, the plain non-streaming call path is unchanged."""
    transport, fake = _transport([json.dumps({"ok": True})])
    result = await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert result == {"ok": True}
    assert "stream" not in fake.calls[0]


# ---------------------------------------------------------------------------
# json_max_tokens vs max_tokens split (Ollama-parity fix)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_json_uses_json_max_tokens_not_max_tokens() -> None:
    fake = _FakeCompletions([json.dumps({"ok": True})])
    transport = OpenRouterJsonTransport(
        completions_client=fake, max_tokens=111, json_max_tokens=222)
    await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert fake.calls[0]["max_completion_tokens"] == 222


@pytest.mark.asyncio
async def test_generate_text_uses_max_tokens_not_json_max_tokens() -> None:
    fake = _FakeCompletions(["hi"])
    transport = OpenRouterJsonTransport(
        completions_client=fake, max_tokens=111, json_max_tokens=222)
    await transport.generate_text(
        model="some/model", system_instructions="", user_payload={},
    )
    assert fake.calls[0]["max_completion_tokens"] == 111


def test_json_max_tokens_defaults_much_larger_than_max_tokens() -> None:
    transport = OpenRouterJsonTransport(completions_client=_FakeCompletions([]))
    assert transport._json_max_tokens > transport._max_tokens * 2


# ---------------------------------------------------------------------------
# Model-capability registry (replaces hardcoded name-substring guessing)
# ---------------------------------------------------------------------------

class _FakeCapsHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeCapsHttpClient:
    """Fakes the httpx.AsyncClient surface _ModelCapabilityCache needs."""

    def __init__(self, models: list[dict]) -> None:
        self._models = models
        self.calls = 0

    async def get(self, url: str, timeout: float | None = None) -> _FakeCapsHttpResponse:
        self.calls += 1
        return _FakeCapsHttpResponse({"data": self._models})


_NEMOTRON_REGISTRY_ENTRY = {
    # Trimmed to the fields we read, matching the real shape verified live
    # against https://openrouter.ai/api/v1/models 2026-07-13.
    "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "supported_parameters": ["reasoning", "reasoning_effort", "max_tokens", "temperature"],
    "default_parameters": {"temperature": 1},
}


@pytest.mark.asyncio
async def test_model_capability_cache_reads_registry_shape() -> None:
    from agentd.providers.openrouter_transport import _ModelCapabilityCache

    http = _FakeCapsHttpClient([_NEMOTRON_REGISTRY_ENTRY])
    cache = _ModelCapabilityCache(http_client=http)

    entry = await cache.get("nvidia/nemotron-3-ultra-550b-a55b:free")
    assert entry == _NEMOTRON_REGISTRY_ENTRY
    assert await cache.get("nvidia/nemotron-3-ultra-550b-a55b:free") is not None
    assert http.calls == 1  # cached within TTL, no second fetch


@pytest.mark.asyncio
async def test_model_capability_cache_unknown_model_returns_none() -> None:
    from agentd.providers.openrouter_transport import _ModelCapabilityCache

    cache = _ModelCapabilityCache(http_client=_FakeCapsHttpClient([_NEMOTRON_REGISTRY_ENTRY]))
    assert await cache.get("some/unknown-model") is None


@pytest.mark.asyncio
async def test_reasoning_config_uses_registry_when_available() -> None:
    """The real registry shape correctly drives is_reasoning=True, temperature=1 —
    verified against the ACTUAL OpenRouter API response for this exact model."""
    from agentd.providers.openrouter_transport import _ModelCapabilityCache

    http = _FakeCapsHttpClient([_NEMOTRON_REGISTRY_ENTRY])
    fake = _FakeCompletions([])
    transport = OpenRouterJsonTransport(
        completions_client=fake, model_capabilities=_ModelCapabilityCache(http_client=http))

    is_reasoning, temperature = await transport._reasoning_config(
        "nvidia/nemotron-3-ultra-550b-a55b:free")
    assert is_reasoning is True
    assert temperature == 1.0


@pytest.mark.asyncio
async def test_reasoning_config_catches_a_model_the_hardcoded_list_would_miss() -> None:
    """The whole point of the registry-driven path: a model whose family name
    isn't in the hardcoded substring list, but whose registry entry declares
    reasoning support, is still correctly detected — proving this doesn't need
    updating by hand for every new reasoning-model family the way the old
    name-substring heuristic did (it missed Nemotron entirely until caught live)."""
    from agentd.providers.openrouter_transport import _ModelCapabilityCache

    unlisted = {
        "id": "acme/brand-new-reasoner",
        "supported_parameters": ["reasoning"],
        "default_parameters": {"temperature": 0.7},
    }
    http = _FakeCapsHttpClient([unlisted])
    fake = _FakeCompletions([])
    transport = OpenRouterJsonTransport(
        completions_client=fake, model_capabilities=_ModelCapabilityCache(http_client=http))

    is_reasoning, temperature = await transport._reasoning_config("acme/brand-new-reasoner")
    assert is_reasoning is True
    assert temperature == 0.7


@pytest.mark.asyncio
async def test_reasoning_config_falls_back_to_heuristic_without_registry() -> None:
    """Default test/fake construction (no model_capabilities injected) never makes
    a network call — falls straight to the name-substring heuristic."""
    transport = OpenRouterJsonTransport(completions_client=_FakeCompletions([]))
    assert transport._model_caps is None

    is_reasoning, temperature = await transport._reasoning_config("qwen/qwen3-coder")
    assert is_reasoning is True
    assert temperature == 1.0

    is_reasoning, temperature = await transport._reasoning_config("some/plain-model")
    assert is_reasoning is False
    assert temperature == 0.0


@pytest.mark.asyncio
async def test_reasoning_config_falls_back_when_registry_lookup_fails() -> None:
    """A capability fetch failure (network issue, endpoint change) degrades to the
    heuristic rather than raising and failing the whole turn."""
    from agentd.providers.openrouter_transport import _ModelCapabilityCache

    class _BrokenHttpClient:
        async def get(self, url: str, timeout: float | None = None):
            raise RuntimeError("connection refused")

    cache = _ModelCapabilityCache(http_client=_BrokenHttpClient())
    fake = _FakeCompletions([])
    transport = OpenRouterJsonTransport(completions_client=fake, model_capabilities=cache)

    is_reasoning, temperature = await transport._reasoning_config("qwen/qwen3-coder")
    assert is_reasoning is True
    assert temperature == 1.0


# ---------------------------------------------------------------------------
# require_parameters routing guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_require_parameters_is_set() -> None:
    """Every request pins provider.require_parameters so strict json_schema is
    only routed to providers that actually enforce response_format."""
    transport, fake = _transport([json.dumps({"answer": 1})])
    await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert fake.calls[0]["extra_body"]["provider"]["require_parameters"] is True
    # And strict json_schema is the requested response format.
    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_require_parameters_can_be_disabled() -> None:
    """With require_parameters=False the strict call omits the provider guard, so it
    routes to the default provider instead of hard-404ing on a non-supporting tier."""
    fake = _FakeCompletions([json.dumps({"answer": 1})])
    transport = OpenRouterJsonTransport(completions_client=fake, require_parameters=False)
    await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert "provider" not in fake.calls[0].get("extra_body", {})
    # strict json_schema is still requested — only the routing guard is dropped.
    assert fake.calls[0]["response_format"]["type"] == "json_schema"


# ---------------------------------------------------------------------------
# Narrowing fallback for the controller schema
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrowing_retries_when_action_fields_missing() -> None:
    """A valid `type` with its action fields missing triggers exactly one retry
    against a schema whose `required` is narrowed to that type's fields."""
    transport, fake = _transport([
        json.dumps({"type": "tool_call", "thought": "exploring"}),  # missing tool/args
        json.dumps({"type": "tool_call", "thought": "ok",
                    "tool": "read_file", "args": {"path": "x"}}),
    ])
    result = await transport.generate_json(
        model="some/model", schema_name="controller_step_response",
        schema={"type": "object"}, system_instructions="", user_payload={},
    )
    assert result["tool"] == "read_file"
    assert len(fake.calls) == 2
    narrowed = fake.calls[1]["response_format"]["json_schema"]["schema"]
    assert set(narrowed.get("required", [])) >= {"type", "thought", "tool", "args"}


@pytest.mark.asyncio
async def test_no_narrowing_when_action_fields_present() -> None:
    """A complete controller response is returned on the first call — no retry."""
    transport, fake = _transport([
        json.dumps({"type": "answer", "thought": "t", "answer": "hi"}),
    ])
    result = await transport.generate_json(
        model="some/model", schema_name="controller_step_response",
        schema={"type": "object"}, system_instructions="", user_payload={},
    )
    assert result["answer"] == "hi"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_fallback_drops_require_parameters() -> None:
    """When the strict json_schema call fails, the json_object fallback must NOT
    carry provider.require_parameters — otherwise it inherits the same routing
    restriction and can never rescue the request (the live free-tier 404 bug)."""
    transport, fake = _transport([
        RuntimeError("no endpoints for response_format"),  # strict call fails
        json.dumps({"type": "answer", "thought": "t", "answer": "hi"}),  # fallback ok
    ])
    result = await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert result["answer"] == "hi"
    assert len(fake.calls) == 2
    # strict call carried the routing guard; the fallback dropped it.
    assert fake.calls[0]["extra_body"]["provider"]["require_parameters"] is True
    assert "provider" not in fake.calls[1].get("extra_body", {})
    assert fake.calls[1]["response_format"]["type"] == "json_object"


@pytest.mark.asyncio
async def test_narrowing_only_applies_to_controller_schema() -> None:
    """A non-controller schema is never narrowed, even if fields look missing."""
    transport, fake = _transport([json.dumps({"type": "tool_call", "thought": "x"})])
    result = await transport.generate_json(
        model="some/model", schema_name="other_schema",
        schema={"type": "object"}, system_instructions="", user_payload={},
    )
    assert result["type"] == "tool_call"
    assert len(fake.calls) == 1
