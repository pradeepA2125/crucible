from __future__ import annotations

import json
from typing import Any

import pytest

import agentd.providers.huggingface_transport as hf_transport_module
from agentd.providers.huggingface_transport import HuggingFaceJsonTransport


class FakeInferenceClient:
    def __init__(self, outputs: list[Any]) -> None:
        self._outputs = outputs
        self.calls: list[dict[str, Any]] = []

    def text_generation(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self._outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_huggingface_transport_sends_expected_request_shape() -> None:
    fake_client = FakeInferenceClient(outputs=[json.dumps({"ok": True})])
    transport = HuggingFaceJsonTransport(
        api_key="hf_test",
        max_new_tokens=222,
        seed=42,
        inference_client=fake_client,
    )

    payload = await transport.generate_json(
        model="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest",
        schema_name="plan_document",
        schema={"type": "object"},
        system_instructions="plan instructions",
        user_payload={"task_id": "task-1", "goal": "x"},
    )

    assert payload == {"ok": True}
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["model"] == "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest"
    assert call["max_new_tokens"] == 222
    assert call["seed"] == 42
    assert "plan_document" in call["prompt"]
    assert '"task_id":"task-1"' in call["prompt"]


@pytest.mark.asyncio
async def test_huggingface_transport_rejects_empty_text_output() -> None:
    fake_client = FakeInferenceClient(outputs=[""])
    transport = HuggingFaceJsonTransport(api_key="hf_test", inference_client=fake_client)

    with pytest.raises(RuntimeError, match="no text output"):
        await transport.generate_json(
            model="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_huggingface_transport_rejects_invalid_json() -> None:
    fake_client = FakeInferenceClient(outputs=["not-json"])
    transport = HuggingFaceJsonTransport(api_key="hf_test", inference_client=fake_client)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        await transport.generate_json(
            model="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_huggingface_transport_rejects_non_object_json() -> None:
    fake_client = FakeInferenceClient(outputs=[json.dumps(["x"])])
    transport = HuggingFaceJsonTransport(api_key="hf_test", inference_client=fake_client)

    with pytest.raises(RuntimeError, match="must be a JSON object"):
        await transport.generate_json(
            model="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


@pytest.mark.asyncio
async def test_huggingface_transport_wraps_client_exceptions() -> None:
    fake_client = FakeInferenceClient(outputs=[RuntimeError("rate limited")])
    transport = HuggingFaceJsonTransport(api_key="hf_test", inference_client=fake_client)

    with pytest.raises(RuntimeError, match="request failed"):
        await transport.generate_json(
            model="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest",
            schema_name="plan_document",
            schema={"type": "object"},
            system_instructions="plan",
            user_payload={},
        )


def test_huggingface_transport_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        HuggingFaceJsonTransport()


def test_huggingface_transport_requires_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hf_transport_module, "HFInferenceClient", None)
    with pytest.raises(RuntimeError, match="huggingface_hub package"):
        HuggingFaceJsonTransport(api_key="hf_test")
