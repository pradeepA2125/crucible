import json

import pytest

from agentd.providers.openai_compatible_transport import (
    OpenAICompatibleTransport,
    normalize_base_url,
)
from agentd.providers.openrouter_transport import OpenRouterJsonTransport

# The fake response/completions classes below deliberately duplicate the ones in
# tests/test_openrouter_transport.py: that file is the extraction's regression net and
# must stay byte-for-byte unmodified, so sharing fixtures would couple it to this new
# file and undo exactly the insulation that made the extraction safe to land.


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

    # No on_thinking/on_progress here, so this takes the NON-streaming path and
    # never sends stream_options — one call, not a probe plus a retry.
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

    # No on_thinking/on_progress here, so this takes the NON-streaming path and
    # never sends stream_options — one call, not a probe plus a retry.
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
    """Both reasoning field names default to None — the shape of a content-only
    delta. A test opts into whichever field the endpoint it stands in for sends."""

    def __init__(
        self,
        content: str | None,
        *,
        reasoning: str | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        self.content = content
        self.reasoning = reasoning
        self.reasoning_content = reasoning_content


class _BareStreamDelta:
    """A delta carrying NEITHER reasoning attribute — what a plain non-reasoning
    endpoint sends, and the case getattr's default has to absorb."""

    def __init__(self, content: str | None) -> None:
        self.content = content


class _StreamChoice:
    def __init__(
        self,
        content: str | None,
        finish_reason: str | None = None,
        *,
        delta: object | None = None,
    ) -> None:
        self.delta = _StreamDelta(content) if delta is None else delta
        self.finish_reason = finish_reason


class _StreamChunk:
    def __init__(
        self,
        content: str | None,
        finish_reason: str | None = None,
        *,
        delta: object | None = None,
    ) -> None:
        self.choices = [_StreamChoice(content, finish_reason, delta=delta)]


class _GoodStream:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self._content = content
        self._finish_reason = finish_reason

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        yield _StreamChunk(self._content)
        yield _StreamChunk(None, finish_reason=self._finish_reason)


class _DeltaStream:
    """Streams caller-supplied delta objects verbatim.

    _GoodStream can only express content, so it cannot reach the reasoning
    branch at all — which is exactly why the existing suite never noticed that
    only one of the two ecosystem reasoning fields was read.
    """

    def __init__(self, deltas: list[object], finish_reason: str = "stop") -> None:
        self._deltas = deltas
        self._finish_reason = finish_reason

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for delta in self._deltas:
            yield _StreamChunk(None, delta=delta)
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
    must stay probative. Splitting the try must not blunt the feature.

    The 400 is scripted TWICE because a response_format rejection recurs: the
    stream opener drops `stream_options` and retries once on any non-retryable
    open failure (it cannot tell which parameter was refused, and must not let a
    counter's parameter reach the downgrade check), but dropping it does not fix
    a response_format the endpoint still hates. So a genuinely unsupported
    endpoint costs one extra probe call, once per process, and then downgrades
    exactly as before."""
    transport, fake = _transport([
        _StatusError(400),
        _StatusError(400),
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

    assert len(fake.calls) == 5
    assert "continuous_usage_stats" in fake.calls[0]["stream_options"]
    assert fake.calls[1]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in fake.calls[2]    # ladder exhausted, still rejected
    assert fake.calls[4]["response_format"]["type"] == "json_object"
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


# ------------------------------------------------- streamed reasoning fields
# There is no spec field for streamed reasoning, so the ecosystem uses two
# names. This transport serves BOTH OpenRouter and the generic openai_compatible
# backend, so it has to accept either — reading only `reasoning` silently
# dropped every thinking chunk from NIM/DeepSeek/vLLM-class endpoints.


async def _collect_thinking(transport: OpenAICompatibleTransport) -> tuple[list[str], str]:
    chunks: list[str] = []
    result = await transport._stream_with_thinking(
        {"model": "m", "messages": []}, on_thinking=chunks.append,
    )
    return chunks, result


@pytest.mark.asyncio
async def test_streamed_reasoning_content_field_reaches_on_thinking() -> None:
    """NVIDIA NIM (nvidia/nemotron-3-ultra, observed live): its deltas carry
    `reasoning_content` and never `reasoning`, so the whole turn streamed zero
    thinking events while the same model via Ollama streamed them fine.
    `reasoning_content` is the wider convention (DeepSeek, vLLM, NIM)."""
    transport, _ = _transport([
        _DeltaStream([
            _StreamDelta(None, reasoning_content="weighing "),
            _StreamDelta(None, reasoning_content="the options"),
            _StreamDelta("answer"),
        ]),
    ])

    chunks, result = await _collect_thinking(transport)

    assert chunks == ["weighing ", "the options"]
    assert result == "answer"


@pytest.mark.asyncio
async def test_streamed_reasoning_field_still_reaches_on_thinking() -> None:
    """OpenRouter's (and Groq's) shape. The fix is additive: this path must be
    byte-identical, since OpenRouter sends `reasoning` and no `reasoning_content`."""
    transport, _ = _transport([
        _DeltaStream([
            _StreamDelta(None, reasoning="step one"),
            _StreamDelta("done"),
        ]),
    ])

    chunks, result = await _collect_thinking(transport)

    assert chunks == ["step one"]
    assert result == "done"


@pytest.mark.asyncio
async def test_stream_without_reasoning_never_calls_on_thinking() -> None:
    """Neither field present, both empty, and both None — none of which may
    reach on_thinking. An empty chunk would render as a blank thinking line."""
    transport, _ = _transport([
        _DeltaStream([
            _BareStreamDelta("hel"),                                  # attributes absent
            _StreamDelta("lo", reasoning="", reasoning_content=""),   # present but empty
            _StreamDelta(None),                                       # present but None
        ]),
    ])

    chunks, result = await _collect_thinking(transport)

    assert chunks == []
    assert result == "hello"


@pytest.mark.asyncio
async def test_delta_carrying_both_reasoning_fields_reports_once() -> None:
    """No endpoint is known to populate both, but a proxy that echoed one into
    the other must not double-report the same text into the thinking pane."""
    transport, _ = _transport([
        _DeltaStream([
            _StreamDelta(None, reasoning="thinking…", reasoning_content="thinking…"),
            _StreamDelta("ok"),
        ]),
    ])

    chunks, result = await _collect_thinking(transport)

    assert chunks == ["thinking…"]
    assert result == "ok"


# --------------------------------------------------------- normalize_base_url


def test_normalize_base_url_strips_trailing_slash() -> None:
    assert normalize_base_url("https://x.test/v1/") == "https://x.test/v1"


def test_normalize_base_url_strips_pasted_chat_completions_suffix() -> None:
    """Vendors document the full endpoint URL; pasting it is the predictable mistake."""
    assert normalize_base_url("https://x.test/v1/chat/completions") == "https://x.test/v1"


def test_normalize_base_url_strips_a_trailing_slash_after_chat_completions() -> None:
    """Both mistakes at once — a copied endpoint URL that also ended in a slash."""
    assert normalize_base_url("https://x.test/v1/chat/completions/") == "https://x.test/v1"


def test_normalize_base_url_leaves_no_trailing_slash_behind_the_suffix() -> None:
    """A doubled separator before the suffix must not leak a trailing slash into
    the SDK's base_url — every returned value has the same shape.

    NOT redundant with test_provider_factory.py's normalize test: that one `rstrip`s
    the SDK's base_url before comparing, so it is structurally blind to a trailing
    slash leaking through. This unit test is the sole pin for that invariant.
    """
    assert normalize_base_url("https://x.test/v1//chat/completions") == "https://x.test/v1"


def test_normalize_base_url_passes_through_a_plain_base() -> None:
    assert normalize_base_url("https://integrate.api.nvidia.com/v1") == \
        "https://integrate.api.nvidia.com/v1"


def test_normalize_base_url_handles_none_and_empty() -> None:
    assert normalize_base_url(None) is None
    assert normalize_base_url("") is None
    assert normalize_base_url("   ") is None
    assert normalize_base_url(" / ") is None


# ------------------------------------------------- json_mode="none" (dev escape hatch)
# NVIDIA NIM's grammar enforcement corrupts the JSON escape `\"` when the next literal
# character is a closing bracket, so `pytest.main([__file__, "-v"])` comes back as
# `"-v"})` — silently, as valid JSON. Verified live on nemotron-3-ultra/super: it fires
# with response_format json_schema AND json_object (json_object is grammar-enforced too,
# so the existing sticky downgrade cannot escape it), while the SAME weights on Ollama
# Cloud are clean. Unconstrained, the model escapes correctly — 4/4 with the schema in
# the prompt. This mode is that last rung: no response_format at all.


@pytest.mark.asyncio
async def test_json_mode_none_sends_no_response_format() -> None:
    """The whole point: server-side grammar enforcement is what corrupts the escape,
    so the request must carry no response_format key at all. json_object is NOT a
    substitute — it is grammar-enforced and corrupts identically."""
    transport, fake = _transport([json.dumps({"ok": True})], json_mode="none")

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )

    assert result == {"ok": True}
    assert len(fake.calls) == 1, "no strict attempt should be made first"
    assert "response_format" not in fake.calls[0], fake.calls[0]


@pytest.mark.asyncio
async def test_json_mode_none_still_puts_the_schema_in_the_prompt() -> None:
    """Nothing enforces the shape now, so the schema has to reach the model somehow —
    it rides the system prompt, exactly as in the json_object fallback."""
    transport, fake = _transport(
        [json.dumps({"ok": True})], json_mode="none")

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object", "title": "MARKER_SCHEMA"},
        system_instructions="sys", user_payload={"k": "v"},
    )

    system = fake.calls[0]["messages"][0]["content"]
    assert "MARKER_SCHEMA" in system, system
    assert "sys" in system, system


@pytest.mark.asyncio
async def test_json_mode_none_is_reported_by_the_property() -> None:
    """The validate route reads .json_mode to tell the caller what actually happened."""
    transport, _ = _transport([json.dumps({"ok": True})], json_mode="none")
    assert transport.json_mode == "none"


@pytest.mark.asyncio
async def test_default_json_mode_still_probes_strict() -> None:
    """The escape hatch is opt-in: omitting json_mode must not change any behavior."""
    transport, fake = _transport([json.dumps({"ok": True})])
    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )
    assert transport.json_mode == "strict"
    assert fake.calls[0]["response_format"]["type"] == "json_schema"


# ------------------------------------------------------- live token progress
# Reasoning streams visibly via on_thinking, but CONTENT deltas are accumulated
# silently and only surface when the call returns — a long generation looks frozen
# (observed live: a 15-minute nemotron call with a motionless UI while it worked).
# on_progress reports running counts DURING the call, split by kind.


class _CountingProgress:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, reasoning_n: int, content_n: int) -> None:
        self.calls.append((reasoning_n, content_n))


@pytest.mark.asyncio
async def test_stream_progress_reports_reasoning_and_content_separately() -> None:
    """The split is the point: 'still thinking' and 'writing output' are different
    states to a waiting user. The delta loop already distinguishes them.

    Counts are approximate tokens (~4 chars each), not delta counts — the two
    sides are deliberately different lengths so the assertion shows they are
    accumulated independently rather than coinciding."""
    transport, _ = _transport([])
    transport._completions = _FakeCompletions([
        _DeltaStream([
            _StreamDelta(None, reasoning_content="think about it carefully "),
            _StreamDelta(None, reasoning_content="and then some more "),
            _StreamDelta("out1"),
            _StreamDelta("out2"),
            _StreamDelta("out3"),
        ]),
    ])
    progress = _CountingProgress()

    text, _ = await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None, on_progress=progress,
    )

    assert text == "out1out2out3"
    assert progress.calls, "on_progress was never called"
    assert progress.calls[-1] == (11, 3), progress.calls


@pytest.mark.asyncio
async def test_stream_progress_is_throttled_not_one_call_per_delta() -> None:
    """~29 deltas/sec measured live; one SSE broadcast per delta would flood the
    channel. Throttled emission must still end on the true final totals."""
    deltas = [_StreamDelta(f"c{i}") for i in range(40)]
    transport, _ = _transport([])
    transport._completions = _FakeCompletions([_DeltaStream(deltas)])
    progress = _CountingProgress()

    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None, on_progress=progress,
    )

    assert len(progress.calls) < 40, f"not throttled: {len(progress.calls)} calls"
    assert progress.calls[-1] == (0, 28), progress.calls   # 110 chars of content


@pytest.mark.asyncio
async def test_stream_without_on_progress_is_unchanged() -> None:
    """Opt-in: every existing caller passes no on_progress and must be unaffected."""
    transport, _ = _transport([])
    transport._completions = _FakeCompletions([
        _DeltaStream([_StreamDelta("a"), _StreamDelta("b")]),
    ])

    text, _ = await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )

    assert text == "ab"


@pytest.mark.asyncio
async def test_json_mode_none_still_reports_token_progress() -> None:
    """Regression: progress was plumbed only into the STRICT path's
    _get_completion_output. In json_mode="none" (and after any sticky downgrade)
    every call skips strict and goes through _json_object_fallback ->
    _get_completion_text, so the counter silently never fired — the exact config
    the escape-corruption workaround runs in. Caught on the wire, not by the unit
    tests, which called _stream_with_finish_reason directly."""
    transport, _ = _transport([], json_mode="none")
    transport._completions = _FakeCompletions([
        _DeltaStream([
            _StreamDelta(None, reasoning_content="hm "),
            _StreamDelta('{"ok"'),
            _StreamDelta(": true}"),
        ]),
    ])
    progress = _CountingProgress()

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
        on_thinking=lambda _c: None, on_progress=progress,
    )

    assert result == {"ok": True}
    assert progress.calls, "on_progress never fired on the json_object/none path"
    assert progress.calls[-1] == (1, 3), progress.calls


# ------------------------------------------------- malformed JSON: repair + fail fast
# Observed live: three identical ~1500-token regenerations of one long prose answer,
# each unparseable. The retry re-sent BYTE-IDENTICAL messages, so resampling just
# reproduced an input-determined failure — 4 attempts, ~6000 tokens, 35s of backoff,
# and the model never learned anything. Three transports (ollama/turboquant/watsonx)
# already repair locally; this one had no repair at all.


class _CountingCompletions(_FakeCompletions):
    """Counts how many times the endpoint was actually hit."""


@pytest.mark.asyncio
async def test_unrepairable_json_fails_fast_instead_of_retrying_blind() -> None:
    """Blind retry is right for a 503 and wrong for malformed output: the input is
    identical, so the failure is deterministic. Fail on the FIRST unrepairable parse
    so the loop layer — which knows the schema and can say something useful — gets
    to correct the model an attempt later instead of four."""
    transport, fake = _transport(["this is prose, not JSON at all"], json_mode="none")

    with pytest.raises(RuntimeError) as excinfo:
        await transport.generate_json(
            model="m", schema_name="s", schema={"type": "object"},
            system_instructions="sys", user_payload={"k": "v"},
        )

    assert len(fake.calls) == 1, f"retried blind {len(fake.calls)} times"
    # The offending text must ride the exception — the retry path previously logged
    # only an attempt counter, making this failure class undiagnosable from logs.
    assert "prose, not JSON" in str(excinfo.value), str(excinfo.value)




# ------------------------------------------- error window instead of the head
# Live failure: the model was told "Invalid control character at column 218" and
# shown output_text[:500], which the loop then capped to 300 — leaving only 178
# chars of payload. The offending character at 217 was in the truncated-away part,
# so it received a precise coordinate into text it could not see, four times in a
# row. The head is also the least useful part: '{"type":"answer","thought":"…' is
# always well-formed. Show a window around the error, like a compiler does.


def test_error_window_centres_on_the_failure_not_the_head() -> None:
    from agentd.providers.openai_compatible_transport import _error_window

    body = '{"type":"answer","answer":"' + ("x" * 400) + "\n" + ("y" * 400) + '"}'
    pos = body.index("\n")

    window = _error_window(body, pos)

    assert len(window) < len(body), "window must be shorter than the payload"
    assert "xxx" in window and "yyy" in window, window   # both sides of the error
    assert not window.startswith('{"type"'), "showed the head, not the error site"


def test_error_window_marks_the_offending_position() -> None:
    from agentd.providers.openai_compatible_transport import _error_window

    body = "A" * 100 + "\t" + "B" * 100
    window = _error_window(body, 100)

    assert "◀" in window or "HERE" in window, window


def test_error_window_returns_short_payloads_whole() -> None:
    from agentd.providers.openai_compatible_transport import _error_window

    body = '{"a": 1,}'
    assert "{" in _error_window(body, 8)


@pytest.mark.asyncio
async def test_parse_error_message_shows_the_bytes_around_the_failure() -> None:
    """End to end: the RuntimeError the loop appends must carry the error SITE, and
    stay short enough to survive the loop's 300-char cap."""
    long_prose = "word " * 120
    bad = '{"type":"answer","thought":"t","answer":"# Title\n' + long_prose + '"}'
    transport, _ = _transport([bad], json_mode="none")

    with pytest.raises(RuntimeError) as excinfo:
        await transport.generate_json(
            model="m", schema_name="s", schema={"type": "object"},
            system_instructions="sys", user_payload={"k": "v"},
        )

    msg = str(excinfo.value)
    assert "Invalid control character" in msg, msg
    assert "# Title" in msg, "the error site is not in the message"
    assert len(msg) < 400, f"message too long to survive the 300-char cap: {len(msg)}"


@pytest.mark.asyncio
async def test_no_regex_repair_is_applied_to_payloads_carrying_code() -> None:
    """REGRESSION: a borrowed 'repair unquoted property names' regex ran over the whole
    JSON text, string values included. Python type annotations look identical to
    unquoted JSON keys, so it rewrote

        def __init__(self, x: int = 0, y: int = 0)
    into
        def __init__(self, "x": int = 0, "y": int = 0)

    corrupting valid code inside a create_file `content` field and reporting the
    damage it had just caused ("after repair, Expecting ',' delimiter"). Observed
    live on two consecutive edits. A malformed payload must be reported AS THE MODEL
    WROTE IT — the model can only fix what it actually emitted."""
    body = ('{"type":"edit","patch_ops":[{"op":"create_file","file":"e.py",'
            '"content":"def __init__(self, x: int = 0, y: int = 0):\n    pass"}]}')
    transport, _ = _transport([body], json_mode="none")

    with pytest.raises(RuntimeError) as excinfo:
        await transport.generate_json(
            model="m", schema_name="s", schema={"type": "object"},
            system_instructions="sys", user_payload={"k": "v"},
        )

    msg = str(excinfo.value)
    assert "after repair" not in msg, msg
    assert '"x":' not in msg, f"repair corrupted the reported payload: {msg}"


@pytest.mark.asyncio
async def test_transient_stream_failure_is_retried_not_reported_as_model_failure() -> None:
    """Live: NIM returned 'ResourceExhausted: Worker local total request limit reached
    (32/32)' mid-stream. It carries no status_code, so _is_retryable missed it, it was
    wrapped as TransientTransportError, and the fallback loop RAISED — burning one of
    the controller's three attempts as though the model had misbehaved. Two arrived a
    second apart and never reached the model at all, so its thinking showed no error.

    TransientTransportError is documented as 'infrastructure failure… says nothing
    about the request's own shape' — it is the one class that SHOULD be retried here."""
    calls = {"n": 0}

    class _ExhaustedThenOk:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            calls["n"] += 1
            if calls["n"] == 1:
                yield _StreamChunk("partial")
                raise RuntimeError("ResourceExhausted: Worker local total request "
                                   "limit reached (32/32)")
            yield _StreamChunk(json.dumps({"ok": True}))
            yield _StreamChunk(None, finish_reason="stop")

    transport, _ = _transport([], json_mode="none")
    transport._completions = _FakeCompletions([_ExhaustedThenOk(), _ExhaustedThenOk()])

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
        on_thinking=lambda _c: None,
    )

    assert result == {"ok": True}, "a transient stream failure must be retried"


