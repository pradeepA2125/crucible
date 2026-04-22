from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

import agentd.providers.groq_transport as groq_transport_module
from agentd.providers.groq_transport import GroqJsonTransport


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list[FakeChoice]


class FakeCompletionsClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_groq_transport_sends_expected_request_shape() -> None:
    client = FakeCompletionsClient(
        responses=[
            FakeResponse(
                choices=[
                    FakeChoice(message=FakeMessage(content=json.dumps({"ok": True}))),
                ]
            )
        ]
    )
    transport = GroqJsonTransport(api_key="test-key", completions_client=client, max_tokens=111)

    payload = await transport.generate_json(
        model="llama-3.3-70b-versatile",
        schema_name="plan_document",
        schema={"type": "object"},
        system_instructions="plan",
        user_payload={"task_id": "task-1"},
    )

    assert payload == {"ok": True}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "llama-3.3-70b-versatile"
    assert call["max_completion_tokens"] == 111
    assert call["temperature"] == 1
    assert call["include_reasoning"] is False
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "plan"
    assert call["messages"][1]["role"] == "user"
    assert json.loads(call["messages"][1]["content"]) == {"task_id": "task-1"}
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["name"] == "plandocument"
    assert call["response_format"]["json_schema"]["schema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_groq_transport_generate_text_returns_content() -> None:
    client = FakeCompletionsClient(
        responses=[FakeResponse(choices=[FakeChoice(message=FakeMessage(content="# Plan"))])]
    )
    transport = GroqJsonTransport(api_key="test-key", completions_client=client, max_tokens=111)

    output = await transport.generate_text(
        model="llama-3.3-70b-versatile",
        system_instructions="plan",
        user_payload={"task_id": "task-1"},
    )

    assert output == "# Plan"
    call = client.calls[0]
    assert call["messages"][0]["content"] == "plan"
    assert "response_format" not in call


@pytest.mark.asyncio
async def test_groq_transport_rejects_missing_choices() -> None:
    client = FakeCompletionsClient(responses=[FakeResponse(choices=[])])
    transport = GroqJsonTransport(api_key="test-key", completions_client=client)

    with pytest.raises(RuntimeError, match="missing choices"):
        await transport.generate_json(
            model="llama-3.3-70b-versatile",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_groq_transport_rejects_empty_text_output() -> None:
    client = FakeCompletionsClient(
        responses=[FakeResponse(choices=[FakeChoice(message=FakeMessage(content=""))])]
    )
    transport = GroqJsonTransport(api_key="test-key", completions_client=client)

    with pytest.raises(RuntimeError, match="no text output"):
        await transport.generate_json(
            model="llama-3.3-70b-versatile",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_groq_transport_rejects_invalid_json() -> None:
    client = FakeCompletionsClient(
        responses=[FakeResponse(choices=[FakeChoice(message=FakeMessage(content="not-json"))])]
    )
    transport = GroqJsonTransport(api_key="test-key", completions_client=client)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        await transport.generate_json(
            model="llama-3.3-70b-versatile",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_groq_transport_rejects_non_object_json() -> None:
    client = FakeCompletionsClient(
        responses=[FakeResponse(choices=[FakeChoice(message=FakeMessage(content=json.dumps(["x"])))])]
    )
    transport = GroqJsonTransport(api_key="test-key", completions_client=client)

    with pytest.raises(RuntimeError, match="must be a JSON object"):
        await transport.generate_json(
            model="llama-3.3-70b-versatile",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


def test_groq_transport_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        GroqJsonTransport()


def test_groq_transport_sdk_constructor_uses_timeout_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeSDKClient:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)
            self.chat = type("FakeChat", (), {"completions": object()})()

    monkeypatch.setattr(groq_transport_module, "AsyncGroqClient", FakeSDKClient)

    GroqJsonTransport(
        api_key="test-key",
        endpoint="https://api.groq.com/openai/v1/",
        timeout_sec=12.5,
    )

    assert captured_kwargs["api_key"] == "test-key"
    assert captured_kwargs["timeout"] == 12.5
    assert captured_kwargs["base_url"] == "https://api.groq.com/openai/v1"
