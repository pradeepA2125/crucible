import json

import pytest

from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.openrouter_transport import OpenRouterJsonTransport


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
