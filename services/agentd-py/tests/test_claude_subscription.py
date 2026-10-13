from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from agentd.api.claude_auth_routes import build_claude_auth_router
from agentd.chatgpt_auth.files import read_private_json, write_private_json
from agentd.claude_auth.service import ClaudeAuthService, ClaudeSessionInvalid
from agentd.providers.claude_subscription import (
    IDENTITY,
    ClaudeAccessStopped,
    ClaudeSubscriptionTransport,
)
from agentd.providers.plan_access import access_payload
from agentd.providers.usage import METER, USAGE_OWNER

REG = "claude_" + "a" * 24
TOKENS = {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600}
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def seed(service, *, expired=False):
    write_private_json(
        service.path(REG),
        {
            **TOKENS,
            "expires_at": time.time() + (0 if expired else 3600),
            "label": "test",
            "registration_id": REG,
        },
    )


@pytest.mark.asyncio
async def test_browser_pkce_and_public_routes(tmp_path):
    requests = []

    def token(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=TOKENS)

    service = ClaudeAuthService(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(token)), callback_port=0
    )
    app = FastAPI()
    app.include_router(build_claude_auth_router(lambda: service))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            started = (await client.post("/v1/auth/claude/attempts")).json()
            params = parse_qs(urlsplit(started["authorize_url"]).query)
            assert (await client.post("/v1/auth/claude/attempts")).status_code == 409
            async with httpx.AsyncClient() as browser:
                assert (
                    await browser.get(
                        params["redirect_uri"][0], params={"state": "wrong", "code": "test"}
                    )
                ).status_code == 400
                assert not requests
                done = await browser.get(
                    params["redirect_uri"][0], params={"state": params["state"][0], "code": "test"}
                )
                assert done.status_code == 200
            verifier = requests[0]["code_verifier"]
            assert params["code_challenge"][0] == base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).decode().rstrip("=")
            assert requests[0]["state"] == params["state"][0]
            status = (await client.get("/v1/auth/claude/attempts/" + started["attempt_id"])).json()
            assert status["state"] == "succeeded"
            accounts = (await client.get("/v1/auth/claude/registrations")).json()
            assert accounts["registrations"][0]["signed_in"]
            assert "private-" not in json.dumps(accounts) + json.dumps(status)
            record_path = service.path(status["registration_id"])
            assert record_path.stat().st_mode & 0o777 == 0o600
            assert tmp_path.stat().st_mode & 0o777 == 0o700
            assert (
                await client.post(
                    "/v1/auth/claude/registrations/" + status["registration_id"] + "/sign-out"
                )
            ).json() == {"remote_revoked": False}
            assert not read_private_json(record_path)["access_token"]
            assert (await client.get("/v1/auth/claude/attempts/unknown")).status_code == 404
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancel_and_timeout_close_listener(tmp_path):
    service = ClaudeAuthService(tmp_path, callback_port=0, lifetime=0.03)
    try:
        started, url = await service.start()
        await service.cancel(started["attempt_id"])
        assert service.status(started["attempt_id"])["reason"] == "cancelled"
        assert not service.attempts[started["attempt_id"]].server.is_serving()
        started, _ = await service.start()
        await asyncio.sleep(0.06)
        assert service.status(started["attempt_id"])["reason"] == "timeout"
        assert not service.accounts()
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_refresh_shared_by_services_and_signout_waits_for_rotation(tmp_path):
    count = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def token(request):
        nonlocal count
        count += 1
        assert json.loads(request.content)["refresh_token"] == "private-refresh"
        entered.set()
        await release.wait()
        return httpx.Response(
            200, json={**TOKENS, "access_token": "rotated", "refresh_token": "rotated-refresh"}
        )

    services = [
        ClaudeAuthService(tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(token)))
        for _ in range(2)
    ]
    seed(services[0], expired=True)
    try:
        first = asyncio.create_task(services[0].bearer(REG))
        await entered.wait()
        second = asyncio.create_task(services[1].bearer(REG))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == "rotated"
        assert count == 1
        assert services[0].read(REG)["refresh_token"] == "rotated-refresh"
        await services[1].sign_out(REG)
        with pytest.raises(ClaudeSessionInvalid):
            await services[0].bearer(REG)
    finally:
        release.set()
        for service in services:
            await service.aclose()


@pytest.mark.asyncio
async def test_invalid_refresh_clears_tokens_but_transient_keeps_them(tmp_path):
    status = 503

    def token(request):
        return httpx.Response(status, json={"error": "private-refresh"})

    service = ClaudeAuthService(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(token))
    )
    seed(service, expired=True)
    try:
        with pytest.raises(RuntimeError, match="could not refresh"):
            await service.bearer(REG)
        assert service.read(REG)["refresh_token"] == "private-refresh"
        status = 400
        with pytest.raises(ClaudeSessionInvalid):
            await service.bearer(REG)
        assert service.read(REG)["refresh_token"] is None
    finally:
        await service.aclose()


