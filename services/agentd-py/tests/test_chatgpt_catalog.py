"""Account catalog compatibility, using local fake HTTP only."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from agentd.chatgpt_auth.service import ChatGPTAuthService, ModelCatalogError
from agentd.chatgpt_auth.store import CredentialRecord, CredentialStore

REG = "reg_000000000001"


def make_service(tmp_path, handler, **kwargs):
    store = CredentialStore(tmp_path)
    store.save(CredentialRecord(
        registration_id=REG, label="Fixture", issuer="https://issuer.test",
        subject="fixture", client_id="fixture", ext_agent_host_id="fixture",
        access_token="fixture-only", scopes=["chatgpt.tokens.use.direct"],
        saved_at=1, auth_session_id="epoch-1"))
    return ChatGPTAuthService(store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata,expected", [
    ({}, None), ({"input_modalities": []}, ()),
    ({"input_modalities": ["text"]}, ("text",)),
    ({"architecture": {"input_modalities": ["text", "image"]}}, ("text", "image")),
    ({"input_modalities": [], "architecture": {"input_modalities": ["image"]}}, ()),
    ({"input_modalities": None}, ()), ({"input_modalities": "image"}, ()),
    ({"input_modalities": ["image", 1]}, ()),
    ({"output_modalities": ["image"]}, None),
])
async def test_modalities_presence(tmp_path, metadata, expected):
    service = make_service(tmp_path, lambda _: httpx.Response(200, json={"models": [
        {"slug": "gpt-future-fixture", "visibility": "list", **metadata},
        {"slug": "hidden", "visibility": "hidden", "input_modalities": ["image"]}]}))
    try:
        model = await service.plan_model(REG, "gpt-future-fixture")
        assert model is not None and model.input_modalities == expected
        assert await service.plan_model(REG, "hidden") is None
        assert await service.plan_model(REG, "gpt-future") is None
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_identity_refresh_account_and_model_switch(tmp_path: Path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"models": [
            {"slug": "one", "visibility": "list"},
            {"slug": "two", "visibility": "list", "input_modalities": ["text"]}]})
    service = make_service(tmp_path, handler)
    try:
        await service.plan_model(REG, "one")
        original = service.store.require(REG)
        service.store.save(original.model_copy(
            update={"saved_at": 2, "access_token": "rotated-fixture"}))
        await service.plan_model(REG, "one")
        assert len(calls) == 1  # token refresh preserves auth epoch
        await service.plan_model(REG, "two")
        assert len(calls) == 2
        other = "reg_000000000002"
        service.store.save(original.model_copy(update={"registration_id": other}))
        await service.plan_model(other, "one")
        assert len(calls) == 3
        service.store.save(original.model_copy(update={"auth_session_id": "epoch-2"}))
        await service.plan_model(REG, "one")
        assert len(calls) == 4
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_expiry_errors_never_return_stale_success(tmp_path):
    now = [1.0]
    status = [200]
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status[0], json={"models": [{"slug": "one", "visibility": "list"}]})
    service = make_service(tmp_path, handler, capacity_ttl_sec=10, clock=lambda: now[0])
    try:
        assert await service.plan_model(REG, "one") is not None
        now[0] = 12
        status[0] = 503
        for _ in range(2):
            with pytest.raises(ModelCatalogError):
                await service.plan_model(REG, "one")
        assert len(calls) == 3
        status[0] = 200
        assert await service.plan_model(REG, "one") is not None
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_coalescing_cancellation_and_identity_race(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"models": [{"slug": "one", "visibility": "list"}]})
    service = make_service(tmp_path, handler)
    try:
        first = asyncio.create_task(service.plan_model(REG, "one"))
        await entered.wait()
        second = asyncio.create_task(service.plan_model(REG, "one"))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        record = service.store.require(REG)
        service.store.save(record.model_copy(update={"auth_session_id": "epoch-2"}))
        release.set()
        with pytest.raises(ModelCatalogError, match="account changed"):
            await second
        assert len(calls) == 1
        assert await service.plan_model(REG, "one") is not None
        assert len(calls) == 2
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_timeout_and_cache_bound(tmp_path):
    async def slow(request):
        await asyncio.Event().wait()
    service = make_service(tmp_path, slow, capacity_timeout_sec=0.01)
    try:
        with pytest.raises(ModelCatalogError, match="timed out"):
            await service.plan_model(REG, "one")
        assert not service._plan_model_tasks
    finally:
        await service.aclose()
    service = make_service(tmp_path, lambda _: httpx.Response(200, json={"models": []}))
    try:
        for i in range(140):
            assert await service.plan_model(REG, str(i)) is None
        assert len(service._plan_models) == 128
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_catalog_reuses_401_refresh_handling(tmp_path):
    calls = []
    class FakeBearer:
        refreshed = False

        async def bearer(self):
            return "refreshed-fixture" if self.refreshed else "initial-fixture"

        async def on_unauthorized(self):
            self.refreshed = True
            return True

    def handler(request):
        calls.append(request.headers["Authorization"])
        if len(calls) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"models": [{"slug": "one", "visibility": "list"}]})
    service = make_service(tmp_path, handler)
    service._bearers[REG] = FakeBearer()
    try:
        assert await service.plan_model(REG, "one") is not None
        assert calls == ["Bearer initial-fixture", "Bearer refreshed-fixture"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_inflight_bound_and_close_cleanup(tmp_path):
    async def handler(request):
        await asyncio.Event().wait()
    service = make_service(tmp_path, handler)
    viewers = [asyncio.create_task(service.plan_model(REG, str(i))) for i in range(32)]
    await asyncio.sleep(0)
    try:
        with pytest.raises(ModelCatalogError, match="busy"):
            await service.plan_model(REG, "overflow")
        assert len(service._plan_model_tasks) == 32
    finally:
        await service.aclose()
        results = await asyncio.gather(*viewers, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert not service._plan_model_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], {}, {"models": None}, {"models": {}}])
async def test_malformed_catalog_is_failure_not_unlisted(tmp_path, body):
    service = make_service(tmp_path, lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(ModelCatalogError, match="invalid models"):
            await service.plan_model(REG, "one")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_reasoning_metadata_is_preserved_per_account_model(tmp_path):
    service = make_service(tmp_path, lambda _: httpx.Response(200, json={"models": [
        {"slug": "one", "visibility": "list", "default_reasoning_level": "low",
         "supported_reasoning_levels": [{"effort": "low", "description": "Quick"}, {"effort": "xhigh"}]},
        {"slug": "two", "visibility": "list", "supported_reasoning_levels": []}]}))
    try:
        one = await service.plan_model(REG, "one")
        two = await service.plan_model(REG, "two")
        assert one.reasoning_effort_support.control.default.value == "low"
        assert one.reasoning_effort_support.control.options[1].value.value == "xhigh"
        assert two.reasoning_effort_support.control.options == ()
    finally:
        await service.aclose()
