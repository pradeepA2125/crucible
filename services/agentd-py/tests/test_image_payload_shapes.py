from __future__ import annotations

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentd.chat.app_factory import build_app
from agentd.providers.anthropic_transport import _anthropic_user_content
from agentd.providers.contracts import payload_without_image_attachments
from agentd.providers.gemini_transport import _gemini_contents
from agentd.providers.openai_compatible_transport import _openai_user_content

DATA = base64.b64encode(b"abc").decode()
DATA_URL = f"data:image/png;base64,{DATA}"
ATTACHMENT = {
    "name": "pixel.png",
    "media_type": "image/png",
    "data_url": DATA_URL,
    "bytes": 3,
}


def test_payload_sanitizer_removes_transport_only_image_fields() -> None:
    payload = {"task": "inspect", "image_attachments": [ATTACHMENT],
               "imageAttachments": [ATTACHMENT]}

    assert payload_without_image_attachments(payload) == {"task": "inspect"}


def test_anthropic_image_payload_uses_base64_image_block() -> None:
    content = _anthropic_user_content({"task": "inspect"}, [ATTACHMENT])

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": json.dumps({"task": "inspect"})}
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": DATA},
    }


def test_gemini_image_payload_uses_inline_data_part() -> None:
    content = _gemini_contents({"task": "inspect"}, [ATTACHMENT])

    assert content == [
        {
            "role": "user",
            "parts": [
                {"text": json.dumps({"task": "inspect"})},
                {"inline_data": {"mime_type": "image/png", "data": DATA}},
            ],
        }
    ]


def test_openai_compatible_image_payload_uses_image_url_part() -> None:
    content = _openai_user_content({"task": "inspect"}, [ATTACHMENT], True)

    assert content == [
        {"type": "text", "text": json.dumps({"task": "inspect"})},
        {"type": "image_url", "image_url": {"url": DATA_URL}},
    ]


def test_openai_compatible_nonvision_falls_back_to_text() -> None:
    content = _openai_user_content({"task": "inspect"}, [ATTACHMENT], False)

    assert content == json.dumps({"task": "inspect"})


@pytest.mark.asyncio
async def test_chat_route_rejects_malformed_images_and_keeps_valid_shape(tmp_path) -> None:
    app = build_app(workspace_path=str(tmp_path))
    agent = app.state.chat_agent
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/threads", json={"workspace": str(tmp_path)})
        thread_id = response.json()["thread_id"]

        captured: dict[str, object] = {}
        agent._active_turns = {thread_id: object()}
        agent.accepts_queued = lambda _thread_id: True
        agent.queue_message = (
            lambda _thread_id, _message, **kwargs: captured.update(kwargs) or "queued-1")

        response = await client.post(
            f"/v1/chat/threads/{thread_id}/message",
            json={
                "message": "inspect",
                "image_attachments": [
                    ATTACHMENT,
                    {**ATTACHMENT, "bytes": "not-a-number"},
                    {**ATTACHMENT, "data_url": "data:image/png;base64,not valid"},
                    {**ATTACHMENT, "media_type": "text/plain"},
                ],
            },
        )

    assert response.status_code == 400
    assert captured == {}


def test_gemini_sdk_blob_serializes_base64_exactly_once():
    from google.genai import types

    blob = types.Blob.model_validate({"mime_type": "image/png", "data": DATA})
    assert blob.data == b"abc"
    assert json.loads(blob.model_dump_json())["data"] == DATA