def stream_response(input_data=None, *, name="crucible_step", stop="tool_use", complete=True):
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 100,
                }
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "provider_1", "name": name, "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(input_data or {"ok": True}),
            },
        },
        {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 8}},
    ]
    if complete:
        events.append({"type": "message_stop"})
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(e) + "\n\n" for e in events),
    )


@pytest.mark.asyncio
async def test_native_history_cache_isolated_agents_and_usage():
    bodies = []

    def messages(request):
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["authorization"] == "Bearer test-access"
        assert "x-api-key" not in request.headers
        body = json.loads(request.content)
        assert [b["text"] for b in body["system"]][0] == IDENTITY
        assert body["tool_choice"] == {"type": "auto"}
        bodies.append(body)
        return stream_response()

    async def bearer():
        return "test-access"

    async with httpx.AsyncClient(transport=httpx.MockTransport(messages)) as client:
        transport = ClaudeSubscriptionTransport(bearer, registration_id=REG, client=client)

        async def call(owner, history, step):
            token = USAGE_OWNER.set(owner)
            try:
                return await transport.generate_json(
                    model="test-model",
                    schema_name="Probe",
                    schema=SCHEMA,
                    system_instructions=owner,
                    user_payload={
                        "workspace": "stable",
                        "conversation_history": history,
                        "step": step,
                    },
                    on_usage=lambda prompt, output: METER.record(
                        owner, prompt=prompt, completion=output
                    ),
                )
            finally:
                USAGE_OWNER.reset(token)

        assert await call("A", [], 1) == {"ok": True}
        await call("B", [], 1)
        history = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {"type": "tool_call", "tool": "read_file", "args": {"path": "a"}}
                ),
            },
            {"role": "tool_result", "tool": "read_file", "content": "file result"},
        ]
        await call("A", history, 2)
        content = bodies[2]["messages"]
        assert "Current step" in content[0]["content"][-1]["text"]
        assert content[1]["content"][0]["type"] == "tool_use"
        assert content[2]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "file result",
        }
        assert '"step": 1' in content[0]["content"][-1]["text"]
        assert '"step": 2' in content[-1]["content"][-1]["text"]
        assert "B" not in json.dumps(content)
        assert METER.take("A").prompt_tokens == 260
        assert METER.take("B").cached_tokens == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected",
    [
        (stream_response({"ok": "wrong"}), "does not match"),
        (stream_response(name="run_command"), "exactly one"),
        (stream_response(stop="max_tokens"), "truncated"),
        (stream_response(complete=False), "before completion"),
    ],
)
async def test_reject_invalid_or_partial_actions(response, expected):
    async def bearer():
        return "test"

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        transport = ClaudeSubscriptionTransport(bearer, registration_id=REG, client=client)
        with pytest.raises(RuntimeError, match=expected):
            await transport.generate_json(
                model="test",
                schema_name="test",
                schema=SCHEMA,
                system_instructions="test",
                user_payload={},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,kind", [(429, "usage_limit"), (401, "session_invalid"), (403, "session_invalid")]
)
async def test_account_errors_stop_controller_without_api_fallback(status, kind):
    async def bearer():
        return "test"

    def error(request):
        return httpx.Response(status, json={"error": {"message": "reflected-secret"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(error)) as client:
        transport = ClaudeSubscriptionTransport(bearer, registration_id=REG, client=client)
        with pytest.raises(ClaudeAccessStopped) as raised:
            await transport.generate_text(model="test", system_instructions="test", user_payload={})
        payload = access_payload(raised.value)
        assert payload["kind"] == kind and payload["provider"] == "claude_subscription"
        assert "ChatGPT" not in payload["message"] and "reflected-secret" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_cancel_exchange_never_saves_late_credentials(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def token(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=TOKENS)

    service = ClaudeAuthService(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(token)), callback_port=0
    )
    try:
        started, url = await service.start()
        params = parse_qs(urlsplit(url).query)
        async with httpx.AsyncClient() as browser:
            callback = asyncio.create_task(
                browser.get(
                    params["redirect_uri"][0], params={"state": params["state"][0], "code": "test"}
                )
            )
            await entered.wait()
            await service.cancel(started["attempt_id"])
            release.set()
            await callback
        assert service.status(started["attempt_id"])["reason"] == "cancelled"
        assert service.accounts() == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_file_lock_does_not_strand_refresh(tmp_path):
    from agentd.chatgpt_auth.files import exclusive_lock

    path = tmp_path / "test.lock"

    async def acquire():
        async with exclusive_lock(path):
            pass

    async with exclusive_lock(path):
        waiting = asyncio.create_task(acquire())
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    await asyncio.wait_for(acquire(), 1)


@pytest.mark.asyncio
async def test_batch_results_keep_member_metadata_and_images():
    from agentd.providers.claude_subscription import _Actions, messages_from_items
    from agentd.providers.openai_native import to_native_input

    action = {
        "type": "tool_calls",
        "thought": "read",
        "calls": [
            {"id": "a", "tool": "read_file", "args": {"path": "a"}},
            {"id": "b", "tool": "read_file", "args": {"path": "b"}},
        ],
    }
    history = [
        {"role": "assistant", "content": json.dumps(action), "batch_id": "batch"},
        {
            "role": "tool_result",
            "batch_id": "batch",
            "call_id": "a",
            "tool": "read_file",
            "content": "A",
            "image_refs": [{"asset_id": "image"}],
        },
        {
            "role": "tool_result",
            "batch_id": "batch",
            "call_id": "b",
            "tool": "read_file",
            "content": "B",
        },
    ]
    messages = messages_from_items(
        to_native_input(
            {"conversation_history": history, "goal": "continue"},
            _Actions(),
            {"image": "data:image/png;base64,eA=="},
        )
    )
    uses = [b for m in messages for b in m["content"] if b["type"] == "tool_use"]
    results = [b for m in messages for b in m["content"] if b["type"] == "tool_result"]
    images = [b for m in messages for b in m["content"] if b["type"] == "image"]
    assert len(uses) == len(results) == len(images) == 1
    assert len(uses[0]["input"]["calls"]) == 2
    assert '"call_id": "a"' in results[0]["content"] and '"call_id": "b"' in results[0]["content"]
    assert "eA==" not in results[0]["content"]


@pytest.mark.asyncio
async def test_factory_subscription_and_image_admission(tmp_path, monkeypatch):
    import agentd.claude_auth.service as module
    from agentd.providers.factory import build_transport, resolve_model
    from agentd.providers.image_capabilities import resolve_image_capability

    service = ClaudeAuthService(tmp_path)
    seed(service)
    monkeypatch.setattr(module, "_service", service)
    try:
        transport = build_transport("claude_subscription", {"CRUCIBLE_CLAUDE_REGISTRATION": REG})
        assert transport.registration_id == REG
        assert resolve_model("claude_subscription") == "claude-sonnet-5-5"
        assert (await resolve_image_capability(transport, "claude-sonnet-5-5")).supported
        with pytest.raises(RuntimeError, match="Connect Claude"):
            monkeypatch.delenv("CRUCIBLE_CLAUDE_REGISTRATION", raising=False)
            build_transport("claude_subscription")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_context_identity_changes_for_disconnect_not_token_rotation(tmp_path, monkeypatch):
    import agentd.claude_auth.service as module
    from agentd.providers.factory import build_transport
    from agentd.providers.runtime import ProviderRuntime

    service = ClaudeAuthService(tmp_path)
    seed(service)
    monkeypatch.setattr(module, "_service", service)
    try:
        transport = build_transport("claude_subscription", {"CRUCIBLE_CLAUDE_REGISTRATION": REG})
        runtime = ProviderRuntime(
            backend="claude_subscription",
            model="claude-sonnet-5-5",
            engines=[],
            transport=transport,
            context_window=128000,
        )
        before = runtime.context_identity()
        record = service.read(REG)
        record["access_token"] = "another-private-token"
        write_private_json(service.path(REG), record)
        assert runtime.context_identity() == before
        assert "private" not in str(before)
        await service.sign_out(REG)
        assert runtime.context_identity() != before
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_catalog_pagination_cache_and_disconnect(tmp_path):
    calls = []

    def catalog(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer private-access"
        assert request.url.host == "api.anthropic.com"
        if request.url.params.get("after_id"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "claude-other", "display_name": "Other", "max_input_tokens": 200000},
                        {
                            "id": "claude-retired",
                            "lifecycle": "retired",
                            "max_input_tokens": 100000,
                        },
                        {"id": "claude-unknown", "max_input_tokens": True},
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "claude-sonnet-5-5",
                        "display_name": "Claude Sonnet 5.5",
                        "max_input_tokens": 1000000,
                        "max_tokens": 128000,
                    },
                ],
                "has_more": True,
                "last_id": "claude-sonnet-5-5",
            },
        )

    service = ClaudeAuthService(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(catalog))
    )
    seed(service)
    app = FastAPI()
    app.include_router(build_claude_auth_router(lambda: service))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/v1/auth/claude/registrations/{REG}/models")
            assert response.status_code == 200
            rows = response.json()["models"]
            assert rows[0]["context_window"] == 1000000
            assert [m["slug"] for m in rows] == [
                "claude-sonnet-5-5",
                "claude-other",
                "claude-unknown",
            ]
            assert rows[-1]["context_window"] is None
            assert "private-" not in response.text
            assert await service.context_capacity(REG, "claude-other") == 200000
            assert await service.context_capacity(REG, "unlisted") is None
            assert len(calls) == 2
            # Returned objects cannot mutate the cached catalog.
            rows[0]["context_window"] = 5
            assert await service.context_capacity(REG, "claude-sonnet-5-5") == 1000000
            refreshed = await client.post(
                f"/v1/auth/claude/registrations/{REG}/models?refresh=true"
            )
            assert refreshed.status_code == 200 and len(calls) == 4
            await service.sign_out(REG)
            assert (
                await client.post(f"/v1/auth/claude/registrations/{REG}/models")
            ).status_code == 401
            assert not service._catalogs
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="private-access"),
        httpx.Response(200, json={"data": [], "has_more": True, "last_id": "same"}),
        httpx.Response(200, json={"data": "invalid"}),
    ],
)
async def test_catalog_errors_are_sanitized_and_not_cached(tmp_path, response):
    service = ClaudeAuthService(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    )
    seed(service)
    try:
        with pytest.raises(RuntimeError) as error:
            await service.models(REG)
        assert "private-access" not in str(error.value)
        assert not service._catalogs
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_catalog_disconnect_during_request_cannot_publish_stale_models(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(_):
        entered.set()
        await release.wait()
        return httpx.Response(
            200, json={"data": [{"id": "claude-sonnet-5-5", "max_input_tokens": 1000000}]}
        )

    service = ClaudeAuthService(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(delayed))
    )
    seed(service)
    try:
        task = asyncio.create_task(service.models(REG))
        await entered.wait()
        await service.sign_out(REG)
        release.set()
        with pytest.raises(ClaudeSessionInvalid):
            await task
        assert not service._catalogs
    finally:
        release.set()
        await service.aclose()


