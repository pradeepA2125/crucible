from types import SimpleNamespace

import pytest

from agentd.providers.image_capabilities import resolve_image_capability
from agentd.providers.image_parts import history_messages
from agentd.providers.openai_transport import OpenAIJsonTransport
from agentd.providers.openrouter_transport import OpenRouterJsonTransport


@pytest.mark.asyncio
async def test_image_capability_fails_closed_for_unknown_model_and_deferred_adapter():
    transport = OpenAIJsonTransport(api_key="unused")
    assert (await resolve_image_capability(transport, "gpt-4o")).supported
    assert not (await resolve_image_capability(transport, "unknown")).supported
    unknown = SimpleNamespace(supports_vision=True)
    assert not (await resolve_image_capability(unknown, "gpt-4o")).supported


@pytest.mark.asyncio
async def test_openrouter_requires_official_endpoint_and_advertised_image_modality():
    class Registry:
        async def get(self, model):
            modalities = ["text", "image"] if model == "vision" else ["text"]
            return {"architecture": {"input_modalities": modalities}}
    transport = OpenRouterJsonTransport(completions_client=object(), model_capabilities=Registry())
    assert (await resolve_image_capability(transport, "vision")).supported
    assert not (await resolve_image_capability(transport, "text")).supported
    transport._image_endpoint = "https://custom.invalid/v1"
    assert not (await resolve_image_capability(transport, "vision")).supported


@pytest.mark.asyncio
async def test_anthropic_custom_endpoint_and_unknown_model_fail_closed():
    from agentd.providers.anthropic_transport import AnthropicJsonTransport

    official = AnthropicJsonTransport(messages_client=object())
    assert (await resolve_image_capability(official, "claude-opus-5-5")).supported
    custom = AnthropicJsonTransport(
        messages_client=object(), endpoint="https://proxy.invalid/v1/messages")
    cap = await resolve_image_capability(custom, "claude-opus-5-5")
    assert not cap.supported and "endpoint" in cap.reason
    assert not (await resolve_image_capability(official, "claude-opus-5-5-custom")).supported


@pytest.mark.asyncio
async def test_openrouter_malformed_registry_fails_closed():
    class Registry:
        async def get(self, model):
            return {"architecture": None}

    transport = OpenRouterJsonTransport(
        completions_client=object(), model_capabilities=Registry())
    assert not (await resolve_image_capability(transport, "vision")).supported


@pytest.mark.asyncio
async def test_unknown_image_model_rejected_before_wire():
    transport = OpenAIJsonTransport(responses_client=object())
    with pytest.raises(ValueError, match="not verified"):
        await transport.generate_json(
            model="gpt-4o-custom", schema_name="s", schema={}, system_instructions="",
            user_payload={}, image_assets={"a": {"data_url": "data:image/png;base64,YQ=="}})


@pytest.mark.asyncio
async def test_gemini_effective_custom_endpoint_fails_closed():
    from agentd.providers.gemini_transport import GeminiJsonTransport

    transport = GeminiJsonTransport(models_client=object())
    transport._client = SimpleNamespace(_api_client=SimpleNamespace(
        _http_options=SimpleNamespace(base_url="https://custom.invalid/")))
    assert not (await resolve_image_capability(transport, "gemini-2.5-flash")).supported
    transport._client._api_client._http_options.base_url = (
        "https://generativelanguage.googleapis.com/")
    assert (await resolve_image_capability(transport, "gemini-2.5-flash")).supported


def test_debug_native_image_parts_are_redacted_without_mutating_request():
    from agentd.providers.image_parts import redact_image_parts

    body = {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,SECRET"},
        {"type": "input_text", "text": "look"}]}]}
    safe = redact_image_parts(body)
    assert "SECRET" not in str(safe)
    assert "SECRET" in str(body)
    assert "look" in str(safe)


@pytest.mark.parametrize("serializer,kind", [
    ("anthropic", "image"), ("gemini", "inline_data"), ("chat", "image_url")])
def test_native_parts_stay_at_owned_user_and_tool_origins(serializer, kind):
    url = "data:image/png;base64,YQ=="
    assets = {"a": {"data_url": url}}
    payload = {"workspace": "/ws", "conversation_history": [
        {"role": "user", "content": "first", "image_refs": [{"asset_id": "a"}]},
        {"role": "tool_result", "tool": "read_image", "content": "second",
         "image_refs": [{"asset_id": "a"}]},
        {"role": "user", "content": "follow up"}], "instruction": "next"}
    messages = history_messages(payload, assets, serializer)
    image_entries = []
    for entry in messages:
        parts = entry.get("parts", entry.get("content", []))
        if isinstance(parts, list) and any(p.get("type") == kind or kind in p for p in parts):
            image_entries.append(entry)
    assert len(image_entries) == 2
    assert "first" in str(image_entries[0]) and "second" in str(image_entries[1])
    for entry in messages:
        parts = entry.get("parts", entry.get("content", []))
        if isinstance(parts, list):
            for part in parts:
                if "text" in part:
                    assert url not in part["text"] and "image_refs" not in part["text"]