# ------------------------------------------- salvage the first object (raw_decode)
# This transport was the ONLY one using bare json.loads; ollama/turboquant/watsonx all
# use raw_decode. json.loads rejects the WHOLE response on trailing data, so a model
# that emitted two actions in one reply cost the entire generation. Measured live:
# 19,772 / 20,878 / 26,714 / 30,681 / 39,225 chars discarded, each regenerated on
# retry. The first object was complete and valid every time.
#
# Safe by protocol: exactly one action per response is the contract, so the FIRST
# object IS the action; anything after it is surplus the loop will re-derive next
# iteration.


@pytest.mark.asyncio
async def test_trailing_second_action_salvages_the_first_object() -> None:
    """The live 39K-char shape: a complete tool_call, a blank line, then a second
    complete action. Previously 'Extra data: line 3 column 1' and the lot discarded."""
    body = ('{"type":"tool_call","tool":"write_todos","args":{"items":[{"note":"loop"}]}}'
            '\n\n{"type":"edit","thought":"Creating game_loop.py"}')
    transport, fake = _transport([body], json_mode="none")

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )

    assert result["type"] == "tool_call"
    assert result["tool"] == "write_todos"
    assert len(fake.calls) == 1, "salvage must not cost a retry"




@pytest.mark.asyncio
async def test_genuinely_broken_json_still_raises_with_the_error_window() -> None:
    """Salvage must not mask a real failure: if the FIRST object is itself invalid
    there is nothing to recover, and the model still needs what/where/why."""
    body = '{"type":"answer","answer":"line one\nline two"}'   # raw control char
    transport, _ = _transport([body], json_mode="none")

    with pytest.raises(RuntimeError) as excinfo:
        await transport.generate_json(
            model="m", schema_name="s", schema={"type": "object"},
            system_instructions="sys", user_payload={"k": "v"},
        )

    msg = str(excinfo.value)
    assert "Invalid control character" in msg, msg
    assert "HERE" in msg, msg


