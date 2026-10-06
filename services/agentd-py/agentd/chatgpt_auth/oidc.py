"""OpenAI's OAuth/OIDC endpoints: discovery, code exchange, refresh, revocation, ID tokens.

Public client: no secret anywhere. Endpoints come from discovery
(`https://auth.openai.com/.well-known/openid-configuration`) rather than being
hard-coded, and ID tokens are verified against the published JWKS: signature, issuer,
audience (the issued client id), expiry and nonce, with a small clock skew.
https://developers.openai.com/siwc/token-sharing-open-source/sign-in
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

API_RESOURCE = "https://api.openai.com/v1"
DEFAULT_ISSUER = "https://auth.openai.com"
_ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]
_CLOCK_SKEW_SEC = 5
_DISCOVERY_TTL_SEC = 3600.0

# Refresh outcomes that mean the refresh token can never work again: clear it and ask
# the user to sign in. Everything else (network, 5xx) keeps the credentials.
UNUSABLE_REFRESH_CODES = frozenset({
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
})


class TokenEndpointError(RuntimeError):
    def __init__(self, status: int, code: str | None, description: str) -> None:
        super().__init__(f"{code or status}: {description}" if description else str(code))
        self.status = status
        self.code = code
        self.description = description

    @property
    def is_unusable_refresh(self) -> bool:
        return self.code in UNUSABLE_REFRESH_CODES

    @property
    def is_transient(self) -> bool:
        return self.status >= 500 or self.status == 429


class IdTokenInvalid(RuntimeError):
    pass


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    revocation_endpoint: str | None


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_in: float | None
    scopes: list[str]
    earliest_refresh_in: float | None
    received_at: float

    @property
    def expires_at(self) -> float | None:
        return self.received_at + self.expires_in if self.expires_in is not None else None

    @property
    def earliest_refresh_at(self) -> float | None:
        return (self.received_at + self.earliest_refresh_in
                if self.earliest_refresh_in is not None else None)


class OidcClient:
    def __init__(self, http: httpx.AsyncClient, issuer: str = DEFAULT_ISSUER) -> None:
        self._http = http
        self._issuer = issuer.rstrip("/")
        self._discovery: Discovery | None = None
        self._discovered_at = 0.0
        self._jwks: dict[str, Any] | None = None

    async def discovery(self) -> Discovery:
        if self._discovery is None or time.time() - self._discovered_at > _DISCOVERY_TTL_SEC:
            resp = await self._http.get(f"{self._issuer}/.well-known/openid-configuration")
            resp.raise_for_status()
            doc = resp.json()
            self._discovery = Discovery(
                issuer=doc["issuer"],
                authorization_endpoint=doc["authorization_endpoint"],
                token_endpoint=doc["token_endpoint"],
                jwks_uri=doc["jwks_uri"],
                revocation_endpoint=doc.get("revocation_endpoint"),
            )
            self._discovered_at = time.time()
        return self._discovery

    async def exchange_code(
        self, *, client_id: str, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenResponse:
        return await self._token_request({
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": code_verifier, "redirect_uri": redirect_uri,
            "resource": API_RESOURCE,
        })

    async def refresh(self, *, client_id: str, refresh_token: str) -> TokenResponse:
        # No `scope`: omitting it keeps the original grant.
        return await self._token_request({
            "grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": refresh_token, "resource": API_RESOURCE,
        })

    async def revoke(self, *, client_id: str, refresh_token: str) -> None:
        """Ends the renewable session. An empty 200 is success, even for a dead token."""
        endpoint = (await self.discovery()).revocation_endpoint
        if not endpoint:
            raise TokenEndpointError(0, "no_revocation_endpoint",
                                     "discovery lists no revocation_endpoint")
        resp = await self._http.post(endpoint, data={
            "token": refresh_token, "token_type_hint": "refresh_token", "client_id": client_id})
        if resp.status_code != 200:
            raise _endpoint_error(resp)

    async def validate_id_token(
        self, id_token: str, *, client_id: str, nonce: str | None
    ) -> dict[str, Any]:
        disco = await self.discovery()
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise IdTokenInvalid(f"malformed ID token: {exc}") from exc
        if header.get("alg") not in _ALLOWED_ALGORITHMS:
            raise IdTokenInvalid(f"ID token algorithm {header.get('alg')!r} is not allowed")
        key = await self._signing_key(header.get("kid"))
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token, key, algorithms=_ALLOWED_ALGORITHMS, audience=client_id,
                issuer=disco.issuer, leeway=_CLOCK_SKEW_SEC,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            raise IdTokenInvalid(f"ID token rejected: {exc}") from exc
        if nonce is not None and claims.get("nonce") != nonce:
            raise IdTokenInvalid("ID token nonce does not match this sign-in")
        return claims

    async def _signing_key(self, kid: str | None) -> Any:
        for refetch in (False, True):
            jwks = self._jwks
            if jwks is None or refetch:
                # An unfamiliar kid means OpenAI rotated keys: fetch the set again once.
                resp = await self._http.get((await self.discovery()).jwks_uri)
                resp.raise_for_status()
                jwks = self._jwks = resp.json()
            for entry in jwks.get("keys", []):
                if kid is None or entry.get("kid") == kid:
                    return jwt.PyJWK.from_dict(entry).key
        raise IdTokenInvalid(f"no published signing key matches kid {kid!r}")

    async def _token_request(self, form: dict[str, str]) -> TokenResponse:
        endpoint = (await self.discovery()).token_endpoint
        resp = await self._http.post(endpoint, data=form)
        if resp.status_code != 200:
            raise _endpoint_error(resp)
        body = resp.json()
        received_at = time.time()
        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            id_token=body.get("id_token"),
            expires_in=_number(body.get("expires_in")),
            scopes=sorted(str(body.get("scope", "")).split()),
            earliest_refresh_in=_relative_seconds(body.get("earliest_refresh_at"), received_at),
            received_at=received_at,
        )


def _endpoint_error(resp: httpx.Response) -> TokenEndpointError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    error = body.get("error")
    if isinstance(error, dict):  # some OpenAI endpoints nest {"error": {"code": …}}
        code, description = error.get("code"), str(error.get("message", ""))
    else:
        code = error or body.get("code")
        description = str(body.get("error_description") or body.get("detail") or "")
    return TokenEndpointError(resp.status_code, code, description)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _relative_seconds(value: Any, received_at: float) -> float | None:
    """`earliest_refresh_at` as seconds from now. The docs name the field but not its
    unit; a value that looks like a Unix time is converted, otherwise it is relative."""
    number = _number(value)
    if number is None:
        return None
    return max(0.0, number - received_at) if number > 1_000_000_000 else number
