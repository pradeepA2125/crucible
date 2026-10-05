"""Auth middleware (spec §3.3) and the /health proof (§3.4)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentd.auth import (
    AuthStartupError,
    AuthState,
    auth_middleware,
    health_bound,
    health_payload,
    health_proof,
    install_auth,
    load_auth_state,
)
from agentd.auth_token import write_token

PORT = 8123
TOKEN = "t" * 43
BASE = f"http://127.0.0.1:{PORT}"
LOOPBACK = ("127.0.0.1", 50000)
NONCE = "0123456789abcdef"
SSE_RELEASE = threading.Event()


def _app(state: AuthState) -> FastAPI:
    app = FastAPI(middleware=[auth_middleware(state)])

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        return health_payload(state, request.scope["query_string"])

    @app.get("/x")
    async def x() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/sse")
    async def sse() -> StreamingResponse:
        async def gen():
            yield b"data: first\n\n"
            # Ends only once the test has read the first chunk: a buffering middleware
            # would deadlock here (and fail by timeout) instead of streaming.
            await asyncio.to_thread(SSE_RELEASE.wait, 10)
            yield b"data: last\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def _ready() -> AuthState:
    return AuthState(port=PORT, token=TOKEN.encode(), serve_pid=777, workspace="/w s")


def _client(state: AuthState | None = None, client=LOOPBACK) -> TestClient:
    return TestClient(_app(state or _ready()), base_url=BASE, client=client,
                      follow_redirects=False)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_token_accepted() -> None:
    assert _client().get("/x", headers=AUTH).status_code == 200


def test_bearer_scheme_is_case_insensitive() -> None:
    assert _client().get("/x", headers={"Authorization": f"bEaReR {TOKEN}"}).status_code == 200


def test_missing_token_401_names_no_value() -> None:
    resp = _client().get("/x")
    assert resp.status_code == 401
    assert TOKEN not in resp.text


def test_wrong_and_non_ascii_token_401() -> None:
    assert _client().get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    raw = {"Authorization": b"Bearer " + "é".encode() * 10}
    assert _client().get("/x", headers=raw).status_code == 401


@pytest.mark.parametrize("peer", [("10.0.0.5", 1), ("testclient", 50000)])
def test_non_loopback_or_non_ip_peer_403(peer) -> None:
    assert _client(client=peer).get("/x", headers=AUTH).status_code == 403


def test_ipv6_loopback_peer_allowed() -> None:
    assert _client(client=("::1", 1)).get("/x", headers=AUTH).status_code == 200


@pytest.mark.parametrize("host", ["localhost:8123", "127.0.0.1", "127.0.0.1:9999", "evil.example"])
def test_wrong_host_421(host: str) -> None:
    resp = _client().get("/x", headers={**AUTH, "Host": host})
    assert resp.status_code == 421
    assert "127.0.0.1:8123" in resp.text


@pytest.mark.parametrize("origin", ["null", "https://evil.example", "vscode-webview://x"])
def test_any_origin_403(origin: str) -> None:
    assert _client().get("/x", headers={**AUTH, "Origin": origin}).status_code == 403


def test_checks_run_in_order() -> None:
    # Bad peer AND bad host AND origin AND no token → the peer check answers.
    resp = _client(client=("10.0.0.5", 1)).get("/x", headers={"Host": "evil", "Origin": "null"})
    assert resp.status_code == 403
    resp = _client().get("/x", headers={"Host": "evil", "Origin": "null"})
    assert resp.status_code == 421
    resp = _client().get("/x", headers={"Origin": "null"})
    assert resp.status_code == 403


def test_empty_state_503() -> None:
    assert _client(AuthState()).get("/x", headers=AUTH).status_code == 503


def test_health_needs_no_token_and_proves_it() -> None:
    body = _client().get(f"/health?nonce={NONCE}").json()
    assert body["proof"] == hmac.new(
        TOKEN.encode(), b"crucible-health-v1\0" + NONCE.encode(), hashlib.sha256).hexdigest()
    assert body["pid"] == 777
    assert body["bound"] == health_bound(TOKEN.encode(), NONCE, 777, "/w s")
    assert body["bound"] != health_bound(TOKEN.encode(), NONCE, 777, "/w")
    assert body["proof"] == health_proof(TOKEN.encode(), NONCE)


@pytest.mark.parametrize("query", ["", "nonce=short", f"nonce={NONCE}&nonce={NONCE}",
                                   "nonce=" + "G" * 16, "nonce=" + "a" * 65])
def test_health_without_valid_nonce_has_no_proof(query: str) -> None:
    body = _client().get(f"/health?{query}").json()
    assert body == {"status": "ok"}


@pytest.mark.parametrize("method,path", [("GET", "/health/"), ("GET", "/healthz"),
                                         ("HEAD", "/health")])
def test_health_exemption_is_exact(method: str, path: str) -> None:
    assert _client().request(method, path).status_code == 401


def test_health_still_checks_host_and_origin() -> None:
    assert _client().get("/health", headers={"Host": "localhost:8123"}).status_code == 421
    assert _client().get("/health", headers={"Origin": "null"}).status_code == 403


def test_auth_disabled_skips_only_the_token() -> None:
    state = _ready()
    state.disabled = True
    assert _client(state).get("/x").status_code == 200
    assert _client(state).get("/x", headers={"Origin": "null"}).status_code == 403


def test_sse_streams_first_chunk_before_generator_ends() -> None:
    # A real server: TestClient collects the whole body before returning, so it cannot
    # tell a streaming middleware from a buffering one.
    SSE_RELEASE.clear()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    state = AuthState(port=port, token=TOKEN.encode(), serve_pid=1, workspace="")
    server = uvicorn.Server(uvicorn.Config(_app(state), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/sse", headers=AUTH)
        resp = conn.getresponse()
        assert resp.status == 200
        first = resp.read1(64)  # times out (fails) if the middleware buffered
        assert first.startswith(b"data: first")
    finally:
        SSE_RELEASE.set()
        server.should_exit = True
        thread.join(10)


def test_websocket_closed() -> None:
    app = _app(_ready())

    @app.websocket("/ws")
    async def ws(websocket) -> None:  # pragma: no cover - never reached
        await websocket.accept()

    client = TestClient(app, base_url=BASE, client=LOOPBACK)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws"):
            pass
    assert excinfo.value.code == 1008


def test_lifespan_passes(tmp_path: Path) -> None:
    write_token(tmp_path, PORT)
    state = AuthState()
    app = _app(state)
    install_auth(app, state)
    env = {"CRUCIBLE_LISTEN_PORT": str(PORT), "CRUCIBLE_SERVE_PID": "42",
           "CRUCIBLE_WORKSPACE_PATH": "/ws"}
    app.state.auth_environ = env
    app.state.auth_home = tmp_path
    with TestClient(app, base_url=BASE, client=LOOPBACK) as client:
        assert state.serve_pid == 42 and state.workspace == "/ws"
        assert client.get("/health").status_code == 200


def test_load_requires_listen_port(tmp_path: Path) -> None:
    with pytest.raises(AuthStartupError, match="agentd.serve"):
        load_auth_state(AuthState(), {}, tmp_path)


def test_load_requires_token_file(tmp_path: Path) -> None:
    with pytest.raises(AuthStartupError):
        load_auth_state(AuthState(), {"CRUCIBLE_LISTEN_PORT": "8123"}, tmp_path)


def test_rejected_and_accepted_requests_log_no_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    _client().get("/x", headers=AUTH)
    _client().get("/x", headers={"Authorization": "Bearer wrong-but-logged?"})
    assert TOKEN not in caplog.text
    assert "wrong-but-logged" not in caplog.text