@pytest.mark.asyncio
async def test_salvage_reports_the_discard_to_the_caller() -> None:
    """A silent discard desynchronises the model from reality: it emitted two actions,
    we ran the first and dropped the second, and its history shows only the first's
    result — so it can believe the second happened. The transport reports the fact;
    the LOOP owns telling the model (same split as on_thinking/on_retry/on_progress)."""
    body = ('{"type":"tool_call","tool":"write_todos","args":{"items":[]}}'
            '\n\n{"type":"edit","thought":"Creating game_loop.py"}')
    transport, _ = _transport([body], json_mode="none")
    salvaged: list[tuple[int, str]] = []

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
        on_salvage=lambda n, text: salvaged.append((n, text)),
    )

    assert result["tool"] == "write_todos"
    assert salvaged, "the discard was not reported"
    discarded_chars, discarded_text = salvaged[0]
    assert discarded_chars > 0
    assert '"type":"edit"' in discarded_text, discarded_text


@pytest.mark.asyncio
async def test_no_salvage_callback_when_nothing_was_discarded() -> None:
    """A clean single-object response must not report a phantom discard."""
    transport, _ = _transport([json.dumps({"type": "answer", "answer": "hi"})],
                              json_mode="none")
    salvaged: list[tuple[int, str]] = []

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
        on_salvage=lambda n, text: salvaged.append((n, text)),
    )

    assert salvaged == []


