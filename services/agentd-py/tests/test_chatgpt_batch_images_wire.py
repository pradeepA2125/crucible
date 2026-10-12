import json

import pytest
from test_chatgpt_resized_wire import account_model
from test_openai_transport import ANSWER, FakeResponses, _completed, _payload, _usage_owner

from agentd.chat.controller_prompts import controller_response_schema
from agentd.providers.openai_transport import OpenAIJsonTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("second_error", [False, True])
async def test_native_batch_images_keep_distinct_successful_origins(monkeypatch, second_error):
    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")
    schema = controller_response_schema(phase="ACTIVE", anyof=True)
    batch = {"type": "tool_calls", "thought": "read", "provider_call_id": "provider-batch",
             "calls": [{"id": "one", "tool": "read_image", "args": {"path": "one.png"}},
                       {"id": "two", "tool": "read_image", "args": {"path": "two.png"}}]}
    results = [{"role": "tool_result", "tool": "read_image", "batch_id": "b",
                "call_id": "b_one", "id": "one", "content": "first",
                "image_refs": [{"asset_id": "a"}]},
               {"role": "tool_result", "tool": "read_image", "batch_id": "b",
                "call_id": "b_two", "id": "two", "content": "error" if second_error else "second",
                "is_error": second_error,
                "image_refs": [] if second_error else [{"asset_id": "b"}]}]
    history = [{"role": "user", "content": "inspect"},
               {"role": "assistant", "content": json.dumps(batch)}, *results]
    assets = {"a": {"data_url": "data:image/png;base64,YQ=="}}
    if not second_error:
        assets["b"] = {"data_url": "data:image/png;base64,Yg=="}
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()])
    transport = OpenAIJsonTransport(responses_client=fake, plan_route=True,
                                    plan_model_lookup=account_model)
    with _usage_owner("thread:batch-image-wire"):
        for step in range(2):
            await transport.generate_json(model="gpt-6.1-sol", schema_name="controller",
                schema=schema, system_instructions="s",
                user_payload=_payload(history.copy(), step), image_assets=assets)
            history.append({"role": "user", "content": "followup"})
    for body in fake.calls:
        images = [part for item in body["input"] for part in item.get("content", [])
                  if isinstance(part, dict) and part.get("type") == "input_image"]
        assert {p["image_url"] for p in images} == {a["data_url"] for a in assets.values()}
        assert len(images) == len(assets)
        outputs = [i for i in body["input"] if i.get("type") == "function_call_output"]
        assert outputs[0]["call_id"] == "provider-batch"
        assert "image_refs" not in outputs[0]["output"]
