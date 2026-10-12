from types import SimpleNamespace

import pytest
from test_openai_transport import (
    ANSWER,
    CONTROLLER_UNION,
    FakeResponses,
    _completed,
    _payload,
    _usage_owner,
)

from agentd.providers.image_capabilities import resolve_image_capability
from agentd.providers.openai_transport import OpenAIJsonTransport


@pytest.mark.asyncio
async def test_plan_catalog_precedence_and_model_switches():
    entries = {
        "gpt-6.1-sol": None, "gpt-5.6-luna": ("text", "image"),
        "future-account-slug": None, "text-only": ("text",), "empty": (),
    }

    async def lookup(model):
        return SimpleNamespace(input_modalities=entries[model]) if model in entries else None

    transport = OpenAIJsonTransport(responses_client=object(), plan_route=True,
                                    plan_model_lookup=lookup)
    for model in ("gpt-6.1-sol", "gpt-5.6-luna", "future-account-slug"):
        assert (await resolve_image_capability(transport, model)).supported
    assert not (await resolve_image_capability(transport, "gpt-6.1-sol")).model_modality
    for model in ("text-only", "empty", "unlisted"):
        assert not (await resolve_image_capability(transport, model)).supported
    entries["gpt-6.1-sol"] = ("text",)
    assert not (await resolve_image_capability(transport, "gpt-6.1-sol")).supported
    assert (await resolve_image_capability(transport, "gpt-5.6-luna")).supported


@pytest.mark.asyncio
async def test_plan_unavailable_catalog_does_not_use_api_allowlist():
    async def failed(model):
        raise RuntimeError("private credential detail")
    transport = OpenAIJsonTransport(responses_client=object(), plan_route=True,
                                    plan_model_lookup=failed)
    capability = await resolve_image_capability(transport, "gpt-4o")
    assert not capability.supported and "catalog" in capability.reason
    assert "private" not in capability.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-5.6-luna", "new-account-model"])
async def test_account_model_whole_requests_keep_owned_high_detail_image(model):
    async def lookup(selected):
        return SimpleNamespace(slug=selected, input_modalities=None)
    fake = FakeResponses(*([[ANSWER, _completed()]] * 4))
    transport = OpenAIJsonTransport(responses_client=fake, plan_route=True,
                                    plan_model_lookup=lookup)
    origin = {"role": "user", "content": "image", "image_refs": [{"asset_id": "a"}]}
    history = [origin]
    url = "data:image/png;base64,YQ=="
    with _usage_owner("thread:catalog-wire"):
        for step in range(3):
            await transport.generate_json(model=model, schema_name="c", schema=CONTROLLER_UNION,
                system_instructions="s", user_payload=_payload(history.copy(), step),
                image_assets={"a": {"data_url": url}})
            history.append({"role": "user", "content": f"followup {step}"})
        await transport.generate_json(model=model, schema_name="c", schema=CONTROLLER_UNION,
            system_instructions="s",
            user_payload=_payload([{"role": "user", "content": "image dropped"}], 4),
            image_assets={})
    for index, body in enumerate(fake.calls):
        images = [p for item in body["input"] for p in item.get("content", [])
                  if isinstance(p, dict) and p.get("type") == "input_image"]
        assert len(images) == (1 if index < 3 else 0)
        assert all(p["detail"] == "high" for p in images)