@pytest.mark.asyncio
async def test_catalog_capacity_controls_swap_startup_and_telemetry(monkeypatch):
    import agentd.providers.runtime as runtime_module
    from agentd.chat.context_usage import ContextUsageTracker
    from agentd.providers.runtime import ProviderRuntime
    from agentd.providers.validate import ProviderValidationError

    async def capacity(model):
        return {"claude-new": 1000000, "claude-small": 200000}.get(model)

    async def token():
        return "test-token"

    transport = ClaudeSubscriptionTransport(token, registration_id=REG, model_capacity=capacity)
    monkeypatch.setattr(transport, "generate_text", lambda **_: asyncio.sleep(0, result="OK"))
    monkeypatch.setattr(runtime_module, "build_transport", lambda *_, **__: transport)
    windows = []

    class Sink:
        def set_window_tokens(self, tokens):
            windows.append(tokens)

    runtime = ProviderRuntime(
        backend="openai", model="old", engines=[], window_sinks=[Sink()], context_window=128000
    )
    result = await runtime.swap(
        backend="claude_subscription", model="claude-new", context_window=1024
    )
    assert result["context_window"] == 1000000
    assert windows == [1000000]
    result = await runtime.swap(backend="claude_subscription", model="claude-small")
    assert result["context_window"] == 200000
    with pytest.raises(ProviderValidationError):
        await runtime.swap(backend="claude_subscription", model="unlisted")
    assert runtime.model == "claude-small" and runtime.context_window == 200000
    # Restarted processes resolve the catalog instead of retaining an old saved limit.
    runtime.context_window = 128000
    tracker = ContextUsageTracker(lambda *_: None)
    tracker.attach_runtime(runtime)
    assert tracker.snapshot("a").context_window is None
    try:
        for _ in range(10):
            await asyncio.sleep(0)
        snap = tracker.snapshot("a")
        assert (snap.context_window, snap.capacity_source) == (200000, "claude_catalog")
        assert runtime.context_window == 200000 and windows[-1] == 200000
    finally:
        await tracker.close()


@pytest.mark.asyncio
async def test_catalog_effort_metadata_is_validated_and_cached_defensively(tmp_path):
    service = ClaudeAuthService(tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"id": "claude-fixture", "max_input_tokens": 200000,
            "capabilities": {"effort": {"supported": True, "low": {"supported": True},
                "high": {"supported": True}, "xhigh": {"supported": False}, "max": {"supported": "true"}}}}]}))))
    seed(service)
    try:
        model = await service.plan_model(REG, "claude-fixture")
        assert model["reasoning_effort"] == {"supported": True, "low": True, "high": True, "xhigh": False}
        model["reasoning_effort"]["xhigh"] = True
        assert (await service.plan_model(REG, "claude-fixture"))["reasoning_effort"]["xhigh"] is False
    finally:
        await service.aclose()
