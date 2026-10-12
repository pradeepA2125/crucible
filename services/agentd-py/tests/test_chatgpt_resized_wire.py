import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from test_openai_transport import (
    ANSWER,
    CONTROLLER_UNION,
    FakeResponses,
    _completed,
    _payload,
    _status_error,
    _usage_owner,
)

from agentd.images import ImageAssetStore
from agentd.providers.openai_transport import OpenAIJsonTransport


async def account_model(model):
    return SimpleNamespace(slug=model, input_modalities=None)


@pytest.mark.asyncio
async def test_resized_images_keep_single_parts_across_growth_switch_and_drop(tmp_path):
    raw = io.BytesIO()
    Image.new("RGB", (3024, 1964), "white").save(raw, format="PNG")
    store = ImageAssetStore(tmp_path)
    ref = store.import_bytes(raw.getvalue(), "retina.png", "image/png")
    asset = store.resolve(ref)
    fake = FakeResponses(*([[ANSWER, _completed()]] * 5))
    transport = OpenAIJsonTransport(responses_client=fake, plan_route=True,
                                    plan_model_lookup=account_model)
    history = [{"role": "user", "content": "read", "image_refs": [ref]}]
    with _usage_owner("thread:resized-wire"):
        for step, model in enumerate(("gpt-6.1-sol", "gpt-6.1-sol", "gpt-5.6-luna")):
            await transport.generate_json(model=model, schema_name="c", schema=CONTROLLER_UNION,
                system_instructions="s", user_payload=_payload(history.copy(), step),
                image_assets={ref["asset_id"]: asset})
            history.append({"role": "user", "content": f"step {step}"})
        for projected in ([{"role": "user", "content": "image unavailable: budget"}],
                          [{"role": "user", "content": "[MEMORY] compacted"}]):
            await transport.generate_json(
                model="gpt-5.6-luna", schema_name="c", schema=CONTROLLER_UNION,
                system_instructions="s", user_payload=_payload(projected, 4), image_assets={})
    for index, body in enumerate(fake.calls):
        parts = [p for item in body["input"] for p in item.get("content", [])
                 if isinstance(p, dict) and p.get("type") == "input_image"]
        assert len(parts) == (1 if index < 3 else 0)
        for part in parts:
            assert part["detail"] == "high"
            raster = Image.open(io.BytesIO(base64.b64decode(part["image_url"].split(",")[1])))
            assert raster.size == (asset["width"], asset["height"])
            assert max(raster.size) <= 2048
            assert raster.width * raster.height <= 4_000_000
        if index >= 3:
            assert asset["data_url"] not in json.dumps(body)


@pytest.mark.asyncio
async def test_account_server_image_rejection_is_not_retried_without_images():
    fake = FakeResponses(_status_error(400, {"error": {
        "message": "This account model does not accept image input; choose another account model.",
        "code": "invalid_image_input"}}))
    transport = OpenAIJsonTransport(responses_client=fake, plan_route=True,
                                    plan_model_lookup=account_model, max_retries=0)
    with _usage_owner("thread:image-refused"):
        from agentd.providers.plan_access import PlanUnsupportedCapability
        with pytest.raises(PlanUnsupportedCapability, match="does not accept image input"):
            await transport.generate_json(
                model="gpt-6.1-sol", schema_name="c", schema=CONTROLLER_UNION,
                system_instructions="s", user_payload=_payload([{
                    "role": "user", "content": "read", "image_refs": [{"asset_id": "a"}]}], 1),
                image_assets={"a": {"data_url": "data:image/png;base64,YQ=="}})
    assert len(fake.calls) == 1
    assert "input_image" in json.dumps(fake.calls[0])
    assert not transport._sessions._state
