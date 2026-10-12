from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.auth import AuthState, auth_middleware
from agentd.chat.controller_factory import select_chat_handler
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.providers.factory import build_transport
from agentd.providers.openai_transport import OpenAIJsonTransport, StaticBearer
from agentd.providers.rate_limit import RateLimitedTransport
from agentd.storage.in_memory import InMemoryTaskStore
from test_api_image_validation import upload
from test_openai_transport import ANSWER, FakeResponses, _completed


@pytest.mark.asyncio
@pytest.mark.parametrize("model,modalities", [
    ("gpt-6.1-sol", None), ("gpt-5.6-luna", ("text", "image")),
    ("text-only", ("text",)),
])
async def test_factory_wrapped_plan_new_chat_http_image(tmp_path, monkeypatch, model, modalities):
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "1")
    monkeypatch.setenv("CRUCIBLE_MEMORY_ENABLED", "0")
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()])
    looked_up = []

    async def plan_model(registration, selected):
        assert registration == "test-account"
        looked_up.append(selected)
        return SimpleNamespace(slug=selected, input_modalities=modalities)

    service = SimpleNamespace(bearer=lambda registration: StaticBearer("test-token"),
                              plan_model=plan_model)
    monkeypatch.setattr("agentd.chatgpt_auth.service.chatgpt_auth_service", lambda: service)
    # Replace only the external wire client, not the production wrapper or capability gate.
    def transport_with_wire(**kwargs):
        return OpenAIJsonTransport(responses_client=fake, **kwargs)
    monkeypatch.setattr("agentd.providers.openai_transport.OpenAIJsonTransport", transport_with_wire)
    transport = build_transport("chatgpt", credentials={"CRUCIBLE_CHATGPT_REGISTRATION": "test-account"})
    monkeypatch.setattr("agentd.providers.openai_transport.OpenAIJsonTransport", OpenAIJsonTransport)
    assert isinstance(transport, RateLimitedTransport)
    store = ChatThreadStore(tmp_path / "chat.db")
    agent = select_chat_handler(workspace_path=str(tmp_path), transport=transport, model=model,
                               thread_store=store, orchestrator=None, broadcaster=EventBroadcaster())
    app = FastAPI(middleware=[auth_middleware(AuthState(port=8123, token=b"secret"))])
    app.include_router(build_router(InMemoryTaskStore(), None, None, chat_agent=agent))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8123",
                               headers={"Authorization": "Bearer secret"}, timeout=10) as client:
            thread = await client.post("/v1/chat/threads", json={"workspace": str(tmp_path)})
            assert thread.status_code == 200
            tid = thread.json()["thread_id"]
            response = await client.post(f"/v1/chat/threads/{tid}/message",
                json={"content": "describe screenshot", "image_attachments": [upload()]})
            assert response.status_code == 200
            if modalities == ("text",):
                assert "cannot process images" in response.text
                assert fake.calls == []
                return
            assert "cannot process images" not in response.text
            assert len(fake.calls) == 1
            followup = await client.post(f"/v1/chat/threads/{tid}/message",
                                        json={"content": "look again"})
            assert followup.status_code == 200
            assert len(fake.calls) == 2
            for body in fake.calls:
                assert body["model"] == model
                images = [part for item in body["input"] for part in item.get("content", [])
                          if isinstance(part, dict) and part.get("type") == "input_image"]
                assert len(images) == 1
                assert images[0]["image_url"].startswith("data:image/png;base64,")
                assert images[0]["detail"] == "high"
            assert looked_up and all(selected == model for selected in looked_up)
    finally:
        await agent._context_usage.close()
        store._conn.close()