# --------------------------------- salvage ONLY a genuine second action
# Live regression from the first cut of this: a response with just 2 trailing chars
# ("}) was "salvaged", but a 2-char remainder means the object CLOSED EARLY — an
# unescaped quote ended a string prematurely — so the parsed object held TRUNCATED
# content. The edit applied and preflight caught "unterminated string literal at line
# 468" in the generated Python. Silent truncation is worse than a loud parse failure.




@pytest.mark.asyncio
async def test_trailing_object_is_still_salvaged() -> None:
    """The 39K case stands: trailing content that STARTS a new object is a genuine
    second action, and the first is safe to execute."""
    body = ('{"type":"tool_call","tool":"write_todos","args":{"items":[]}}'
            '\n\n{"type":"edit","thought":"Creating game_loop.py"}')
    transport, _ = _transport([body], json_mode="none")

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )

    assert result["tool"] == "write_todos"


def test_trailing_remainder_message_does_not_assert_a_cause() -> None:
    """Two different faults produce an identical short remainder and I cannot tell
    them apart from the bytes: `"}` was a genuine early close (content truncated,
    live: "unterminated string literal at line 468"), while `]}` followed a complete,
    properly-quoted todo list — surplus brackets. Asserting "the content is truncated"
    is wrong half the time, and a confidently-wrong correction is the exact failure
    this whole line of work has been chasing. State the fact, name both causes."""
    from agentd.providers.openai_compatible_transport import _trailing_remainder_message

    msg = _trailing_remainder_message(2)

    assert "2" in msg
    assert "bracket" in msg.lower(), msg
    # Must NOT claim to know which cause it was.
    assert "is truncated" not in msg, msg


