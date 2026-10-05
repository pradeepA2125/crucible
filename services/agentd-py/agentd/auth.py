"""Request authentication (spec §3.3) and the /health proof (§3.4).

A pure ASGI middleware — BaseHTTPMiddleware would buffer SSE. Checks, in order:
loopback peer (403) → exact Host 127.0.0.1:<port> (421) → no Origin (403) → bearer
token (401). The only exemption is exactly GET /health, which instead returns HMAC
proofs that the server holds the token without ever receiving it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from agentd.auth_token import TokenFileError, read_token, touch_token
from agentd.startup import in_background

logger = logging.getLogger(__name__)

_NONCE_RE = re.compile(r"[0-9a-f]{16,64}")
_PROOF_PREFIX = b"crucible-health-v1\0"
_BOUND_PREFIX = b"crucible-health-bound-v1\0"
_DISABLED_WARN_EVERY = 60
_KEEPALIVE_SEC = 86400.0


@dataclass
class AuthState:
    port: int | None = None
    token: bytes | None = None
    serve_pid: int = 0
    workspace: str = ""
    disabled: bool = False


AUTH_STATE = AuthState()


class AuthStartupError(RuntimeError):
    """The app was started without what agentd.serve provides."""


def load_auth_state(state: AuthState, environ: Mapping[str, str], home: Path) -> None:
    raw_port = environ.get("CRUCIBLE_LISTEN_PORT", "")
    if not raw_port.isdigit():
        raise AuthStartupError(
            "CRUCIBLE_LISTEN_PORT is not set: start the backend with "
            "`python -m agentd.serve`, not `uvicorn agentd.main:app`")
    port = int(raw_port)
    try:
        token = read_token(home, port)
    except TokenFileError as exc:
        raise AuthStartupError(f"refusing to start: {exc}") from exc
    if token is None:
        raise AuthStartupError(
            f"no token file for port {port}; start the backend with `python -m agentd.serve`")
    raw_pid = environ.get("CRUCIBLE_SERVE_PID", "")
    state.port = port
    state.token = token.encode("ascii")
    state.serve_pid = int(raw_pid) if raw_pid.isdigit() else os.getpid()
    state.workspace = environ.get("CRUCIBLE_WORKSPACE_PATH", "")
    state.disabled = environ.get("CRUCIBLE_AUTH_DISABLED", "") == "1"
    if state.disabled:
        logger.warning(
            "CRUCIBLE_AUTH_DISABLED=1: the token check is OFF. Any local user, and any "
            "web page doing blind GETs, can drive this backend.")


def health_proof(token: bytes, nonce: str) -> str:
    return hmac.new(token, _PROOF_PREFIX + nonce.encode("ascii"), hashlib.sha256).hexdigest()


def health_bound(token: bytes, nonce: str, pid: int, workspace: str) -> str:
    message = _BOUND_PREFIX + f"{nonce}\0{pid}\0{workspace}".encode("utf-8")
    return hmac.new(token, message, hashlib.sha256).hexdigest()


def health_payload(state: AuthState, query_string: bytes) -> dict[str, object]:
    nonces = parse_qs(query_string.decode("latin-1"), keep_blank_values=True).get("nonce", [])
    if state.token is None or len(nonces) != 1 or not _NONCE_RE.fullmatch(nonces[0]):
        return {"status": "ok"}
    nonce = nonces[0]
    return {
        "status": "ok",
        "pid": state.serve_pid,
        "proof": health_proof(state.token, nonce),
        "bound": health_bound(state.token, nonce, state.serve_pid, state.workspace),
    }


def _is_loopback(client: Any) -> bool:
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def _bearer(headers: list[tuple[bytes, bytes]]) -> bytes | None:
    values = [v for k, v in headers if k == b"authorization"]
    if len(values) != 1:
        return None
    scheme, _, credential = values[0].partition(b" ")
    if scheme.lower() != b"bearer" or not credential.strip():
        return None
    return credential.strip()


async def _respond(send: Send, status: int, text: str) -> None:
    body = text.encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    def __init__(self, app: ASGIApp, state: AuthState) -> None:
        self.app = app
        self.state = state
        self._disabled_requests = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        state = self.state
        if state.port is None or state.token is None:
            await _respond(send, 503, "backend is starting")
            return
        if not _is_loopback(scope.get("client")):
            await _respond(send, 403, "only loopback clients are accepted")
            return
        headers: list[tuple[bytes, bytes]] = scope["headers"]
        hosts = [v for k, v in headers if k == b"host"]
        expected_host = f"127.0.0.1:{state.port}".encode("ascii")
        if len(hosts) != 1 or hosts[0] != expected_host:
            await _respond(send, 421, f"connect to 127.0.0.1:{state.port}")
            return
        if any(k == b"origin" for k, _ in headers):
            await _respond(send, 403, "cross-origin requests are refused")
            return
        is_health = scope["method"] == "GET" and scope["path"] == "/health"
        if not is_health:
            if state.disabled:
                self._disabled_requests += 1
                if self._disabled_requests % _DISABLED_WARN_EVERY == 0:
                    logger.warning("CRUCIBLE_AUTH_DISABLED=1: the token check is still OFF")
            else:
                presented = _bearer(headers)
                if presented is None:
                    await _respond(send, 401, "missing Authorization: Bearer token")
                    return
                if not hmac.compare_digest(presented, state.token):
                    await _respond(send, 401, "invalid token")
                    return
        await self.app(scope, receive, send)


def auth_middleware(state: AuthState) -> Middleware:
    return Middleware(AuthMiddleware, state=state)


def install_auth(app: FastAPI, state: AuthState) -> None:
    def _load() -> None:
        environ = getattr(app.state, "auth_environ", None) or os.environ
        home = getattr(app.state, "auth_home", None) or Path.home()
        load_auth_state(state, environ, home)

    async def _keepalive() -> None:
        home = getattr(app.state, "auth_home", None) or Path.home()
        while True:
            await asyncio.sleep(_KEEPALIVE_SEC)
            if state.port is not None:
                touch_token(home, state.port)

    # Index 0: every other hook was registered at import time and must run with
    # AuthState filled; the middleware answers 503 until this has run.
    app.router.on_startup.insert(0, _load)
    app.router.add_event_handler("startup", in_background(_keepalive, "token-keepalive"))
