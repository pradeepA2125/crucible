"""Continue with ChatGPT: one browser authorization attempt per sign-in.

Each attempt gets fresh `state`, OIDC `nonce` and a PKCE verifier, and its own
one-shot loopback listener on 127.0.0.1 (never `localhost`; only the port may vary
between attempts, the path is always /auth/callback). The listener is a separate
socket from the backend's API, so the API's auth middleware never sees the browser.

First sign-in for an account registers a new client (`client_id=dynamic_agent_client`
+ `agent_name_hint`); the callback carries the issued `oaiapp_…` id, which is what the
code is exchanged with and what every later sign-in reuses. A returning account must
come back as the same subject with the same client id, or nothing is replaced.
https://developers.openai.com/siwc/token-sharing-open-source/sign-in
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import html
import logging
import secrets
import urllib.parse
from dataclasses import dataclass, field
from typing import Literal

from agentd.chatgpt_auth.oidc import (
    API_RESOURCE,
    IdTokenInvalid,
    OidcClient,
    TokenEndpointError,
    TokenResponse,
)
from agentd.chatgpt_auth.store import CredentialRecord, CredentialStore

logger = logging.getLogger(__name__)

AGENT_NAME = "Crucible"
REGISTRATION_CLIENT_ID = "dynamic_agent_client"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
PREFERRED_PORT = 1455
ATTEMPT_TIMEOUT_SEC = 600.0
_MAX_REQUEST_BYTES = 16384

FailureReason = Literal[
    "access_denied", "registration_incomplete", "client_mismatch", "account_mismatch",
    "invalid_id_token", "exchange_failed", "timed_out", "cancelled", "oauth_error",
]
_FAILURE_TEXT: dict[str, str] = {
    "access_denied": "Sign-in was cancelled in the browser.",
    "registration_incomplete": "ChatGPT did not finish registering Crucible. Try again.",
    "client_mismatch": "ChatGPT returned a different app registration for this account.",
    "account_mismatch": "You signed in to a different ChatGPT account than the one selected.",
    "invalid_id_token": "ChatGPT's sign-in response could not be verified.",
    "exchange_failed": "ChatGPT did not accept the sign-in. Try again.",
    "timed_out": "Sign-in took too long. Try again.",
    "cancelled": "Sign-in was cancelled.",
    "oauth_error": "ChatGPT reported an error during sign-in.",
}


@dataclass
class AttemptStatus:
    attempt_id: str
    state: Literal["pending", "succeeded", "failed"] = "pending"
    registration_id: str | None = None
    plan_enabled: bool | None = None
    first_plan_sign_in: bool = False
    reason: FailureReason | None = None
    message: str | None = None


@dataclass
class _Attempt:
    status: AttemptStatus
    registration_id: str | None
    client_id: str
    state: str
    nonce: str
    verifier: str
    redirect_uri: str = ""
    server: asyncio.Server | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    timeout_task: asyncio.Task[None] | None = None


class SignInInProgress(RuntimeError):
    pass


class AttemptManager:
    def __init__(
        self,
        store: CredentialStore,
        oidc: OidcClient,
        *,
        preferred_port: int = PREFERRED_PORT,
        timeout_sec: float = ATTEMPT_TIMEOUT_SEC,
    ) -> None:
        self._store = store
        self._oidc = oidc
        self._preferred_port = preferred_port
        self._timeout_sec = timeout_sec
        self._attempts: dict[str, _Attempt] = {}

    async def start(
        self, *, registration_id: str | None = None, reconsent: bool = False
    ) -> tuple[AttemptStatus, str]:
        """Begin a sign-in. Returns the status handle and the URL to open in a browser.

        The URL can carry an ID token hint: hand it to the browser, never to a log.
        """
        if any(a.status.state == "pending" for a in self._attempts.values()):
            raise SignInInProgress("A ChatGPT sign-in is already waiting for the browser")
        existing = self._store.require(registration_id) if registration_id else None
        disco = await self._oidc.discovery()
        host_id = await asyncio.to_thread(self._store.host_id)
        verifier = _b64url(secrets.token_bytes(48))
        attempt = _Attempt(
            status=AttemptStatus(attempt_id=f"att_{secrets.token_hex(8)}"),
            registration_id=registration_id,
            client_id=existing.client_id if existing else REGISTRATION_CLIENT_ID,
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            verifier=verifier,
        )
        attempt.server = await self._listen(attempt)
        port = attempt.server.sockets[0].getsockname()[1]
        attempt.redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"

        params: dict[str, str] = {
            "client_id": attempt.client_id,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": attempt.redirect_uri,
            "scope": SCOPES,
            "resource": API_RESOURCE,
            "state": attempt.state,
            "nonce": attempt.nonce,
            "code_challenge_method": "S256",
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        }
        if existing is None:
            params["agent_name_hint"] = AGENT_NAME
        else:
            if existing.id_token:
                params["id_token_hint"] = existing.id_token
            if existing.email:
                params["login_hint"] = existing.email
        if reconsent:
            # Re-enabling plan usage after a decline. `force_reconsent` replaces this
            # only once OpenAI confirms deployment for the integration (errors page).
            params["prompt"] = "consent"

        self._attempts[attempt.status.attempt_id] = attempt
        attempt.timeout_task = asyncio.create_task(self._expire(attempt))
        # %20, never "+": the extension hands this to vscode.env.openExternal, and a
        # parse/re-serialize round trip must not change what a space means.
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        url = f"{disco.authorization_endpoint}?{query}"
        return attempt.status, url

    def status(self, attempt_id: str) -> AttemptStatus | None:
        attempt = self._attempts.get(attempt_id)
        return attempt.status if attempt else None

    async def wait(self, attempt_id: str) -> AttemptStatus:
        attempt = self._attempts[attempt_id]
        await attempt.done.wait()
        return attempt.status

    async def cancel(self, attempt_id: str) -> None:
        attempt = self._attempts.get(attempt_id)
        if attempt and attempt.status.state == "pending":
            await self._finish(attempt, failure="cancelled")

    async def aclose(self) -> None:
        for attempt in list(self._attempts.values()):
            if attempt.status.state == "pending":
                await self._finish(attempt, failure="cancelled")

    # ------------------------------------------------------------ listener

    async def _listen(self, attempt: _Attempt) -> asyncio.Server:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await self._handle_connection(attempt, reader, writer)
            finally:
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()

        for port in (self._preferred_port, 0):
            try:
                return await asyncio.start_server(handle, "127.0.0.1", port)
            except OSError:
                continue
        raise RuntimeError("could not open a loopback port for the sign-in callback")

    async def _handle_connection(
        self, attempt: _Attempt, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            return
        if len(head) > _MAX_REQUEST_BYTES:
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request_line.split(" ")
        if len(parts) < 2 or parts[0] != "GET":
            await _respond(writer, 405, "Not supported.")
            return
        target = urllib.parse.urlsplit(parts[1])
        if target.path != CALLBACK_PATH:
            await _respond(writer, 404, "Not found.")
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(target.query).items()}
        if attempt.status.state != "pending":
            await _respond(writer, 200, _page_for(attempt.status))
            return
        if not secrets.compare_digest(query.get("state", ""), attempt.state):
            # A stray or forged request: ignore it and keep waiting for the real one.
            await _respond(writer, 400, "This sign-in link is not for the current attempt.")
            return
        await self._complete(attempt, query)
        await _respond(writer, 200, _page_for(attempt.status))

    # ------------------------------------------------------------ completion

    async def _complete(self, attempt: _Attempt, query: dict[str, str]) -> None:
        error = query.get("error")
        if error:
            await self._finish(attempt, failure="access_denied" if error == "access_denied"
                               else "oauth_error")
            return
        returned_client = query.get("client_id")
        if attempt.registration_id is None:
            if not returned_client or not returned_client.startswith("oaiapp_"):
                await self._finish(attempt, failure="registration_incomplete")
                return
            client_id = returned_client
        else:
            if returned_client and returned_client != attempt.client_id:
                await self._finish(attempt, failure="client_mismatch")
                return
            client_id = attempt.client_id
        code = query.get("code")
        if not code:
            await self._finish(attempt, failure="oauth_error")
            return
        try:
            tokens = await self._oidc.exchange_code(
                client_id=client_id, code=code, code_verifier=attempt.verifier,
                redirect_uri=attempt.redirect_uri)
            if not tokens.id_token:
                raise IdTokenInvalid("token response carried no ID token")
            claims = await self._oidc.validate_id_token(
                tokens.id_token, client_id=client_id, nonce=attempt.nonce)
        except TokenEndpointError as exc:
            logger.warning("ChatGPT code exchange failed: %s", exc.code or exc.status)
            await self._finish(attempt, failure="exchange_failed")
            return
        except IdTokenInvalid as exc:
            logger.warning("ChatGPT ID token rejected: %s", exc)
            await self._finish(attempt, failure="invalid_id_token")
            return

        if attempt.registration_id is None:
            await self._save(attempt, client_id, tokens, claims)
            return
        # A returning account's record may be mid-refresh in another backend.
        async with self._store.locked(attempt.registration_id):
            await self._save(attempt, client_id, tokens, claims)

    async def _save(
        self, attempt: _Attempt, client_id: str, tokens: TokenResponse,
        claims: dict[str, object],
    ) -> None:
        subject = str(claims["sub"])
        raw_email, raw_name = claims.get("email"), claims.get("name")
        email = raw_email if isinstance(raw_email, str) else None
        name = raw_name if isinstance(raw_name, str) else None
        disco = await self._oidc.discovery()
        if attempt.registration_id is not None:
            previous = self._store.require(attempt.registration_id)
            if previous.subject != subject:
                await self._finish(attempt, failure="account_mismatch")
                return
            registration_id = previous.registration_id
            label = previous.label
            was_plan_enabled = previous.plan_enabled
        else:
            registration_id = self._store.new_registration_id()
            label = self._store.unique_label(email)
            was_plan_enabled = False
        record = CredentialRecord(
            registration_id=registration_id, label=label, email=email, name=name,
            issuer=disco.issuer, subject=subject, client_id=client_id,
            ext_agent_host_id=self._store.host_id(), id_token=tokens.id_token,
            access_token=tokens.access_token, refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at, earliest_refresh_at=tokens.earliest_refresh_at,
            scopes=tokens.scopes, saved_at=tokens.received_at,
        )
        self._store.save(record)
        attempt.status.registration_id = registration_id
        attempt.status.plan_enabled = record.plan_enabled
        attempt.status.first_plan_sign_in = record.plan_enabled and not was_plan_enabled
        await self._finish(attempt, failure=None)

    async def _expire(self, attempt: _Attempt) -> None:
        await asyncio.sleep(self._timeout_sec)
        if attempt.status.state == "pending":
            await self._finish(attempt, failure="timed_out")

    async def _finish(self, attempt: _Attempt, *, failure: FailureReason | None) -> None:
        if attempt.status.state != "pending":
            return
        if failure is None:
            attempt.status.state = "succeeded"
        else:
            attempt.status.state = "failed"
            attempt.status.reason = failure
            attempt.status.message = _FAILURE_TEXT[failure]
        attempt.done.set()
        if attempt.timeout_task is not None and attempt.timeout_task is not asyncio.current_task():
            attempt.timeout_task.cancel()
        if attempt.server is not None:
            attempt.server.close()  # stop accepting; the in-flight response still completes


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _page_for(status: AttemptStatus) -> str:
    if status.state == "succeeded":
        if status.plan_enabled:
            return "You're signed in to Crucible with ChatGPT. You can close this tab."
        return ("You're signed in, but ChatGPT plan usage wasn't allowed. "
                "Return to VS Code to enable it or use an API key instead.")
    return status.message or "Sign-in did not complete. Return to VS Code to try again."


async def _respond(writer: asyncio.StreamWriter, status: int, text: str) -> None:
    body = (
        "<!doctype html><meta charset=utf-8><title>Crucible</title>"
        "<body style=\"font:15px system-ui,sans-serif;margin:15vh auto;max-width:28rem;"
        "text-align:center\">"
        f"<p>{html.escape(text)}</p></body>"
    ).encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}
    writer.write(
        f"HTTP/1.1 {status} {reason.get(status, 'OK')}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n"
        .encode() + body)
    with contextlib.suppress(ConnectionError):
        await writer.drain()