@pytest.mark.asyncio
async def test_short_trailing_remainder_is_salvaged_not_raised() -> None:
    """A short remainder is AMBIGUOUS — `]}` follows a complete object (surplus
    brackets), `"}` follows one that closed early (truncated content). Identical bytes,
    so a guard can only guess. It doesn't need to: preflight is the net, and caught the
    live truncation as "unterminated string literal at line 468" before disk. Raising
    would cost a full regeneration (~10K tokens) in BOTH cases; salvaging costs nothing
    when the object is fine and one rejected edit when it isn't."""
    body = '{"type":"edit","patch_ops":[{"content":"def f(): pass"}]}]}'
    transport, fake = _transport([body], json_mode="none")

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )

    assert result["type"] == "edit"
    assert len(fake.calls) == 1, "salvage must not cost a retry"


# ---------------------------------------- one-shot unconstrained escape hatch
# Strict is the better default: a grammar makes the ENTIRE JSON-validity failure
# class structurally impossible (Extra data, Invalid control character, unescaped
# quotes, early closes — every failure observed in a night of running `none`).
# Its one defect is NIM's silent escape corruption, which turns `print("hello")`
# into `print("hello"}` — invalid JSON is not produced, invalid CODE is, so it
# surfaces at preflight rather than here.
#
# So: strict every call, and when preflight rejects the generated code, the loop
# asks for ONE unconstrained call. Per-call, not sticky — a genuine model syntax
# error (a missing colon, a stray quote) then costs one call of lost grammar, not
# the rest of the session.


@pytest.mark.asyncio
async def test_unconstrained_call_sends_no_response_format() -> None:
    """The escape hatch: this one call skips the grammar entirely."""
    transport, fake = _transport([json.dumps({"ok": True})])   # default json_mode=strict

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"}, unconstrained=True,
    )

    assert "response_format" not in fake.calls[0], fake.calls[0]


