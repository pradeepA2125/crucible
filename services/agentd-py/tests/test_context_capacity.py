"""Authoritative context capacity and private effective selection identity."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from agentd.chatgpt_auth.service import ChatGPTAuthService
from agentd.chatgpt_auth.store import CredentialRecord, CredentialStore, summarize
from agentd.providers.openai_transport import OpenAIJsonTransport
from agentd.providers.runtime import ProviderRuntime
from agentd.providers.validate import ProviderValidationError


def record(reg: str = "reg_000000000001", **updates: object) -> CredentialRecord:
    values = dict(registration_id=reg, label="Account", issuer="https://issuer.test",
                  subject="subject", client_id="client", ext_agent_host_id="host",
                  access_token="secret", scopes=["chatgpt.tokens.use.direct"], saved_at=1.0)
    values.update(updates)
    return CredentialRecord.model_validate(values)


def test_legacy_session_identity_is_stable_and_not_public(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)
    old = record()
    assert old.auth_session_id is None
    store.save(old)
    assert store.require(old.registration_id).auth_session_id is None
    assert "auth_session_id" not in summarize(old).model_dump()


def test_store_subscription_is_account_scoped_and_ignores_refresh(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)
    current = record(auth_session_id="session-1")
    store.save(current)
    seen = []
    unsubscribe = store.subscribe(current.registration_id, lambda: seen.append("changed"))
    store.save(record("reg_000000000002", auth_session_id="other"))
    store.save(current.model_copy(update={"saved_at": 2, "access_token": "rotated"}))
    assert seen == []
    store.save(current.model_copy(update={"auth_session_id": "session-2"}))
    assert seen == ["changed"]
    unsubscribe()
    store.save(current)
    assert seen == ["changed"]


def catalog_service(tmp_path, handler, **kwargs):
    store = CredentialStore(tmp_path)
    store.save(record(auth_session_id="session-1"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ChatGPTAuthService(store=store, http=http, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("value, expected", [(272000, 272000), (None, None),
    (True, None), (0, None), (-1, None), ("128000", None), (12.5, None)])
async def test_catalog_capacity_accepts_only_positive_integer(tmp_path, value, expected):
    def handler(request):
        return httpx.Response(200, json={"models": [
            {"slug": "model", "visibility": "list", "context_window": value}]})
    service = catalog_service(tmp_path, handler)
    assert await service.context_capacity("reg_000000000001", "model") == expected
    assert await service.context_capacity("reg_000000000001", "absent") is None
    await service.aclose()


@pytest.mark.asyncio
async def test_catalog_capacity_coalesces_and_expires_unknowns(tmp_path):
    calls = []
    now = [1.0]
    release = asyncio.Event()
    async def handler(request):
        calls.append(request.url.path)
        await release.wait()
        return httpx.Response(503)
    service = catalog_service(tmp_path, handler, capacity_ttl_sec=10, clock=lambda: now[0])
    first = asyncio.create_task(service.context_capacity("reg_000000000001", "model"))
    second = asyncio.create_task(service.context_capacity("reg_000000000001", "model"))
    await asyncio.sleep(0.02)
    assert calls == ["/v1/models"]
    release.set()
    assert await first is None and await second is None
    assert await service.context_capacity("reg_000000000001", "model") is None
    assert len(calls) == 1
    now[0] += 11
    assert await service.context_capacity("reg_000000000001", "model") is None
    assert len(calls) == 2
    await service.aclose()


@pytest.mark.asyncio
async def test_runtime_capacity_sources_and_private_active_auth_identity(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [
            {"slug": "model", "visibility": "list", "context_window": 272000}]})
    service = catalog_service(tmp_path, handler)
    reg = "reg_000000000001"
    transport = OpenAIJsonTransport(bearer=service.bearer(reg), plan_route=True)
    runtime = ProviderRuntime(backend="chatgpt", model="model", engines=[],
                              context_window=128000, transport=transport, auth_service=service)
    identity = runtime.context_identity()
    assert identity[:2] == ("chatgpt", "model")
    assert calls == []  # construction/identity must never fetch a catalog
    assert await runtime.context_capacity() == (272000, "catalog")
    service.store.save(service.store.require(reg).model_copy(update={"saved_at": 3,
                                                                      "access_token": "rotated"}))
    assert runtime.context_identity() == identity
    service.store.save(service.store.require(reg).model_copy(update={"auth_session_id": "next"}))
    assert runtime.context_identity() != identity
    runtime.model = "absent"
    assert await runtime.context_capacity() == (None, "unknown")
    runtime.backend = "openai"
    assert await runtime.context_capacity() == (128000, "configured")
    await service.aclose()


@pytest.mark.asyncio
async def test_runtime_notifies_only_effective_successful_changes(tmp_path, monkeypatch):
    service = catalog_service(tmp_path, lambda request: httpx.Response(503))
    reg = "reg_000000000001"
    def transport(account=reg):
        return OpenAIJsonTransport(bearer=service.bearer(account), plan_route=True)
    runtime = ProviderRuntime(backend="chatgpt", model="model", engines=[],
        context_window=128000, transport=transport(), auth_service=service)
    seen = []
    stop = runtime.subscribe_context(lambda: seen.append("changed"))
    async def ping(candidate, model):
        if model == "bad":
            raise ProviderValidationError("bad")
    monkeypatch.setattr("agentd.providers.runtime.ping_transport", ping)
    monkeypatch.setattr("agentd.providers.runtime.build_transport",
                        lambda backend, credentials: transport())
    identity = runtime.context_identity()
    with pytest.raises(ProviderValidationError):
        await runtime.swap(backend="chatgpt", model="bad", context_window=50)
    assert runtime.context_identity() == identity and seen == []
    await runtime.swap(backend="chatgpt", model="model")
    assert seen == []  # new transport instance is not a new effective selection
    await runtime.apply_reasoning_effort(None)
    assert runtime.context_identity() == identity and seen == []
    await runtime.swap(backend="chatgpt", model="other")
    assert seen == ["changed"]
    await runtime.swap(backend="chatgpt", model="other", context_window=64000)
    assert seen == ["changed", "changed"]
    other = record("reg_000000000002", auth_session_id="other")
    service.store.save(other)
    await asyncio.sleep(0)
    assert len(seen) == 2
    await asyncio.to_thread(service.store.save,
        service.store.require(reg).model_copy(update={"auth_session_id": "next"}))
    await asyncio.sleep(0)
    assert len(seen) == 3
    monkeypatch.setattr("agentd.providers.runtime.build_transport",
                        lambda backend, credentials: transport(other.registration_id))
    await runtime.swap(backend="chatgpt", model="other")
    assert len(seen) == 4
    service.store.save(service.store.require(reg).model_copy(update={"auth_session_id": "old"}))
    await asyncio.sleep(0)
    assert len(seen) == 4  # former active account must be detached
    stop()
    service.store.save(other.model_copy(update={"auth_session_id": "next"}))
    await asyncio.sleep(0)
    assert len(seen) == 4
    await service.aclose()


@pytest.mark.asyncio
async def test_catalog_positive_cache_is_ttl_model_and_account_scoped(tmp_path):
    calls = []
    now = [1.0]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [
            {"slug": "model", "visibility": "list", "context_window": 1000},
            {"slug": "other", "visibility": "list", "context_window": 2000}]})
    service = catalog_service(tmp_path, handler, capacity_ttl_sec=10, clock=lambda: now[0])
    reg = "reg_000000000001"
    other = record("reg_000000000002", auth_session_id="other")
    service.store.save(other)
    assert await service.context_capacity(reg, "model") == 1000
    assert await service.context_capacity(reg, "model") == 1000
    assert len(calls) == 1
    assert await service.context_capacity(reg, "other") == 2000
    assert await service.context_capacity(other.registration_id, "model") == 1000
    assert len(calls) == 3
    now[0] += 11
    assert await service.context_capacity(reg, "model") == 1000
    assert len(calls) == 4
    await service.aclose()


@pytest.mark.asyncio
async def test_catalog_canceled_viewer_does_not_cancel_shared_fetch(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        started.set()
        await release.wait()
        return httpx.Response(200, json={"models": [
            {"slug": "model", "visibility": "list", "context_window": 1000}]})
    service = catalog_service(tmp_path, handler)
    first = asyncio.create_task(service.context_capacity("reg_000000000001", "model"))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(service.context_capacity("reg_000000000001", "model"))
    release.set()
    assert await second == 1000
    assert len(calls) == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_catalog_timeout_is_unknown_and_service_close_cancels_owned_fetch(tmp_path):
    started = asyncio.Event()
    async def handler(request):
        started.set()
        await asyncio.Event().wait()
    service = catalog_service(tmp_path, handler, capacity_timeout_sec=0.02)
    assert await service.context_capacity("reg_000000000001", "model") is None
    assert not service._capacity_tasks
    pending = asyncio.create_task(service.context_capacity("reg_000000000001", "other"))
    started.clear()
    await started.wait()
    await service.aclose()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not service._capacity_tasks


@pytest.mark.asyncio
async def test_runtime_unconfigured_chatgpt_never_uses_configured_capacity():
    runtime = ProviderRuntime(backend="chatgpt", model="model", engines=[],
                              context_window=128000, config_error="not signed in")
    assert await runtime.context_capacity() == (None, "unknown")


@pytest.mark.asyncio
async def test_runtime_model_change_discards_inflight_capacity(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"models": [
            {"slug": "model", "visibility": "list", "context_window": 1000}]})
    service = catalog_service(tmp_path, handler)
    runtime = ProviderRuntime(backend="chatgpt", model="model", engines=[],
        transport=OpenAIJsonTransport(bearer=service.bearer("reg_000000000001"), plan_route=True),
        auth_service=service)
    pending = asyncio.create_task(runtime.context_capacity())
    await started.wait()
    runtime.model = "other"
    release.set()
    assert await pending == (None, "unknown")
    await service.aclose()


@pytest.mark.asyncio
async def test_catalog_capacity_external_epoch_change_discards_inflight(tmp_path):
    calls = []
    release = asyncio.Event()
    async def handler(request):
        calls.append(request.url.path)
        await release.wait()
        return httpx.Response(200, json={"models": [
            {"slug": "model", "visibility": "list", "context_window": 272000}]})
    service = catalog_service(tmp_path, handler)
    reg = "reg_000000000001"
    pending = asyncio.create_task(service.context_capacity(reg, "model"))
    await asyncio.sleep(0.02)
    external = CredentialStore(tmp_path)
    external.save(external.require(reg).model_copy(update={"auth_session_id": "session-2"}))
    release.set()
    assert await pending is None
    assert await service.context_capacity(reg, "model") == 272000
    assert len(calls) == 2
    # Ordinary refresh neither invalidates the capacity nor changes its identity.
    service.store.save(service.store.require(reg).model_copy(update={"saved_at": 4,
                                                                  "access_token": "rotated"}))
    assert await service.context_capacity(reg, "model") == 272000
    assert len(calls) == 2
    await service.aclose()
