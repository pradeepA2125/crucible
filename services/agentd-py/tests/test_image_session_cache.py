import json

import openai
import pytest
from test_openai_transport import (
    ANSWER,
    CONTROLLER_UNION,
    FakeResponses,
    _completed,
    _payload,
    _status_error,
    _transport,
    _usage_owner,
)


@pytest.mark.asyncio
async def test_failed_image_request_is_not_committed_to_real_owner_session():
    url = "data:image/png;base64,YQ=="
    fake = FakeResponses(
        [ANSWER, _completed()],
        _status_error(400, {"error": {"message": "bad", "code": "x"}}),
        [ANSWER, _completed()],
    )
    transport = _transport(fake, plan_route=True, max_retries=0)
    origin = {"role": "user", "content": "look", "image_refs": [{"asset_id": "a"}]}
    assets = {"a": {"data_url": url}}
    with _usage_owner("thread:image-failure"):
        for step in (1, 2, 3):
            kwargs = dict(model="gpt-4o", schema_name="c", schema=CONTROLLER_UNION,
                          system_instructions="s", user_payload=_payload([origin], step),
                          image_assets=assets)
            if step == 2:
                with pytest.raises(openai.APIStatusError):
                    await transport.generate_json(**kwargs)
            else:
                await transport.generate_json(**kwargs)
    for body in fake.calls:
        assert sum(part.get("type") == "input_image"
                   for item in body["input"] for part in item.get("content", [])
                   if isinstance(part, dict)) == 1
    final = json.dumps(fake.calls[-1]["input"])
    assert "step 1" in final and "step 3" in final and "step 2" not in final


@pytest.mark.asyncio
async def test_decode_failure_never_commits_image_session():
    from test_openai_transport import _call_done

    fake = FakeResponses(
        [ANSWER, _completed()],
        [_call_done("unknown_function", {}), _completed()],
        [ANSWER, _completed()],
    )
    transport = _transport(fake, plan_route=True, max_retries=0)
    origin = {"role": "user", "content": "look", "image_refs": [{"asset_id": "a"}]}
    with _usage_owner("thread:image-decode-failure"):
        for step in (1, 2, 3):
            kwargs = dict(
                model="gpt-4o", schema_name="c", schema=CONTROLLER_UNION,
                system_instructions="s", user_payload=_payload([origin], step),
                image_assets={"a": {"data_url": "data:image/png;base64,YQ=="}})
            if step == 2:
                with pytest.raises(RuntimeError, match="unknown function"):
                    await transport.generate_json(**kwargs)
            else:
                await transport.generate_json(**kwargs)
    assert "step 2" not in json.dumps(fake.calls[-1])


@pytest.mark.asyncio
async def test_transient_image_retry_reuses_whole_request():
    fake = FakeResponses(
        _status_error(503, {"error": {"message": "busy"}}), [ANSWER, _completed()])
    transport = _transport(fake, plan_route=True, max_retries=1)
    origin = {"role": "user", "content": "look", "image_refs": [{"asset_id": "a"}]}
    with _usage_owner("thread:image-retry"):
        await transport.generate_json(
            model="gpt-4o", schema_name="c", schema=CONTROLLER_UNION,
            system_instructions="s", user_payload=_payload([origin], 1),
            image_assets={"a": {"data_url": "data:image/png;base64,YQ=="}})
    assert len(fake.calls) == 2
    assert fake.calls[0]["input"] == fake.calls[1]["input"]
    assert json.dumps(fake.calls[1]).count("data:image/png;base64,YQ==") == 1