@pytest.mark.asyncio
async def test_unconstrained_call_salvages_trailing_content() -> None:
    """It must also get raw_decode, since without a grammar the model may append a
    second action — the whole reason `none` needs salvage."""
    body = ('{"type":"tool_call","tool":"write_todos","args":{"items":[]}}'
            '\n\n{"type":"edit","thought":"x"}')
    transport, _ = _transport([body])

    result = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"}, unconstrained=True,
    )

    assert result["tool"] == "write_todos"


@pytest.mark.asyncio
async def test_unconstrained_does_not_persist_to_the_next_call() -> None:
    """Auto-reverting by construction: the flag is per-call, so the very next call is
    constrained again. A sticky downgrade would lose grammar enforcement for the whole
    process on one false positive."""
    transport, fake = _transport([json.dumps({"ok": True}), json.dumps({"ok": True})])

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={}, unconstrained=True)
    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={})

    assert "response_format" not in fake.calls[0]
    assert fake.calls[1]["response_format"]["type"] == "json_schema", fake.calls[1]
    assert transport.json_mode == "strict", "one unconstrained call must not downgrade"


# ── on_progress reports token-ish counts, not delta counts ────────────────────
# A structured-output endpoint routinely returns the whole JSON action in ONE
# content delta. Counting deltas then reports "1" for a 300-char tool call while
# the UI labels it "output tokens" — the number is meaningless exactly where the
# user is watching it. These pin the counts to the produced text, not to how the
# server happened to chunk it.


@pytest.mark.asyncio
async def test_progress_counts_output_tokens_not_deltas() -> None:
    action = '{"type":"tool_call","tool":"read_file","args":{"path":"a.py"}}'
    transport, _ = _transport([_GoodStream(action)])
    seen: list[tuple[int, int]] = []
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_progress=lambda r, c: seen.append((r, c)),
    )
    assert seen, "on_progress never fired"
    _reasoning, content = seen[-1]
    # The whole action arrived as a single delta; a delta count would report 1.
    assert content > 1, f"counted deltas, not tokens: {content}"
    # ~4 chars per token, so a 61-char action is roughly 15 tokens — assert the
    # order of magnitude rather than an exact tokenizer result.
    assert 8 <= content <= 30, f"implausible token estimate for {len(action)} chars: {content}"


@pytest.mark.asyncio
async def test_progress_counts_reasoning_tokens_not_deltas() -> None:
    reasoning = "I need to read the file first. " * 8   # 248 chars, one delta
    stream = _DeltaStream([_StreamDelta(None, reasoning=reasoning)])
    transport, _ = _transport([stream])
    seen: list[tuple[int, int]] = []
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_progress=lambda r, c: seen.append((r, c)),
    )
    assert seen, "on_progress never fired"
    reasoning_n, _content = seen[-1]
    assert reasoning_n > 1, f"counted deltas, not tokens: {reasoning_n}"


@pytest.mark.asyncio
async def test_progress_accumulates_across_deltas() -> None:
    """A server that DOES chunk must still produce a monotonically rising count."""
    stream = _DeltaStream([
        _StreamDelta("word one here "),
        _StreamDelta("word two here "),
        _StreamDelta("word three here "),
    ])
    transport, _ = _transport([stream])
    seen: list[tuple[int, int]] = []
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_progress=lambda r, c: seen.append((r, c)),
    )
    counts = [c for _r, c in seen]
    assert counts == sorted(counts), f"output count went backwards: {counts}"
    assert counts[-1] > 1


@pytest.mark.asyncio
async def test_streams_for_progress_even_without_a_thinking_callback() -> None:
    """The streaming branch was gated on on_thinking, not on on_progress, so a
    caller wanting only the counter silently got the NON-streaming path: no
    deltas, no progress, and the whole response landing in one lump at the end.
    Nothing hits that combination today, which is exactly why it would rot."""
    transport, _ = _transport([])
    transport._completions = _FakeCompletions([_DeltaStream([_StreamDelta("hello there")])])
    progress = _CountingProgress()

    text, _ = await transport._get_completion_output(
        {"model": "m", "messages": []}, None, None, progress,
    )

    assert text == "hello there"
    assert progress.calls, "no progress: the call did not stream"


@pytest.mark.asyncio
async def test_generate_text_streaming_forwards_progress() -> None:
    """_stream_with_thinking dropped on_progress on the floor, so generate_text
    could never report a count however long it ran."""
    transport, _ = _transport([])
    transport._completions = _FakeCompletions([_DeltaStream([_StreamDelta("some answer text")])])
    progress = _CountingProgress()

    result = await transport._stream_with_thinking(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None, on_progress=progress,
    )

    assert result == "some answer text"
    assert progress.calls, "generate_text's streaming path reported no progress"


# ── exact token counts from the stream's usage chunk ──────────────────────────
# A provider that reports usage sends ONE extra final chunk with empty choices
# and a usage object. The delta loop's `if not choices: continue` skipped exactly
# that chunk, so an exact count we were already being handed was thrown away.
# The live climb stays estimated (no per-delta counts exist in the format); only
# the closing number — the one left on screen — becomes exact.


class _UsageDetails:
    def __init__(self, reasoning_tokens: int) -> None:
        self.reasoning_tokens = reasoning_tokens


class _Usage:
    def __init__(
        self,
        completion_tokens: int,
        reasoning_tokens: int | None = None,
        prompt_tokens: int = 0,
    ) -> None:
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = (
            _UsageDetails(reasoning_tokens) if reasoning_tokens is not None else None
        )
        self.prompt_tokens = prompt_tokens


