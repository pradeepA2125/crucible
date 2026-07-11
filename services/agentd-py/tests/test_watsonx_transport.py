from __future__ import annotations

import pytest

import agentd.providers.watsonx_transport as watsonx_transport_module
from agentd.providers.watsonx_transport import (
    WatsonxJsonTransport,
    _extract_json_object,
    _repair_json,
    _split_thinking,
)

# ---------------------------------------------------------------------------
# Fakes for ibm_watsonx_ai SDK objects
# ---------------------------------------------------------------------------

class FakeCredentials:
    def __init__(self, *, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key


class FakeTextChatResponseFormatType:
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    TEXT = "text"


class FakeTextChatResponseJsonSchema:
    def __init__(self, *, name=None, schema=None, strict=None) -> None:
        self.name = name
        self.schema = schema
        self.strict = strict


class FakeTextChatResponseFormat:
    def __init__(self, *, type, json_schema=None) -> None:
        self.type = type
        self.json_schema = json_schema


class FakeTextChatParameters:
    def __init__(self, *, max_tokens=None, response_format=None, guided_json=None) -> None:
        self.max_tokens = max_tokens
        self.response_format = response_format
        self.guided_json = guided_json


class FakeModelInference:
    """Fake ModelInference that records calls and returns configurable responses."""

    # Class-level response shared across instances; override per-test as needed.
    response_text: str = '{"default": true}'
    calls: list[dict] = []

    def __init__(
        self,
        *,
        model_id: str,
        credentials: object,
        project_id: str | None,
        space_id: str | None,
    ) -> None:
        self.model_id = model_id
        self.credentials = credentials
        self.project_id = project_id
        self.space_id = space_id

    async def achat(self, *, messages: list, params: object) -> dict:
        FakeModelInference.calls.append(
            {
                "model_id": self.model_id,
                "messages": messages,
                "params": params,
            }
        )
        return {
            "choices": [
                {"message": {"content": FakeModelInference.response_text}}
            ]
        }


def _patch_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    m = watsonx_transport_module
    monkeypatch.setattr(m, "Credentials", FakeCredentials)
    monkeypatch.setattr(m, "ModelInference", FakeModelInference)
    monkeypatch.setattr(m, "TextChatParameters", FakeTextChatParameters)
    monkeypatch.setattr(m, "TextChatResponseFormat", FakeTextChatResponseFormat)
    monkeypatch.setattr(m, "TextChatResponseJsonSchema", FakeTextChatResponseJsonSchema)
    monkeypatch.setattr(m, "TextChatResponseFormatType", FakeTextChatResponseFormatType)


def _make_transport(monkeypatch: pytest.MonkeyPatch, **kwargs) -> WatsonxJsonTransport:
    _patch_sdk(monkeypatch)
    return WatsonxJsonTransport(
        api_key="test-key",
        project_id="project-1",
        url="https://watsonx.example",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# generate_text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_text_returns_output(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeModelInference.calls.clear()
    FakeModelInference.response_text = "Hello world"
    transport = _make_transport(monkeypatch)

    output = await transport.generate_text(
        model="ibm/granite",
        system_instructions="plan",
        user_payload={"task_id": "task-1"},
    )

    assert output == "Hello world"
    assert FakeModelInference.calls
    call = FakeModelInference.calls[0]
    assert call["model_id"] == "ibm/granite"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "plan"


@pytest.mark.asyncio
async def test_generate_text_fires_on_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeModelInference.calls.clear()
    FakeModelInference.response_text = "<think>some reasoning</think>actual text"
    transport = _make_transport(monkeypatch)

    received: list[str] = []
    output = await transport.generate_text(
        model="ibm/granite",
        system_instructions="plan",
        user_payload={},
        on_thinking=received.append,
    )

    assert received == ["some reasoning"]
    assert output == "actual text"


@pytest.mark.asyncio
async def test_generate_text_on_thinking_none_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeModelInference.response_text = "<think>reasoning</think>result"
    transport = _make_transport(monkeypatch)
    output = await transport.generate_text(
        model="ibm/granite",
        system_instructions="x",
        user_payload={},
        on_thinking=None,
    )
    assert output == "result"


# ---------------------------------------------------------------------------
# generate_json
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_json_parses_plain_json(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeModelInference.calls.clear()
    FakeModelInference.response_text = '{"answer": 42}'
    transport = _make_transport(monkeypatch)

    result = await transport.generate_json(
        model="ibm/granite",
        schema_name="test_schema",
        schema={"type": "object", "properties": {"answer": {"type": "integer"}}},
        system_instructions="answer",
        user_payload={"question": "?"},
    )

    assert result == {"answer": 42}


@pytest.mark.asyncio
async def test_generate_json_passes_response_format_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strict json_schema is always on — response_format=json_schema, strict=True."""
    FakeModelInference.calls.clear()
    FakeModelInference.response_text = '{"ok": true}'
    transport = _make_transport(monkeypatch)

    await transport.generate_json(
        model="ibm/granite",
        schema_name="my_schema",
        schema={"type": "object"},
        system_instructions="",
        user_payload={},
    )

    params = FakeModelInference.calls[0]["params"]
    assert isinstance(params, FakeTextChatParameters)
    assert params.response_format is not None
    assert params.response_format.type == FakeTextChatResponseFormatType.JSON_SCHEMA
    assert params.response_format.json_schema.name == "my_schema"
    assert params.response_format.json_schema.strict is True
    assert params.guided_json is None


@pytest.mark.asyncio
async def test_generate_json_strips_think_block(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeModelInference.response_text = '<think>I need to think</think>{"value": "ok"}'
    transport = _make_transport(monkeypatch)

    received: list[str] = []
    result = await transport.generate_json(
        model="ibm/granite",
        schema_name="s",
        schema={},
        system_instructions="",
        user_payload={},
        on_thinking=received.append,
    )

    assert result == {"value": "ok"}
    assert received == ["I need to think"]


@pytest.mark.asyncio
async def test_generate_json_retries_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transport should retry up to max_retries times on parse failures."""
    attempt_count = 0

    class RetryModelInference(FakeModelInference):
        async def achat(self, *, messages, params) -> dict:
            nonlocal attempt_count
            attempt_count += 1
            text = "not json at all" if attempt_count < 3 else '{"ok": true}'
            return {"choices": [{"message": {"content": text}}]}

    transport = _make_transport(monkeypatch, max_retries=4)
    monkeypatch.setattr(watsonx_transport_module, "ModelInference", RetryModelInference)

    async def _instant_sleep(_: float) -> None:
        pass
    monkeypatch.setattr(watsonx_transport_module.asyncio, "sleep", _instant_sleep)

    result = await transport.generate_json(
        model="ibm/granite",
        schema_name="retry_test",
        schema={},
        system_instructions="",
        user_payload={},
    )
    assert result == {"ok": True}
    assert attempt_count == 3


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_supports_oneof_grammar_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _make_transport(monkeypatch)
    assert transport.supports_oneof_grammar is False


def test_supports_anyof_grammar_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strict json_schema is always on, so anyOf per-variant enforcement is offered."""
    transport = _make_transport(monkeypatch)
    assert transport.supports_anyof_grammar is True


@pytest.mark.asyncio
async def test_aclose_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _make_transport(monkeypatch)
    await transport.aclose()


# ---------------------------------------------------------------------------
# Retry classification (transient vs permanent)
# ---------------------------------------------------------------------------

class _StatusError(Exception):
    """Exception carrying an HTTP status_code, like the watsonx SDK's failures."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_json_retries_transient_then_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 503 (transient) is retried; the eventual success is returned."""
    attempts = 0

    class TransientModel(FakeModelInference):
        async def achat(self, *, messages, params) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise _StatusError("service unavailable", status_code=503)
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    transport = _make_transport(monkeypatch, max_retries=4)
    monkeypatch.setattr(watsonx_transport_module, "ModelInference", TransientModel)
    monkeypatch.setattr(watsonx_transport_module.asyncio, "sleep", lambda _: _noop())

    result = await transport.generate_json(
        model="ibm/granite", schema_name="s", schema={},
        system_instructions="", user_payload={},
    )
    assert result == {"ok": True}
    assert attempts == 2


@pytest.mark.asyncio
async def test_json_no_retry_on_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 (auth) surfaces immediately without burning the retry budget."""
    attempts = 0

    class AuthErrorModel(FakeModelInference):
        async def achat(self, *, messages, params) -> dict:
            nonlocal attempts
            attempts += 1
            raise _StatusError("unauthorized", status_code=401)

    transport = _make_transport(monkeypatch, max_retries=4)
    monkeypatch.setattr(watsonx_transport_module, "ModelInference", AuthErrorModel)

    with pytest.raises(_StatusError):
        await transport.generate_json(
            model="ibm/granite", schema_name="s", schema={},
            system_instructions="", user_payload={},
        )
    assert attempts == 1


@pytest.mark.asyncio
async def test_text_no_retry_on_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate_text also surfaces a permanent error immediately (no 75s backoff)."""
    attempts = 0

    class AuthErrorModel(FakeModelInference):
        async def achat(self, *, messages, params) -> dict:
            nonlocal attempts
            attempts += 1
            raise _StatusError("forbidden", status_code=403)

    transport = _make_transport(monkeypatch, max_retries=4)
    monkeypatch.setattr(watsonx_transport_module, "ModelInference", AuthErrorModel)

    with pytest.raises(_StatusError):
        await transport.generate_text(
            model="ibm/granite", system_instructions="", user_payload={},
        )
    assert attempts == 1


async def _noop() -> None:
    return None


# ---------------------------------------------------------------------------
# Constructor guards
# ---------------------------------------------------------------------------

def test_requires_project_or_space(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sdk(monkeypatch)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    monkeypatch.delenv("WATSONX_SPACE_ID", raising=False)

    with pytest.raises(RuntimeError, match="WATSONX_PROJECT_ID or WATSONX_SPACE_ID"):
        WatsonxJsonTransport(api_key="test-key", project_id=None, space_id=None)


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------

def test_split_thinking_no_tags() -> None:
    thinking, remainder = _split_thinking("plain text")
    assert thinking == ""
    assert remainder == "plain text"


def test_split_thinking_closed_tags() -> None:
    thinking, remainder = _split_thinking("<think>reason</think>result")
    assert thinking == "reason"
    assert remainder == "result"


def test_split_thinking_unclosed_tag() -> None:
    thinking, remainder = _split_thinking("<think>partial reasoning")
    assert thinking == "partial reasoning"
    assert remainder == ""


def test_repair_json_unquoted_keys() -> None:
    repaired = _repair_json('{type: "tool_call", value: 1}')
    assert '"type"' in repaired
    assert '"value"' in repaired


def test_extract_json_object_clean() -> None:
    result = _extract_json_object('{"x": 1}', "test")
    assert result == {"x": 1}


def test_extract_json_object_with_prefix_garbage() -> None:
    result = _extract_json_object('some preamble {"x": 2}', "test")
    assert result == {"x": 2}


def test_extract_json_object_no_json_raises() -> None:
    with pytest.raises(RuntimeError, match="no JSON object"):
        _extract_json_object("no braces here", "test")


def test_extract_json_object_repairs_unquoted_keys() -> None:
    result = _extract_json_object('{key: "val"}', "test")
    assert result == {"key": "val"}
