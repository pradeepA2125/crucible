import json

import pytest

from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.openrouter_transport import OpenRouterJsonTransport


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.choices = [_FakeChoice(content, finish_reason)]


class _NoChoicesResponse:
    """What openrouter/free returns as a routing artifact — see the
    _is_reasoning_model docstring. Nothing to do with response_format."""

    def __init__(self) -> None:
        self.choices: list[object] = []


class _FakeCompletions:
    """Records every create() call and replays a scripted list of contents.

    A scripted item may be an Exception (raised), a str (wrapped in a default
    _FakeResponse), or any other object (returned as-is — that is how a test
    scripts a finish_reason or a malformed response shape).
    """

    def __init__(self, contents: list[object]) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        item = self._contents.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return _FakeResponse(item)
        return item


def _transport(contents: list[object], **kw) -> tuple[OpenAICompatibleTransport, _FakeCompletions]:
    fake = _FakeCompletions(contents)
    kw.setdefault("base_url", "https://example.test/v1")
    return OpenAICompatibleTransport(completions_client=fake, **kw), fake


def test_openrouter_is_a_subclass() -> None:
    """The extraction's contract: OpenRouter reuses the base, it does not duplicate it."""
    assert issubclass(OpenRouterJsonTransport, OpenAICompatibleTransport)