class _UsageChunk:
    """Final chunk of a usage-reporting stream: no choices, just totals."""

    def __init__(self, usage: object) -> None:
        self.choices = []
        self.usage = usage


class _StreamThenUsage:
    def __init__(self, deltas: list[object], usage: object) -> None:
        self._deltas = deltas
        self._usage = usage

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for d in self._deltas:
            yield _StreamChunk(None, delta=d)
        yield _UsageChunk(self._usage)


async def _run_progress(transport: object, stream: object) -> list[tuple[int, int]]:
    transport._completions = _FakeCompletions([stream])   # type: ignore[attr-defined]
    seen: list[tuple[int, int]] = []
    await transport._stream_with_finish_reason(   # type: ignore[attr-defined]
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_progress=lambda r, c: seen.append((r, c)),
    )
    return seen


@pytest.mark.asyncio
async def test_exact_content_tokens_replace_the_estimate() -> None:
    """No reasoning in the response, so completion_tokens is all content."""
    transport, _ = _transport([])
    seen = await _run_progress(
        transport,
        _StreamThenUsage([_StreamDelta("a" * 400)], _Usage(completion_tokens=57)),
    )
    assert seen[-1] == (0, 57), f"estimate not replaced by exact usage: {seen[-1]}"


@pytest.mark.asyncio
async def test_usage_splits_reasoning_from_content_when_details_are_present() -> None:
    """completion_tokens INCLUDES reasoning tokens; the details break them out."""
    transport, _ = _transport([])
    seen = await _run_progress(
        transport,
        _StreamThenUsage(
            [_StreamDelta(None, reasoning_content="x" * 300), _StreamDelta("y" * 80)],
            _Usage(completion_tokens=100, reasoning_tokens=80),
        ),
    )
    assert seen[-1] == (80, 20), f"bad split: {seen[-1]}"


@pytest.mark.asyncio
async def test_usage_without_a_breakdown_apportions_the_exact_total() -> None:
    """Measured live on NVIDIA NIM: a reasoning model reports an exact
    completion_tokens and NO completion_tokens_details. Bailing to independent
    estimates there would waste the one exact number in the call, so the total
    is split on the character ratio actually observed on the wire — exact in
    total, derived in split."""
    transport, _ = _transport([])
    seen = await _run_progress(
        transport,
        _StreamThenUsage(
            [_StreamDelta(None, reasoning_content="x" * 400), _StreamDelta("y" * 100)],
            _Usage(completion_tokens=137),
        ),
    )
    reasoning, content = seen[-1]
    # 400:100 chars => 80% reasoning. The parts must sum to the exact total.
    assert reasoning + content == 137, f"total not preserved: {seen[-1]}"
    assert reasoning == 110 and content == 27, f"bad apportionment: {seen[-1]}"


@pytest.mark.asyncio
async def test_usage_as_a_plain_dict_is_read() -> None:
    """Not every SDK/server surfaces usage as an attribute object."""
    transport, _ = _transport([])
    seen = await _run_progress(
        transport,
        _StreamThenUsage([_StreamDelta("a" * 400)], {"completion_tokens": 33}),
    )
    assert seen[-1] == (0, 33), f"dict-shaped usage ignored: {seen[-1]}"


@pytest.mark.asyncio
async def test_no_usage_chunk_still_reports_the_estimate() -> None:
    """The overwhelming majority of endpoints send no usage at all."""
    transport, _ = _transport([])
    transport._completions = _FakeCompletions([_DeltaStream([_StreamDelta("a" * 400)])])
    seen: list[tuple[int, int]] = []
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_progress=lambda r, c: seen.append((r, c)),
    )
    assert seen[-1] == (0, 100), f"estimate lost: {seen[-1]}"


# ── Stage 2: asking for the usage chunk, safely ───────────────────────────────


class _RejectThenAccept:
    """An endpoint that 400s on the unknown stream_options parameter, then works
    once it is dropped. Records the kwargs of every attempt."""

    def __init__(self, stream: object) -> None:
        self.seen: list[dict] = []
        self._stream = stream

    async def create(self, **kwargs: object) -> object:
        self.seen.append(kwargs)
        if "stream_options" in kwargs:
            raise _StatusError(400)
        return self._stream


@pytest.mark.asyncio
async def test_asks_for_usage_on_the_streaming_call() -> None:
    transport, _ = _transport([])
    fake = _FakeCompletions([_DeltaStream([_StreamDelta("hi")])])
    transport._completions = fake
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )
    assert fake.calls, "harness did not record kwargs"
    assert fake.calls[-1].get("stream_options") == {
        "include_usage": True, "continuous_usage_stats": True,
    }


@pytest.mark.asyncio
async def test_drops_stream_options_when_the_endpoint_rejects_it() -> None:
    """And never lets that rejection escape — a 400 reaching the caller would be
    fed to proves_json_schema_unsupported and permanently downgrade JSON mode."""
    transport, _ = _transport([])
    fake = _RejectThenAccept(_DeltaStream([_StreamDelta("hi")]))
    transport._completions = fake

    text, _ = await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )

    assert text == "hi"
    # Two probes: the vLLM extension, then include_usage alone, then bare.
    assert len(fake.seen) == 3, f"expected the full ladder, got {len(fake.seen)}"
    assert "continuous_usage_stats" in fake.seen[0]["stream_options"]
    assert fake.seen[1]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in fake.seen[2]


