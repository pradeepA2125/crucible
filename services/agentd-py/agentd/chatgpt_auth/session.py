"""A signed-in ChatGPT registration as a bearer credential: refresh and sign-out.

Access tokens last an hour; refresh tokens last 30 days and rotate on every refresh.
Several backends (one per workspace) share one registration, so a refresh runs under
the registration's cross-process lock and re-reads the record first — another
backend may have just refreshed, and reusing its spent refresh token would fail with
`refresh_token_reused`.
https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from agentd.chatgpt_auth.oidc import OidcClient, TokenEndpointError
from agentd.chatgpt_auth.store import CredentialRecord, CredentialStore
from agentd.providers.openai_compatible_transport import TransientTransportError
from agentd.providers.plan_access import (
    PlanAccessMisconfigured,
    PlanSessionInvalid,
    PlanUsageDisabled,
)

logger = logging.getLogger(__name__)

REFRESH_MARGIN_SEC = 300.0
_REVOKE_ATTEMPTS = 3
_SIGN_IN_AGAIN = "Your ChatGPT sign-in has ended. Sign in again in Crucible settings."


class ChatGPTPlanBearer:
    """`BearerSource` for one registration (see `providers/openai_transport.py`)."""

    def __init__(
        self,
        registration_id: str,
        store: CredentialStore,
        oidc: OidcClient,
        *,
        clock: Callable[[], float] = time.time,
        refresh_margin_sec: float = REFRESH_MARGIN_SEC,
    ) -> None:
        self._registration_id = registration_id
        self._store = store
        self._oidc = oidc
        self._clock = clock
        self._margin = refresh_margin_sec
        self._lock = asyncio.Lock()  # in-process; the file lock covers other processes
        self._last_token: str | None = None

    @property
    def registration_id(self) -> str:
        return self._registration_id

    async def bearer(self) -> str:
        record = self._usable(await self._read())
        if not self._wants_refresh(record):
            self._last_token = record.access_token
            assert record.access_token is not None
            return record.access_token
        async with self._lock:
            token = await self._refresh(force=False)
        self._last_token = token
        return token

    async def on_unauthorized(self) -> bool:
        """A 401 on a token we believed valid: refresh now, unless someone already has."""
        rejected = self._last_token
        async with self._lock:
            try:
                token = await self._refresh(force=True, rejected=rejected)
            except PlanSessionInvalid:
                return False
        self._last_token = token
        return token != rejected

    async def _refresh(self, *, force: bool, rejected: str | None = None) -> str:
        async with self._store.locked(self._registration_id):
            record = self._usable(await self._read())
            assert record.access_token is not None
            if force and record.access_token != rejected:
                return record.access_token  # another backend refreshed meanwhile
            if not force and not self._wants_refresh(record):
                return record.access_token
            if not force and not self._expired(record) and self._too_early(record):
                return record.access_token
            if record.refresh_token is None:
                await self._mark_signed_out(record)
                raise PlanSessionInvalid(_SIGN_IN_AGAIN, status=401)
            try:
                tokens = await self._oidc.refresh(
                    client_id=record.client_id, refresh_token=record.refresh_token)
            except TokenEndpointError as exc:
                if exc.is_unusable_refresh:
                    logger.warning("ChatGPT refresh token unusable (%s); sign-in needed",
                                   exc.code)
                    await self._mark_signed_out(record)
                    raise PlanSessionInvalid(_SIGN_IN_AGAIN, code=exc.code, status=401) from exc
                if exc.code == "invalid_client":
                    raise PlanAccessMisconfigured(
                        "ChatGPT rejected Crucible's client registration", code=exc.code,
                        status=exc.status) from exc
                return self._keep_or_raise(record, exc)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                return self._keep_or_raise(record, exc)
            updated = record.model_copy(update={
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token or record.refresh_token,
                "id_token": await self._verified_id_token(tokens.id_token, record),
                "expires_at": tokens.expires_at,
                "earliest_refresh_at": tokens.earliest_refresh_at,
                "scopes": tokens.scopes or record.scopes,
                "saved_at": tokens.received_at,
            })
            await asyncio.to_thread(self._store.save, updated)
            return tokens.access_token

    def _keep_or_raise(self, record: CredentialRecord, exc: Exception) -> str:
        """Network or server trouble never discards credentials. Keep using the current
        token while it is still valid; otherwise report a retryable failure."""
        logger.warning("ChatGPT token refresh failed transiently: %s", exc.__class__.__name__)
        if not self._expired(record) and record.access_token is not None:
            return record.access_token
        raise TransientTransportError(f"ChatGPT token refresh failed: {exc}") from exc

    async def _verified_id_token(
        self, id_token: str | None, record: CredentialRecord
    ) -> str | None:
        """Keep a refreshed ID token (the next sign-in's hint) only if it verifies as
        the same account; otherwise keep the one we have."""
        if not id_token:
            return record.id_token
        try:
            claims = await self._oidc.validate_id_token(
                id_token, client_id=record.client_id, nonce=None)
        except Exception:  # noqa: BLE001 — a hint is optional; the refresh still counts
            logger.warning("refreshed ChatGPT ID token did not verify; keeping the old one")
            return record.id_token
        return id_token if claims.get("sub") == record.subject else record.id_token

    async def _mark_signed_out(self, record: CredentialRecord) -> None:
        await asyncio.to_thread(self._store.save, record.model_copy(update={
            "access_token": None, "refresh_token": None, "needs_sign_in": True}))

    async def _read(self) -> CredentialRecord | None:
        return await asyncio.to_thread(self._store.get, self._registration_id)

    def _usable(self, record: CredentialRecord | None) -> CredentialRecord:
        if record is None or not record.signed_in:
            raise PlanSessionInvalid(_SIGN_IN_AGAIN, status=401)
        if not record.plan_enabled:
            raise PlanUsageDisabled(
                "ChatGPT plan usage isn't enabled for this account. Enable it in Crucible "
                "settings, or use an API key instead.")
        return record

    def _wants_refresh(self, record: CredentialRecord) -> bool:
        return record.expires_at is not None and record.expires_at - self._clock() < self._margin

    def _expired(self, record: CredentialRecord) -> bool:
        return record.expires_at is not None and record.expires_at <= self._clock()

    def _too_early(self, record: CredentialRecord) -> bool:
        return record.earliest_refresh_at is not None and self._clock() < record.earliest_refresh_at


@dataclass(frozen=True)
class SignOutResult:
    remote_revoked: bool


async def sign_out(
    registration_id: str,
    store: CredentialStore,
    oidc: OidcClient,
    *,
    retry_delay_sec: float = 1.0,
) -> SignOutResult:
    """Revoke the renewable session, then clear tokens. The registration (issued client
    id, account, label) and the host id stay, so signing in again reuses them."""
    async with store.locked(registration_id):
        record = store.require(registration_id)
        revoked = record.refresh_token is None
        if record.refresh_token is not None:
            for attempt in range(_REVOKE_ATTEMPTS):
                try:
                    await oidc.revoke(client_id=record.client_id,
                                      refresh_token=record.refresh_token)
                    revoked = True
                    break
                except TokenEndpointError as exc:
                    if not exc.is_transient:
                        break
                except httpx.TransportError:
                    pass
                if attempt < _REVOKE_ATTEMPTS - 1:
                    await asyncio.sleep(retry_delay_sec * (2 ** attempt))
        await asyncio.to_thread(store.save, record.model_copy(update={
            "access_token": None, "refresh_token": None, "id_token": None,
            "expires_at": None, "earliest_refresh_at": None, "needs_sign_in": False}))
    return SignOutResult(remote_revoked=revoked)
