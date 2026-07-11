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