@pytest.mark.asyncio
async def test_stream_options_rejection_is_sticky_for_the_process() -> None:
    """One wasted call per process, not one per turn."""
    transport, _ = _transport([])
    fake = _RejectThenAccept(_DeltaStream([_StreamDelta("hi")]))
    transport._completions = fake
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )
    fake._stream = _DeltaStream([_StreamDelta("again")])
    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )
    # 3 for the first call's full ladder, 1 for the second — the ladder is not
    # re-walked once it has bottomed out.
    assert len(fake.seen) == 4, f"re-probed the ladder: {len(fake.seen)} attempts"
    assert "stream_options" not in fake.seen[3]


# ── per-chunk usage: exact live counts, exact split ───────────────────────────
# Measured on NVIDIA NIM: with continuous_usage_stats every one of 1968 chunks
# carried a running completion_tokens, and reasoning/content chunks never
# overlapped (432 reasoning-only, 1533 content-only, 0 both). So each chunk's
# increment is attributable to the phase that chunk carried, and the split needs
# no completion_tokens_details — which NIM does not publish.


class _ChunkWithUsage:
    def __init__(self, delta: object | None, completion_tokens: int) -> None:
        self.choices = [_StreamChoice(None, delta=delta)] if delta is not None else []
        self.usage = _Usage(completion_tokens=completion_tokens)


class _ContinuousStream:
    def __init__(self, items: list[tuple[object | None, int]]) -> None:
        self._items = items

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for delta, total in self._items:
            yield _ChunkWithUsage(delta, total)


@pytest.mark.asyncio
async def test_per_chunk_usage_attributes_each_increment_to_its_phase() -> None:
    transport, _ = _transport([])
    stream = _ContinuousStream([
        (_StreamDelta(None, reasoning_content="think "), 10),
        (_StreamDelta(None, reasoning_content="more "), 25),
        (_StreamDelta("out "), 40),
        (_StreamDelta("more out"), 60),
    ])
    seen = await _run_progress(transport, stream)
    # 25 reasoning (10 + 15), 35 content (15 + 20) — exact, summing to 60.
    assert seen[-1] == (25, 35), seen[-1]


@pytest.mark.asyncio
async def test_per_chunk_usage_climbs_live_rather_than_landing_at_the_end() -> None:
    """The whole point: a number that increases while the model works."""
    transport, _ = _transport([])
    stream = _ContinuousStream([
        (_StreamDelta("a"), 10), (_StreamDelta("b"), 20),
        (_StreamDelta("c"), 30), (_StreamDelta("d"), 40),
    ])
    seen = await _run_progress(transport, stream)
    contents = [c for _r, c in seen]
    assert contents == sorted(contents), contents
    assert contents[-1] == 40, contents


@pytest.mark.asyncio
async def test_trailing_usage_only_chunk_is_reconciled_into_content() -> None:
    """The closing chunk carries neither phase, so its tokens are unattributed;
    content absorbs the remainder so the parts sum to the provider's total."""
    transport, _ = _transport([])
    stream = _ContinuousStream([
        (_StreamDelta(None, reasoning_content="t "), 10),
        (_StreamDelta("out"), 30),
        (None, 34),          # usage-only closing chunk
    ])
    seen = await _run_progress(transport, stream)
    reasoning, content = seen[-1]
    assert reasoning + content == 34, seen[-1]
    assert reasoning == 10 and content == 24, seen[-1]


@pytest.mark.asyncio
async def test_stream_options_ladder_falls_back_to_include_usage_only() -> None:
    """An endpoint that knows include_usage but not the vLLM extension must not
    lose usage reporting altogether."""
    transport, _ = _transport([])

    class _RejectContinuousOnly:
        def __init__(self) -> None:
            self.seen: list[dict] = []

        async def create(self, **kwargs):
            self.seen.append(kwargs)
            opts = kwargs.get("stream_options") or {}
            if "continuous_usage_stats" in opts:
                raise _StatusError(400)
            return _DeltaStream([_StreamDelta("hi")])

    fake = _RejectContinuousOnly()
    transport._completions = fake
    text, _ = await transport._stream_with_finish_reason(
        {"model": "m", "messages": []}, on_thinking=lambda _c: None,
    )
    assert text == "hi"
    assert len(fake.seen) == 2
    assert fake.seen[1]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_on_usage_reports_prompt_tokens_once() -> None:
    """The compaction trigger needs the size of what we SENT. It is on the same
    usage object the counters already read, and it fires exactly once — unlike
    on_progress, which is throttled and fires many times per call."""
    transport, _ = _transport([])
    seen: list[tuple[int, int]] = []
    stream = _StreamThenUsage(
        [_StreamDelta("hello there")],
        _Usage(completion_tokens=17, prompt_tokens=4242),
    )
    transport._completions = _FakeCompletions([stream])

    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_usage=lambda p, c: seen.append((p, c)),
    )

    assert seen == [(4242, 17)], seen


@pytest.mark.asyncio
async def test_on_usage_is_silent_when_the_endpoint_reports_nothing() -> None:
    """Most endpoints report no usage at all. A zero would be indistinguishable
    from a real measurement and would poison the trigger."""
    transport, _ = _transport([])
    seen: list[tuple[int, int]] = []
    transport._completions = _FakeCompletions([_DeltaStream([_StreamDelta("hi")])])

    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_usage=lambda p, c: seen.append((p, c)),
    )

    assert seen == [], seen
