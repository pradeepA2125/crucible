import base64
import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from agentd.api.routes import build_router
from agentd.auth import AuthState, auth_middleware
from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.images import ImageAssetStore
from agentd.storage.in_memory import InMemoryTaskStore


def upload():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    raw = stream.getvalue()
    return {"name": "x.png", "media_type": "image/png", "bytes": len(raw),
            "data_url": "data:image/png;base64," + base64.b64encode(raw).decode()}


def app_for(tmp_path):
    store = ChatThreadStore(tmp_path / "chat.db")
    agent = SimpleNamespace(_store=store)
    app = FastAPI(middleware=[auth_middleware(AuthState(port=8123, token=b"secret"))])
    app.include_router(build_router(InMemoryTaskStore(), None, None, chat_agent=agent))
    return app, store


@pytest.mark.asyncio
@pytest.mark.parametrize("images,status", [([{}], 400), ([{}] * 9, 413), ("bad", 400)])
async def test_validation_atomic(tmp_path, images, status):
    app, store = app_for(tmp_path)
    thread = store.create_thread(str(tmp_path), "t")
    if isinstance(images, list):
        images = [upload(), *images] if len(images) < 9 else images
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8123",
                           headers={"Authorization": "Bearer secret"}) as client:
        response = await client.post(f"/v1/chat/threads/{thread.thread_id}/message",
                                     json={"content": "x", "image_attachments": images})
    assert response.status_code == status
    assert response.json()["detail"]
    assert store.get_thread(thread.thread_id).messages == []


@pytest.mark.asyncio
async def test_preview_auth_reference_workspace_and_deletion(tmp_path):
    app, store = app_for(tmp_path)
    ref, = ImageAssetStore(tmp_path).import_uploads([upload()])
    thread = store.create_thread(str(tmp_path), "t")
    other = store.create_thread(str(tmp_path), "other")
    store.append_message(thread.thread_id, ChatMessage(role="user", content="",
                         metadata={"image_attachments": [ref]}))
    path = f"/v1/chat/threads/{thread.thread_id}/images/{ref['asset_id']}"
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://127.0.0.1:8123") as client:
        assert (await client.get(path)).status_code == 401
        client.headers["Authorization"] = "Bearer secret"
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == ImageAssetStore(tmp_path).read_asset(ref["asset_id"])
        other_response = await client.get(path.replace(thread.thread_id, other.thread_id))
        assert other_response.status_code == 404
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        wrong = store.create_thread(str(isolated), "wrong")
        store.append_message(wrong.thread_id, ChatMessage(role="user", content="",
                             metadata={"image_attachments": [ref]}))
        wrong_response = await client.get(path.replace(thread.thread_id, wrong.thread_id))
        assert wrong_response.status_code == 404
        store._conn.execute("DELETE FROM chat_threads WHERE thread_id = ?", (thread.thread_id,))
        store._conn.commit()
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_legacy_route_persists_refs_and_explicitly_declines_images(tmp_path):
    from agentd.chat.agent import ChatAgent
    from agentd.orchestrator.broadcaster import EventBroadcaster

    store = ChatThreadStore(tmp_path / "legacy.db")
    agent = ChatAgent(workspace_path=str(tmp_path), transport=object(), model="unknown",
                      thread_store=store, orchestrator=None, broadcaster=EventBroadcaster())
    app = FastAPI()
    app.include_router(build_router(InMemoryTaskStore(), None, None, chat_agent=agent))
    thread = store.create_thread(str(tmp_path), "legacy")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/chat/threads/{thread.thread_id}/message",
                                     json={"content": "describe", "image_attachments": [upload()]})
        assert response.status_code == 200
        assert "legacy chat" in response.text
        durable = store.get_thread(thread.thread_id)
        ref, = durable.messages[0].metadata["image_attachments"]
        assert "data_url" not in ref
        assert "legacy chat" in durable.messages[-1].content
        store.set_controller_history(thread.thread_id, [
            {"role": "user", "content": "origin", "image_refs": [ref]}])
        store._conn.execute("UPDATE chat_threads SET messages_json = '[]' WHERE thread_id = ?",
                            (thread.thread_id,))
        store._conn.commit()
        preview = await client.get(
            f"/v1/chat/threads/{thread.thread_id}/images/{ref['asset_id']}")
        assert preview.status_code == 200
