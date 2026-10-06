"""An in-process stand-in for OpenAI's authorization server (auth.openai.com).

Real JWT signing (RSA, published as JWKS), PKCE checking, issued-client binding and
rotating refresh tokens — enough that the sign-in and refresh code can't pass by
accident. Served through `httpx.MockTransport`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://auth.example.test"
FULL_SCOPE = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class _Grant:
    client_id: str
    challenge: str
    redirect_uri: str
    nonce: str
    subject: str
    email: str
    scope: str


@dataclass
class FakeAuthServer:
    key: Any = field(default_factory=lambda: rsa.generate_private_key(
        public_exponent=65537, key_size=2048))
    kid: str = "kid-1"
    codes: dict[str, _Grant] = field(default_factory=dict)
    refresh_tokens: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    revocations: list[dict[str, str]] = field(default_factory=list)
    refresh_error: tuple[int, str] | None = None
    revoke_status: int = 200
    issued_clients: int = 0
    expires_in: int = 3600
    access_tokens: set[str] = field(default_factory=set)
    models: list[dict[str, Any]] = field(default_factory=lambda: [
        {"slug": "gpt-plan-large", "display_name": "GPT Plan Large", "visibility": "list",
         "context_window": 272000},
        {"slug": "gpt-internal", "display_name": "Internal", "visibility": "hide"},
        {"slug": "gpt-plan-small", "display_name": "GPT Plan Small", "visibility": "list"},
    ])

    # ------------------------------------------------------------ test helpers

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def authorize(
        self, authorize_url: str, *, subject: str = "user-1", email: str = "a@example.com",
        scope: str = FULL_SCOPE, nonce: str | None = None, issue_client: bool = True,
    ) -> dict[str, str]:
        """What the browser would do: consent, then the callback query parameters."""
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
        client_id = params["client_id"]
        callback: dict[str, str] = {"state": params["state"], "scope": scope}
        if client_id == "dynamic_agent_client":
            if issue_client:
                self.issued_clients += 1
                client_id = f"oaiapp_{self.issued_clients:04d}"
                callback["client_id"] = client_id
        code = secrets.token_urlsafe(16)
        self.codes[code] = _Grant(
            client_id=client_id, challenge=params["code_challenge"],
            redirect_uri=params["redirect_uri"], nonce=nonce or params["nonce"],
            subject=subject, email=email, scope=scope)
        callback["code"] = code
        return callback

    def id_token(self, *, client_id: str, subject: str, email: str, nonce: str | None,
                 key: Any = None) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {"iss": ISSUER, "aud": client_id, "sub": subject,
                                  "email": email, "iat": now, "exp": now + 3600}
        if nonce is not None:
            claims["nonce"] = nonce
        return jwt.encode(claims, key or self.key, algorithm="RS256",
                          headers={"kid": self.kid})

    # ------------------------------------------------------------ HTTP

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/api/accounts/authorize",
                "token_endpoint": f"{ISSUER}/api/accounts/oauth/token",
                "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                "revocation_endpoint": f"{ISSUER}/api/accounts/oauth/revoke",
            })
        if path == "/.well-known/jwks.json":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
            return httpx.Response(200, json={"keys": [jwk]})
        if path == "/v1/models":
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in self.access_tokens:
                return httpx.Response(401, json={"detail": "invalid token"})
            return httpx.Response(200, json={"models": self.models})
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        if path == "/api/accounts/oauth/token":
            self.token_requests.append(form)
            if form.get("grant_type") == "authorization_code":
                return self._exchange(form)
            return self._refresh(form)
        if path == "/api/accounts/oauth/revoke":
            self.revocations.append(form)
            if self.revoke_status == 200:
                self.refresh_tokens.pop(form.get("token", ""), None)
            return httpx.Response(self.revoke_status)
        return httpx.Response(404)

    def _exchange(self, form: dict[str, str]) -> httpx.Response:
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None or form.get("client_id") != grant.client_id \
                or form.get("redirect_uri") != grant.redirect_uri \
                or form.get("resource") != "https://api.openai.com/v1" \
                or _b64url(hashlib.sha256(form.get("code_verifier", "").encode()).digest()) \
                != grant.challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json=self._tokens(grant.client_id, grant.subject,
                                                     grant.email, grant.nonce, grant.scope))

    def _refresh(self, form: dict[str, str]) -> httpx.Response:
        if self.refresh_error is not None:
            status, code = self.refresh_error
            return httpx.Response(status, json={"error": code})
        bound = self.refresh_tokens.pop(form.get("refresh_token", ""), None)
        if bound is None:
            return httpx.Response(400, json={"error": "refresh_token_reused"})
        client_id, subject, scope = bound
        if form.get("client_id") != client_id or "scope" in form:
            return httpx.Response(400, json={"error": "invalid_client"})
        return httpx.Response(200, json=self._tokens(client_id, subject, "a@example.com",
                                                     None, scope))

    def _tokens(self, client_id: str, subject: str, email: str, nonce: str | None,
                scope: str) -> dict[str, Any]:
        refresh = f"rt_{secrets.token_urlsafe(12)}"
        self.refresh_tokens[refresh] = (client_id, subject, scope)
        access = f"at_{secrets.token_urlsafe(12)}"
        self.access_tokens.add(access)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "id_token": self.id_token(client_id=client_id, subject=subject, email=email,
                                      nonce=nonce),
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "scope": scope,
        }
