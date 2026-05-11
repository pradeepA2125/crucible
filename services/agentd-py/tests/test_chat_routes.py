import json
import pytest
from httpx import AsyncClient, ASGITransport
from agentd.chat.app_factory import build_app

@pytest.mark.asyncio
async def test_list_threads_empty(tmp_path):
    app = build_app(workspace_path=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/chat/threads", params={"workspace": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["threads"] == []

@pytest.mark.asyncio
async def test_create_thread_and_get_it(tmp_path):
    app = build_app(workspace_path=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post(
            "/v1/chat/threads",
            json={"workspace": str(tmp_path), "title": "My chat"},
        )
        assert create_resp.status_code == 200
        thread_id = create_resp.json()["thread_id"]

        get_resp = await client.get(f"/v1/chat/threads/{thread_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["thread_id"] == thread_id

@pytest.mark.asyncio
async def test_post_message_streams_events(tmp_path):
    app = build_app(workspace_path=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        thread_id = (
            await client.post("/v1/chat/threads", json={"workspace": str(tmp_path)})
        ).json()["thread_id"]

        async with client.stream(
            "POST", f"/v1/chat/threads/{thread_id}/message",
            json={"message": "What is this project?"},
        ) as resp:
            assert resp.status_code == 200
            lines = []
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    lines.append(json.loads(line[5:].strip()))

    types = [e["type"] for e in lines]
    assert "intent_classified" in types
    assert "chat_done" in types
