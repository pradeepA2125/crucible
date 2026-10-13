"""Pi-compatible browser PKCE login. Tokens never leave the backend.

This uses the public client/profile used by Pi, not a Crucible-issued OAuth client.
Private atomic files and cross-process refresh locks follow our ChatGPT store.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from agentd.chatgpt_auth.files import (
    ensure_private_dir,
    exclusive_lock,
    read_private_json,
    write_private_json,
)
from agentd.providers.plan_access import ProviderAccessStopped

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
SCOPES = (
    "org:create_api_key user:profile user:inference user:sessions:claude_code "
    "user:mcp_servers user:file_upload"
)
ID_PATTERN = re.compile(r"claude_[a-f0-9]{24}\Z")


class ClaudeSessionInvalid(ProviderAccessStopped):
    kind = "session_invalid"
    provider = "claude_subscription"


@dataclass
class Attempt:
    attempt_id: str
    state: str = "pending"
    registration_id: str | None = None
    reason: str | None = None
    message: str | None = None
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    redirect_uri: str = ""
    server: asyncio.Server | None = field(default=None, repr=False)
    timer: asyncio.Task | None = field(default=None, repr=False)
    exchange: asyncio.Task | None = field(default=None, repr=False)

    def summary(self) -> dict:
        return {
            key: getattr(self, key)
            for key in ("attempt_id", "state", "registration_id", "reason", "message")
        }


class ClaudeAuthService:
    def __init__(
        self,
        root: Path | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        callback_port: int = 53692,
        lifetime: float = 600,
    ) -> None:
        self.root = root or Path(
            os.environ.get("CRUCIBLE_CLAUDE_AUTH_DIR", str(Path.home() / ".crucible/auth/claude"))
        )
        self.client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.callback_port = callback_port
        self.lifetime = lifetime
        self.attempts: dict[str, Attempt] = {}
        self._refreshes: dict[str, asyncio.Task] = {}
        self._start_lock = asyncio.Lock()
        self._catalogs: dict[str, tuple[float, list[dict]]] = {}
        self._catalog_locks: dict[str, asyncio.Lock] = {}

    def path(self, registration_id: str) -> Path:
        if not ID_PATTERN.fullmatch(registration_id):
            raise KeyError("Unknown Claude connection")
        return self.root / f"{registration_id}.json"

    def read(self, registration_id: str) -> dict:
        record = read_private_json(self.path(registration_id))
        if record is None:
            raise KeyError("Unknown Claude connection")
        return record

    def accounts(self) -> list[dict]:
        if not self.root.exists():
            return []
        return [
            {
                "registration_id": p.stem,
                "label": r.get("label", "Claude account"),
                "signed_in": bool(r.get("access_token") and r.get("refresh_token")),
            }
            for p in sorted(self.root.glob("claude_*.json"))
            if ID_PATTERN.fullmatch(p.stem) and (r := self.read(p.stem))
        ]

    async def start(self) -> tuple[dict, str]:
        async with self._start_lock:
            if any(a.state == "pending" for a in self.attempts.values()):
                raise RuntimeError("A Claude sign-in is already in progress")
            attempt = Attempt(secrets.token_urlsafe(18))

            async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                await self._callback(attempt, reader, writer)

            try:
                attempt.server = await asyncio.start_server(
                    handler, "127.0.0.1", self.callback_port, limit=16384
                )
            except OSError:
                attempt.server = await asyncio.start_server(handler, "127.0.0.1", 0, limit=16384)
            port = attempt.server.sockets[0].getsockname()[1]
            attempt.redirect_uri = f"http://localhost:{port}/callback"
            self.attempts[attempt.attempt_id] = attempt
            # Bound completed status retention; active browser handshakes remain intact.
            for old in list(self.attempts)[:-32]:
                if self.attempts[old].state != "pending":
                    del self.attempts[old]
            attempt.timer = asyncio.create_task(self._expire(attempt))
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            url = "https://claude.ai/oauth/authorize?" + urlencode(
                {
                    "code": "true",
                    "client_id": CLIENT_ID,
                    "response_type": "code",
                    "redirect_uri": attempt.redirect_uri,
                    "scope": SCOPES,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": attempt.nonce,
                }
            )
            return attempt.summary(), url

    def status(self, attempt_id: str) -> dict:
        return self.attempts[attempt_id].summary()

    async def _finish(self, attempt: Attempt, *, reason: str | None = None) -> None:
        if reason:
            attempt.state = "failed"
            attempt.reason = reason
            attempt.message = {
                "cancelled": "Claude sign-in cancelled.",
                "timeout": "Claude sign-in timed out. Try again.",
                "denied": "Claude sign-in was declined. Try again when ready.",
                "exchange_failed": "Claude sign-in could not be completed. Try again.",
            }[reason]
        if attempt.server:
            attempt.server.close()
            # Python 3.12 waits for accepted connections too. The callback is awaiting
            # this exchange, so waiting here would deadlock its HTTP response.
        if attempt.timer and attempt.timer is not asyncio.current_task():
            attempt.timer.cancel()

    async def _expire(self, attempt: Attempt) -> None:
        await asyncio.sleep(self.lifetime)
        await self.cancel(attempt.attempt_id, reason="timeout")

    async def cancel(self, attempt_id: str, *, reason: str = "cancelled") -> dict:
        attempt = self.attempts[attempt_id]
        if attempt.state == "pending":
            # Mark before cancelling exchange: no late callback can save credentials.
            attempt.state = "failed"
            if attempt.exchange and attempt.exchange is not asyncio.current_task():
                attempt.exchange.cancel()
                await asyncio.gather(attempt.exchange, return_exceptions=True)
            await self._finish(attempt, reason=reason)
        return attempt.summary()

    async def _callback(
        self, attempt: Attempt, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        status, text = 400, "Invalid Claude sign-in callback."
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            url = urlsplit(target)
            query = parse_qs(url.query)
            state = query.get("state", [""])[0]
            if (
                method == "GET"
                and url.path == "/callback"
                and attempt.state == "pending"
                and hmac.compare_digest(state, attempt.nonce)
                and not attempt.exchange
            ):
                if "error" in query:
                    await self._finish(attempt, reason="denied")
                    status, text = 200, "Claude sign-in was declined. Return to Crucible."
                elif query.get("code", [""])[0]:
                    attempt.exchange = asyncio.create_task(
                        self._exchange(attempt, query["code"][0])
                    )
                    await attempt.exchange
                    status = 200 if attempt.state == "succeeded" else 400
                    text = (
                        "Claude connected. Return to Crucible."
                        if status == 200
                        else "Sign-in failed. Return to Crucible and retry."
                    )
        except (
            ValueError,
            TypeError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            pass
        except asyncio.CancelledError:
            text = "Claude sign-in cancelled. Return to Crucible."
        finally:
            body = text.encode()
            writer.write(
                (
                    f"HTTP/1.1 {status} Result\r\nContent-Type: text/plain; charset=utf-8"
                    f"\r\nContent-Length: {len(body)}\r\nCache-Control: no-store"
                    "\r\nConnection: close\r\n\r\n"
                ).encode()
                + body
            )
            try:
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            writer.close()
            await writer.wait_closed()

    async def _exchange(self, attempt: Attempt, code: str) -> None:
        try:
            response = await self.client.post(
                TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "state": attempt.nonce,
                    "redirect_uri": attempt.redirect_uri,
                    "code_verifier": attempt.verifier,
                },
            )
            response.raise_for_status()
            tokens = self._tokens(response.json())
            if attempt.state != "pending":
                return
            registration = "claude_" + secrets.token_hex(12)
            account = response.json().get("account") or {}
            label = account.get("email_address") if isinstance(account, dict) else None
            record = {**tokens, "label": label or "Claude account", "registration_id": registration}
            ensure_private_dir(self.root)
            async with exclusive_lock(self.path(registration).with_suffix(".lock")):
                if attempt.state != "pending":
                    return
                write_private_json(self.path(registration), record)
                attempt.registration_id, attempt.state = registration, "succeeded"
            await self._finish(attempt)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Token endpoints may echo sensitive values; never surface response bodies.
            await self._finish(attempt, reason="exchange_failed")

    @staticmethod
    def _tokens(data: dict) -> dict:
        access, refresh = data.get("access_token"), data.get("refresh_token")
        expiry = float(data["expires_in"])
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ValueError("Missing Claude credentials")
        if not 0 < expiry < 365 * 86400:
            raise ValueError("Invalid Claude token lifetime")
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": time.time() + expiry,
        }

    async def bearer(self, registration_id: str) -> str:
        # Shield the whole lock operation. Caller cancellation must not strand a worker
        # holding a file lock, or discard a successfully rotated refresh token.
        task = self._refreshes.get(registration_id)
        if task is None or task.done():
            task = asyncio.create_task(self._bearer_locked(registration_id))
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            self._refreshes[registration_id] = task
        return await asyncio.shield(task)

    async def _bearer_locked(self, registration_id: str) -> str:
        async with exclusive_lock(self.path(registration_id).with_suffix(".lock")):
            record = self.read(registration_id)
            if not record.get("access_token") or not record.get("refresh_token"):
                raise ClaudeSessionInvalid(
                    "Your Claude connection has ended. Connect again in settings."
                )
            if float(record.get("expires_at", 0)) > time.time() + 300:
                return record["access_token"]
            response = await self.client.post(
                TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": record["refresh_token"],
                },
            )
            if response.status_code in (400, 401, 403):
                record.update(access_token=None, refresh_token=None, expires_at=0)
                write_private_json(self.path(registration_id), record)
                raise ClaudeSessionInvalid(
                    "Your Claude connection has expired. Connect again in settings."
                )
            if response.is_error:
                raise RuntimeError("Claude could not refresh the connection. Try again shortly.")
            try:
                record.update(self._tokens(response.json()))
            except (ValueError, KeyError, TypeError):
                raise RuntimeError("Claude returned an invalid refresh response.") from None
            write_private_json(self.path(registration_id), record)
            return record["access_token"]

    def _require_signed_in(self, registration_id: str) -> None:
        record = self.read(registration_id)
        if not record.get("access_token") or not record.get("refresh_token"):
            self._catalogs.pop(registration_id, None)
            raise ClaudeSessionInvalid(
                "Your Claude connection has ended. Connect again in settings."
            )

    async def models(self, registration_id: str, *, refresh: bool = False) -> list[dict]:
        """Account-scoped, bounded Models API cache. Never infer entitlement from visibility."""
        self._require_signed_in(registration_id)
        lock = self._catalog_locks.setdefault(registration_id, asyncio.Lock())
        async with lock:
            self._require_signed_in(registration_id)
            cached = self._catalogs.get(registration_id)
            if cached and not refresh and time.monotonic() - cached[0] < 300:
                return copy.deepcopy(cached[1])
            token = await self.bearer(registration_id)
            headers = {
                "authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
                "user-agent": "claude-cli/2.1.280",
                "x-app": "cli",
            }
            result: dict[str, dict] = {}
            cursor = None
            seen: set[str] = set()
            try:
                for _ in range(20):
                    params = {"limit": "100"}
                    if cursor:
                        params["after_id"] = cursor
                    response = await self.client.get(
                        "https://api.anthropic.com/v1/models", headers=headers, params=params
                    )
                    if response.status_code in (401, 403):
                        raise ClaudeSessionInvalid(
                            "Claude refused this connection. Connect again in settings."
                        )
                    if response.is_error:
                        raise RuntimeError(
                            "Claude's model catalog is unavailable. Try again shortly."
                        )
                    page = response.json()
                    if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                        raise ValueError("Invalid catalog")
                    for row in page["data"]:
                        if not isinstance(row, dict) or row.get("lifecycle") == "retired":
                            continue
                        slug = row.get("id")
                        if not isinstance(slug, str) or not slug.startswith("claude-"):
                            continue
                        capacity = row.get("max_input_tokens")
                        output = row.get("max_tokens")
                        capabilities = row.get("capabilities")
                        effort = (
                            capabilities.get("effort") if isinstance(capabilities, dict) else None
                        )
                        effort_metadata = None
                        if isinstance(effort, dict) and type(effort.get("supported")) is bool:
                            effort_metadata = {"supported": effort["supported"]}
                            for level in ("low", "medium", "high", "xhigh", "max"):
                                value = effort.get(level)
                                if isinstance(value, dict) and type(value.get("supported")) is bool:
                                    effort_metadata[level] = value["supported"]
                        result[slug] = {
                            "slug": slug,
                            "reasoning_effort": effort_metadata,
                            "display_name": row.get("display_name")
                            if isinstance(row.get("display_name"), str)
                            else slug,
                            "context_window": capacity
                            if type(capacity) is int and 1024 <= capacity <= 10_000_000
                            else None,
                            "max_tokens": output if type(output) is int and output > 0 else None,
                        }
                    if not page.get("has_more"):
                        break
                    cursor = page.get("last_id")
                    if not isinstance(cursor, str) or not cursor or cursor in seen:
                        raise ValueError("Invalid catalog cursor")
                    seen.add(cursor)
                else:
                    raise ValueError("Catalog too large")
            except (httpx.HTTPError, ValueError, TypeError):
                # Never expose provider bodies or credential-bearing request objects.
                raise RuntimeError(
                    "Claude's model catalog could not be loaded. Try again shortly."
                ) from None
            self._require_signed_in(registration_id)
            models = list(result.values())
            self._catalogs[registration_id] = (time.monotonic(), models)
            return copy.deepcopy(models)

    async def plan_model(self, registration_id: str, model: str) -> dict | None:
        return next((m for m in await self.models(registration_id) if m["slug"] == model), None)

    async def context_capacity(self, registration_id: str, model: str) -> int | None:
        return next(
            (m["context_window"] for m in await self.models(registration_id) if m["slug"] == model),
            None,
        )

    async def sign_out(self, registration_id: str) -> None:
        task = asyncio.create_task(self._sign_out_locked(registration_id))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        await asyncio.shield(task)

    async def _sign_out_locked(self, registration_id: str) -> None:
        async with exclusive_lock(self.path(registration_id).with_suffix(".lock")):
            record = self.read(registration_id)
            record.update(access_token=None, refresh_token=None, expires_at=0)
            write_private_json(self.path(registration_id), record)
            self._catalogs.pop(registration_id, None)

    async def aclose(self) -> None:
        for attempt_id in list(self.attempts):
            await self.cancel(attempt_id)
        await asyncio.gather(*self._refreshes.values(), return_exceptions=True)
        await asyncio.gather(
            *(a.server.wait_closed() for a in self.attempts.values() if a.server is not None)
        )
        await self.client.aclose()


_service: ClaudeAuthService | None = None


def claude_auth_service() -> ClaudeAuthService:
    global _service
    if _service is None:
        _service = ClaudeAuthService()
    return _service