@pytest.mark.asyncio
async def test_generate_json_returns_parsed_object() -> None:
    transport, fake = _transport([json.dumps({"ok": True})])
    result = await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )
    assert result == {"ok": True}
    assert len(fake.calls) == 1
    assert fake.calls[0]["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_base_sends_no_openrouter_specific_fields() -> None:
    """The base must be vendor-neutral: no provider.require_parameters, no site headers."""
    transport, fake = _transport([json.dumps({"ok": True})])
    await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert "provider" not in fake.calls[0].get("extra_body", {})


@pytest.mark.asyncio
async def test_generate_text_uses_max_tokens_not_json_max_tokens() -> None:
    transport, fake = _transport(["hello"], max_tokens=77, json_max_tokens=4242)
    await transport.generate_text(model="m", system_instructions="", user_payload={})
    assert fake.calls[0]["max_completion_tokens"] == 77


@pytest.mark.asyncio
async def test_openrouter_generate_text_sends_no_require_parameters_guard() -> None:
    """The `for_json` seam, from the caller's side: provider.require_parameters
    guards response_format routing, which only a structured-output call sends.
    Routing an OpenRouter TEXT completion through it would narrow provider choice
    with nothing to protect. Extracting the base briefly regressed this by putting
    generate_text through the same hook call as generate_json — this pins it."""
    fake = _FakeCompletions(["hello"])
    transport = OpenRouterJsonTransport(completions_client=fake)

    await transport.generate_text(model="some/model", system_instructions="", user_payload={})

    assert "provider" not in fake.calls[0].get("extra_body", {})
    # A non-reasoning text call carries no extra_body key at all.
    assert "extra_body" not in fake.calls[0]


# ---------------------------------------------------------------------------
# Real construction — the ONLY tests here that do not inject completions_client.
#
# Injecting a fake short-circuits the base constructor before it ever calls
# self._default_headers(), so every other test in both files leaves the hook, the
# _site_url/_site_name-before-super() ordering, and the AsyncOpenAI construction
# completely unexercised. That ordering is exactly the hazard the extraction
# introduced: _default_headers() is now a virtual method dispatched from a base
# __init__, so moving those two assignments below super().__init__() would break
# every real OpenRouter construction while all the fake-injected tests still pass.
#
# Constructing AsyncOpenAI makes no network call (verified: ~0.02s, offline).
# ---------------------------------------------------------------------------

def test_openrouter_real_construction_dispatches_default_headers_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing for real is itself the ordering assertion: the base __init__
    calls the overridden _default_headers(), which reads _site_url/_site_name. If
    those assignments ever move below super().__init__(), this raises
    AttributeError at construction instead of silently shipping headerless
    requests."""
    monkeypatch.delenv("CRUCIBLE_OPENROUTER_SITE_URL", raising=False)
    monkeypatch.delenv("CRUCIBLE_OPENROUTER_SITE_NAME", raising=False)

    transport = OpenRouterJsonTransport(
        api_key="x", site_url="https://site.test", site_name="Crucible",
    )

    assert transport._default_headers() == {
        "HTTP-Referer": "https://site.test",
        "X-Title": "Crucible",
    }
    # And they actually reached the constructed client — proving the hook's return
    # value is really wired into the default_headers kwarg, not just computable.
    headers = transport._completions._client.default_headers
    assert headers["HTTP-Referer"] == "https://site.test"
    assert headers["X-Title"] == "Crucible"


def test_openrouter_real_construction_without_site_config_sends_no_site_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The falsy branch of `default_headers=extra_headers or None`: with neither
    site value configured the hook returns None and no site headers are attached."""
    monkeypatch.delenv("CRUCIBLE_OPENROUTER_SITE_URL", raising=False)
    monkeypatch.delenv("CRUCIBLE_OPENROUTER_SITE_NAME", raising=False)

    transport = OpenRouterJsonTransport(api_key="x")

    assert transport._default_headers() is None
    headers = transport._completions._client.default_headers
    assert "HTTP-Referer" not in headers
    assert "X-Title" not in headers


def test_base_real_construction_sends_placeholder_key_and_no_headers() -> None:
    """The base's own real-construction path: a keyless local endpoint (vLLM,
    LM Studio) still constructs, because the OpenAI SDK demands *some* api_key."""
    transport = OpenAICompatibleTransport(base_url="https://example.test/v1")

    assert transport._default_headers() is None
    client = transport._completions._client
    assert client.api_key == "not-required"
    assert "HTTP-Referer" not in client.default_headers


@pytest.mark.asyncio
async def test_generate_json_uses_json_max_tokens() -> None:
    transport, fake = _transport([json.dumps({"ok": True})], max_tokens=77, json_max_tokens=4242)
    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert fake.calls[0]["max_completion_tokens"] == 4242


# ---------------------------------------------------------------------------
# Sticky json_object downgrade
# ---------------------------------------------------------------------------

class _StatusError(Exception):
    """Mimics the OpenAI SDK's APIStatusError shape: what _is_retryable reads is
    the `status_code` attribute, nothing else."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


@pytest.mark.asyncio
async def test_downgrade_sticks_after_strict_failure() -> None:
    """Call 1 tries strict, fails, falls back. Call 2 must skip strict entirely —
    otherwise every LLM call in the system pays a guaranteed failed request."""
    transport, fake = _transport([
        RuntimeError("response_format not supported"),   # call 1 strict -> fail
        json.dumps({"ok": 1}),                            # call 1 fallback -> ok
        json.dumps({"ok": 2}),                            # call 2 -> straight to fallback
    ])

    first = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    second = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )

    assert first == {"ok": 1}
    assert second == {"ok": 2}
    # 2 requests for call 1 (strict + fallback), only 1 for call 2.
    assert len(fake.calls) == 3
    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    assert fake.calls[1]["response_format"]["type"] == "json_object"
    assert fake.calls[2]["response_format"]["type"] == "json_object"


@pytest.mark.asyncio
async def test_downgrade_clears_grammar_support_flags() -> None:
    """An endpoint that cannot honor json_schema must not be sent a tight oneOf schema."""
    transport, _ = _transport([
        RuntimeError("response_format not supported"),
        json.dumps({"ok": 1}),
    ], supports_oneof=True)

    assert transport.supports_oneof_grammar is True
    assert transport.supports_anyof_grammar is True

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )

    assert transport.supports_oneof_grammar is False
    assert transport.supports_anyof_grammar is False


@pytest.mark.asyncio
async def test_downgrade_is_per_instance_not_class_wide() -> None:
    """Clearing supports_anyof_grammar shadows the class attribute on THIS
    instance only — a second transport in the same process must start clean."""
    downgraded, _ = _transport([
        RuntimeError("response_format not supported"), json.dumps({"ok": 1}),
    ], supports_oneof=True)
    await downgraded.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert downgraded.supports_anyof_grammar is False

    fresh, fresh_fake = _transport([json.dumps({"ok": 2})], supports_oneof=True)
    assert fresh.supports_anyof_grammar is True
    assert fresh.supports_oneof_grammar is True
    await fresh.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert fresh_fake.calls[0]["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_no_downgrade_when_strict_succeeds() -> None:
    transport, fake = _transport([json.dumps({"ok": 1}), json.dumps({"ok": 2})],
                                 supports_oneof=True)
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    assert len(fake.calls) == 2
    assert all(c["response_format"]["type"] == "json_schema" for c in fake.calls)
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_does_not_downgrade() -> None:
    """A 429 says "you are sending too many requests", NOT "I cannot honor
    response_format". Downgrading on it would let one burst of rate limiting
    silently degrade every structured call for the remaining process lifetime.
    The fallback still fires (unchanged), only the STICKY flag is withheld."""
    transport, fake = _transport([
        _StatusError(429),          # call 1 strict -> retries exhausted (max_retries=0)
        json.dumps({"ok": 1}),      # call 1 fallback -> ok
        json.dumps({"ok": 2}),      # call 2 strict -> must be attempted again
    ], max_retries=0, supports_oneof=True)

    assert await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}) == {"ok": 1}
    assert await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}) == {"ok": 2}

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_gateway_error_does_not_downgrade() -> None:
    """502 is not in _RETRYABLE_STATUS_CODES (not worth retrying), but it is still
    a gateway fault rather than a statement about response_format support."""
    transport, fake = _transport([
        _StatusError(502), json.dumps({"ok": 1}), json.dumps({"ok": 2}),
    ], max_retries=0, supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_bad_request_does_downgrade() -> None:
    """The positive counterpart: a 400/404 rejecting the request's own shape IS
    probative, and must still flip the sticky flag."""
    transport, fake = _transport([
        _StatusError(404), json.dumps({"ok": 1}), json.dumps({"ok": 2}),
    ], max_retries=0, supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_object"
    assert transport.supports_oneof_grammar is False


@pytest.mark.asyncio
async def test_timeout_does_not_downgrade() -> None:
    """Same reasoning as the 429 case: a timeout is an infrastructure blip, not
    evidence about the endpoint's schema support."""
    transport, fake = _transport([
        TimeoutError(),             # call 1 strict -> transport timeout
        json.dumps({"ok": 1}),      # call 1 fallback -> ok
        json.dumps({"ok": 2}),      # call 2 strict -> must be attempted again
    ], max_retries=0, supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_downgrade_during_first_call_applies_to_narrowing_retry() -> None:
    """generate_json's controller_step_response narrowing calls _generate_json_once
    a SECOND time. If the first call downgraded, the retry must honor it rather
    than re-probing strict — the retry is a correction, not a fresh probe."""
    transport, fake = _transport([
        RuntimeError("response_format not supported"),          # strict -> fail
        json.dumps({"type": "tool_call", "thought": "x"}),      # fallback -> incomplete
        json.dumps({"type": "tool_call", "thought": "x",
                    "tool": "read_file", "args": {"path": "p"}}),  # narrowed retry
    ])

    result = await transport.generate_json(
        model="m", schema_name="controller_step_response",
        schema={"type": "object"}, system_instructions="", user_payload={},
    )

    assert result["tool"] == "read_file"
    assert len(fake.calls) == 3
    assert [c["response_format"]["type"] for c in fake.calls] == [
        "json_schema", "json_object", "json_object",
    ]


# ---------------------------------------------------------------------------
# Downgrade triggers that arrive as a RESPONSE, not as a raised exception.
#
# The first nine downgrade tests all injected the failure from the fake's
# create(). That missed the highest-frequency real trigger entirely: the request
# succeeds and the *response* is unusable. Those raise plain RuntimeError from
# _extract_text / _parse_output_object with no status_code, so they fall through
# every network-shaped denylist branch.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_truncated_response_does_not_downgrade() -> None:
    """A PERFECTLY enforced grammar still yields unparseable JSON when the model
    runs out of output budget mid-object. CLAUDE.md documents this happening live
    ("legitimately overflowed its 16384-token output budget (a real large file)").
    One oversized edit must not cost the session its schema enforcement."""
    transport, fake = _transport([
        _FakeResponse('{"ok": 1, "body": "def foo(', finish_reason="length"),
        json.dumps({"ok": 1}),      # call 1 fallback
        json.dumps({"ok": 2}),      # call 2 strict -> must be attempted again
    ], supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_prose_instead_of_json_does_downgrade() -> None:
    """The feature's real positive signal: a COMPLETE response (finish_reason
    "stop") that is prose means response_format was silently ignored — the
    LM Studio / vLLM case the whole downgrade exists for."""
    transport, fake = _transport([
        _FakeResponse("Sure! Here is the object you asked for.", finish_reason="stop"),
        json.dumps({"ok": 1}),
        json.dumps({"ok": 2}),
    ], supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_object"
    assert transport.supports_oneof_grammar is False


@pytest.mark.asyncio
async def test_missing_choices_does_not_downgrade() -> None:
    """openrouter/free returns empty choices as a routing artifact."""
    transport, fake = _transport([
        _NoChoicesResponse(), json.dumps({"ok": 1}), json.dumps({"ok": 2}),
    ], supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_no_text_output_does_not_downgrade() -> None:
    """The qwen3 thinking-exhaustion class: the whole output budget goes to
    implicit reasoning tokens and content comes back empty."""
    transport, fake = _transport([
        _FakeResponse(""), json.dumps({"ok": 1}), json.dumps({"ok": 2}),
    ], supports_oneof=True)

    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


# ---------------------------------------------------------------------------
# Streaming path — every controller turn passes on_thinking, so this IS the
# primary chat path, not an edge case.
# ---------------------------------------------------------------------------

class _HttpxLikeError(Exception):
    """Mimics httpx.HTTPError: derives from Exception (NOT OSError) and carries
    no status_code — so neither the OSError branch nor the status-code branch of
    the denylist would catch it on its own."""


class _StreamDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.reasoning = None


class _StreamChoice:
    def __init__(self, content: str | None, finish_reason: str | None = None) -> None:
        self.delta = _StreamDelta(content)
        self.finish_reason = finish_reason


class _StreamChunk:
    def __init__(self, content: str | None, finish_reason: str | None = None) -> None:
        self.choices = [_StreamChoice(content, finish_reason)]


class _GoodStream:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self._content = content
        self._finish_reason = finish_reason

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        yield _StreamChunk(self._content)
        yield _StreamChunk(None, finish_reason=self._finish_reason)


class _BrokenStream:
    """Opens fine, then the connection drops part-way through the response."""

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        yield _StreamChunk('{"ok"')
        raise _HttpxLikeError("connection reset by peer")


@pytest.mark.asyncio
async def test_mid_stream_failure_does_not_downgrade() -> None:
    """The stream OPENED — the endpoint accepted response_format — and then the
    connection broke. That is no evidence at all, and it is the primary chat
    path: _get_completion_output streams whenever on_thinking is callable."""
    transport, fake = _transport([
        _BrokenStream(),            # call 1 strict -> dies mid-response
        _GoodStream(json.dumps({"ok": 1})),   # call 1 fallback
        _GoodStream(json.dumps({"ok": 2})),   # call 2 strict -> attempted again
    ], max_retries=0, supports_oneof=True)

    first = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=lambda _c: None,
    )
    second = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=lambda _c: None,
    )

    assert (first, second) == ({"ok": 1}, {"ok": 2})
    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_truncated_stream_does_not_downgrade() -> None:
    """Truncation detection must work on the streaming path too — the
    finish_reason arrives on the terminal chunk, not on a response object."""
    transport, fake = _transport([
        _GoodStream('{"ok": 1, "body": "def foo(', finish_reason="length"),
        _GoodStream(json.dumps({"ok": 1})),
        _GoodStream(json.dumps({"ok": 2})),
    ], max_retries=0, supports_oneof=True)

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=lambda _c: None)
    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=lambda _c: None)

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_schema"
    assert transport.supports_oneof_grammar is True


@pytest.mark.asyncio
async def test_stream_open_failure_still_downgrades() -> None:
    """The counterpart to the mid-stream case: if the request is rejected before
    a single byte comes back, that CAN be a genuine response_format rejection and
    must stay probative. Splitting the try must not blunt the feature."""
    transport, fake = _transport([
        _StatusError(400),
        _GoodStream(json.dumps({"ok": 1})),
        _GoodStream(json.dumps({"ok": 2})),
    ], max_retries=0, supports_oneof=True)

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=lambda _c: None)
    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={}, on_thinking=lambda _c: None)

    assert len(fake.calls) == 3
    assert fake.calls[2]["response_format"]["type"] == "json_object"
    assert transport.supports_oneof_grammar is False


@pytest.mark.asyncio
async def test_stream_with_thinking_still_returns_a_plain_string() -> None:
    """_stream_with_thinking is called directly by an existing frozen test and by
    generate_text; splitting out _stream_with_finish_reason must not change its
    return type from str."""
    transport, _ = _transport([_GoodStream("hi")])
    result = await transport._stream_with_thinking(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )
    assert result == "hi"
