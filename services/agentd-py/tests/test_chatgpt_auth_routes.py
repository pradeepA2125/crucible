"""Sign in with ChatGPT routes, end to end against the fake authorization server."""
from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from agentd.api.chatgpt_auth_routes import build_chatgpt_auth_router
from agentd.chatgpt_auth.service import ChatGPTAuthService
from agentd.chatgpt_auth.store import CredentialStore
from tests._chatgpt_fake import ISSUER, FakeAuthServer


@pytest_asyncio.fixture
async def setup(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, FakeAuthServer,
                                                       ChatGPTAuthService]]:
    fake = FakeAuthServer()
    service = ChatGPTAuthService(store=CredentialStore(tmp_path / "auth"), http=fake.client(),
                                 issuer=ISSUER)
    service.attempts._preferred_port = 0  # noqa: SLF001 — never fight a real 1455 listener
    app = FastAPI()
    app.include_router(build_chatgpt_auth_router(lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        yield client, fake, service
    await service.aclose()


async def _sign_in(client: httpx.AsyncClient, fake: FakeAuthServer,
                   registration_id: str | None = None) -> dict[str, object]:
    started = (await client.post("/v1/auth/chatgpt/attempts",
                                 json={"registration_id": registration_id})).json()
    url = str(started["authorize_url"])
    redirect = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["redirect_uri"]
    async with httpx.AsyncClient() as browser:
        await browser.get(redirect, params=fake.authorize(url))
    for _ in range(50):
        status = (await client.get(f"/v1/auth/chatgpt/attempts/{started['attempt_id']}")).json()
        if status["state"] != "pending":
            return status
        await asyncio.sleep(0.01)
    raise AssertionError("sign-in never finished")


@pytest.mark.asyncio
async def test_sign_in_list_models_and_sign_out(setup) -> None:  # noqa: ANN001
    client, fake, _ = setup
    done = await _sign_in(client, fake)
    assert done["state"] == "succeeded" and done["plan_enabled"] is True
    assert done["first_plan_sign_in"] is True
    reg = done["registration_id"]

    listing = (await client.get("/v1/auth/chatgpt/registrations")).json()["registrations"]
    assert listing == [{"registration_id": reg, "label": "a@example.com",
                        "email": "a@example.com", "name": None, "plan_enabled": True,
                        "signed_in": True}]

    models = (await client.post(f"/v1/auth/chatgpt/registrations/{reg}/models")).json()
    assert models == {"models": [
        {"slug": "gpt-plan-large", "display_name": "GPT Plan Large"},
        {"slug": "gpt-plan-small", "display_name": "GPT Plan Small"}]}

    out = (await client.post(f"/v1/auth/chatgpt/registrations/{reg}/sign-out")).json()
    assert out == {"remote_revoked": True}
    listing = (await client.get("/v1/auth/chatgpt/registrations")).json()["registrations"]
    assert listing[0]["signed_in"] is False

    gone = await client.post(f"/v1/auth/chatgpt/registrations/{reg}/models")
    assert gone.status_code == 409 and gone.json()["detail"]["kind"] == "session_invalid"

    again = await _sign_in(client, fake, registration_id=str(reg))
    assert again["state"] == "succeeded" and again["first_plan_sign_in"] is False


@pytest.mark.asyncio
async def test_one_attempt_at_a_time_and_unknown_ids(setup) -> None:  # noqa: ANN001
    client, _, _ = setup
    first = await client.post("/v1/auth/chatgpt/attempts", json={})
    assert first.status_code == 200
    assert (await client.post("/v1/auth/chatgpt/attempts", json={})).status_code == 409
    cancelled = await client.post(f"/v1/auth/chatgpt/attempts/{first.json()['attempt_id']}/cancel")
    assert cancelled.json()["state"] == "failed" and cancelled.json()["reason"] == "cancelled"
    assert (await client.get("/v1/auth/chatgpt/attempts/att_nope")).status_code == 404
    assert (await client.post("/v1/auth/chatgpt/attempts",
                              json={"registration_id": "reg_000000000000"})).status_code == 404
    assert (await client.post("/v1/auth/chatgpt/registrations/../x/models")).status_code == 404
