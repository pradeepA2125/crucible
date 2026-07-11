"""Tests for OllamaJsonTransport — mirrors the Gemini transport's coverage."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from agentd.providers.ollama_transport import (
    OllamaJsonTransport,
    _parse_output_object,
    _repair_json,
    _split_thinking,
    strip_json_code_fences,
)


@dataclass
class _FakeRequest:
    method: str = "POST"
    url: str = "http://localhost:11434/api/chat"


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict[str, Any] | str

    @property
    def text(self) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)

    @property
    def request(self) -> _FakeRequest:
        return _FakeRequest()

    def json(self) -> dict[str, Any]:
        if isinstance(self.payload, str):
            raise json.JSONDecodeError("not json", self.payload, 0)
        return self.payload


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "body": json})
        out = self._responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    async def aclose(self) -> None:
        self.closed = True


def _ok_response(content: str, prompt_tokens: int = 10, eval_tokens: int = 5) -> _FakeResponse:
    return _FakeResponse(
        status_code=200,
        payload={
            "model": "qwen2.5-coder:7b",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "prompt_eval_count": prompt_tokens,
            "eval_count": eval_tokens,
            "total_duration": 1_200_000_000,  # 1.2s in ns
        },
    )


@pytest.mark.asyncio
async def test_ollama_transport_sends_expected_request_shape() -> None:
    client = _FakeAsyncClient([_ok_response(json.dumps({"ok": True}))])
    transport = OllamaJsonTransport(http_client=client)

    payload = await transport.generate_json(
        model="qwen2.5-coder:7b",
        schema_name="plan_document",
        schema={"type": "object"},
        system_instructions="plan",
        user_payload={"task_id": "task-1", "goal": "x"},
    )

    assert payload == {"ok": True}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"].endswith("/api/chat")
    body = call["body"]
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["stream"] is False
    assert body["format"] == {"type": "object"}
    assert body["options"]["temperature"] == 0
    assert "num_ctx" in body["options"]
    assert body["messages"][0] == {"role": "system", "content": "plan"}
    assert body["messages"][1]["role"] == "user"
    assert json.loads(body["messages"][1]["content"]) == {"task_id": "task-1", "goal": "x"}
    assert "think" not in body


@pytest.mark.asyncio
async def test_ollama_transport_generate_text_omits_format() -> None:
    client = _FakeAsyncClient([_ok_response("# Plan\n\n- step")])
    transport = OllamaJsonTransport(http_client=client)

    output = await transport.generate_text(
        model="qwen2.5-coder:7b",
        system_instructions="plan",
        user_payload={"task_id": "task-1"},
    )

    assert output == "# Plan\n\n- step"
    body = client.calls[0]["body"]
    assert "format" not in body
    assert body["options"]["temperature"] == 0


@pytest.mark.asyncio
async def test_ollama_transport_omits_think_field() -> None:
    """think flag was removed — the transport must never send it regardless of model."""
    client = _FakeAsyncClient([_ok_response(json.dumps({"ok": True}))])
    transport = OllamaJsonTransport(http_client=client)

    await transport.generate_json(
        model="qwen3:8b",
        schema_name="plan_document",
        schema={"type": "object"},
        system_instructions="plan",
        user_payload={"task_id": "task-1"},
    )

    assert "think" not in client.calls[0]["body"]


@pytest.mark.asyncio
async def test_ollama_transport_passes_keep_alive_when_set() -> None:
    client = _FakeAsyncClient([_ok_response(json.dumps({"ok": True}))])
    transport = OllamaJsonTransport(http_client=client, keep_alive="10m")

    await transport.generate_json(
        model="qwen2.5-coder:7b",
        schema_name="plan_document",
        schema={"type": "object"},
        system_instructions="plan",
        user_payload={},
    )

    assert client.calls[0]["body"]["keep_alive"] == "10m"


@pytest.mark.asyncio
async def test_ollama_transport_strips_code_fences() -> None:
    fenced = "```json\n" + json.dumps({"ok": True}) + "\n```"
    client = _FakeAsyncClient([_ok_response(fenced)])
    transport = OllamaJsonTransport(http_client=client)

    payload = await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
    )
    assert payload == {"ok": True}


@pytest.mark.asyncio
async def test_ollama_transport_rejects_empty_text_output() -> None:
    client = _FakeAsyncClient([_ok_response("")])
    transport = OllamaJsonTransport(http_client=client)

    with pytest.raises(RuntimeError, match="no text content"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_ollama_transport_rejects_invalid_json() -> None:
    client = _FakeAsyncClient([_ok_response("not-json")])
    transport = OllamaJsonTransport(http_client=client)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_ollama_transport_rejects_non_object_json() -> None:
    # A plain JSON array has no '{', so it's caught as "not valid JSON" (no object).
    # A JSON object wrapping a list would need to be a dict to pass.
    client = _FakeAsyncClient([_ok_response(json.dumps(["x"]))])
    transport = OllamaJsonTransport(http_client=client)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )


# ---------------------------------------------------------------------------
# on_thinking + think-block stripping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_json_strips_think_block_and_fires_callback() -> None:
    content = '<think>reasoning here</think>{"result": 1}'
    client = _FakeAsyncClient([_ok_response(content)])
    transport = OllamaJsonTransport(http_client=client)

    received: list[str] = []
    result = await transport.generate_json(
        model="qwen3:8b",
        schema_name="s",
        schema={"type": "object"},
        system_instructions="",
        user_payload={},
        on_thinking=received.append,
    )

    assert result == {"result": 1}
    assert received == ["reasoning here"]


@pytest.mark.asyncio
async def test_generate_text_strips_think_block_and_fires_callback() -> None:
    content = "<think>some thought</think>actual answer"
    client = _FakeAsyncClient([_ok_response(content)])
    transport = OllamaJsonTransport(http_client=client)

    received: list[str] = []
    result = await transport.generate_text(
        model="qwen3:8b",
        system_instructions="",
        user_payload={},
        on_thinking=received.append,
    )

    assert result == "actual answer"
    assert received == ["some thought"]


@pytest.mark.asyncio
async def test_generate_text_on_thinking_none_does_not_raise() -> None:
    client = _FakeAsyncClient([_ok_response("<think>thought</think>answer")])
    transport = OllamaJsonTransport(http_client=client)
    result = await transport.generate_text(
        model="m", system_instructions="", user_payload={}, on_thinking=None
    )
    assert result == "answer"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_supports_oneof_grammar_is_true() -> None:
    # Ollama routes json_schema to llama-server's GBNF converter — same path as
    # TurboQuant, which has been measured to enforce oneOf cleanly.
    transport = OllamaJsonTransport(http_client=_FakeAsyncClient([]))
    assert transport.supports_oneof_grammar is True


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
    thinking, remainder = _split_thinking("<think>partial")
    assert thinking == "partial"
    assert remainder == ""


def test_repair_json_unquoted_keys() -> None:
    repaired = _repair_json('{type: "x", val: 1}')
    assert '"type"' in repaired
    assert '"val"' in repaired


def test_parse_output_object_clean() -> None:
    assert _parse_output_object('{"x": 1}', "t") == {"x": 1}


def test_parse_output_object_with_prefix_garbage() -> None:
    assert _parse_output_object('preamble {"x": 2}', "t") == {"x": 2}


def test_parse_output_object_repairs_unquoted_keys() -> None:
    assert _parse_output_object('{key: "val"}', "t") == {"key": "val"}


def test_parse_output_object_no_json_raises() -> None:
    with pytest.raises(RuntimeError, match="not valid JSON"):
        _parse_output_object("no braces", "t")


@pytest.mark.asyncio
async def test_ollama_transport_retries_on_503() -> None:
    flaky = [
        _FakeResponse(status_code=503, payload="busy"),
        _ok_response(json.dumps({"ok": True})),
    ]
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)

    result = await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
    )
    assert result == {"ok": True}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_ollama_transport_retries_on_connect_error() -> None:
    flaky: list[Any] = [
        httpx.ConnectError("daemon down"),
        _ok_response(json.dumps({"ok": True})),
    ]
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)

    result = await transport.generate_json(
        model="m",
        schema_name="x",
        schema={"type": "object"},
        system_instructions="s",
        user_payload={},
    )
    assert result == {"ok": True}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_ollama_transport_raises_on_non_retryable_4xx() -> None:
    client = _FakeAsyncClient([_FakeResponse(status_code=404, payload="not found")])
    transport = OllamaJsonTransport(http_client=client, max_retries=2)

    with pytest.raises(RuntimeError, match="404"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_ollama_transport_raises_after_retries_exhausted() -> None:
    flaky = [_FakeResponse(status_code=503, payload="busy")] * 3
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)

    with pytest.raises(RuntimeError, match="failed after 2 retries"):
        await transport.generate_json(
            model="m",
            schema_name="x",
            schema={"type": "object"},
            system_instructions="s",
            user_payload={},
        )
    assert len(client.calls) == 3


def test_ollama_transport_default_host_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    transport = OllamaJsonTransport(http_client=_FakeAsyncClient([]))
    assert transport._host == "http://localhost:11434"


def test_ollama_transport_honors_env_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434/")
    transport = OllamaJsonTransport(http_client=_FakeAsyncClient([]))
    assert transport._host == "http://gpu-box:11434"  # trailing slash stripped
