"""The process-wide ChatGPT sign-in service: store, OIDC client, attempts, bearers.

One per backend process. Bearers are cached per registration so every transport in
the process (the live one, a validate ping, a hot-swap candidate) shares one
in-process refresh lock; the file lock in the store covers other processes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from agentd.chatgpt_auth.oauth import AttemptManager
from agentd.chatgpt_auth.oidc import API_RESOURCE, DEFAULT_ISSUER, OidcClient
from agentd.chatgpt_auth.session import ChatGPTPlanBearer, SignOutResult, sign_out
from agentd.chatgpt_auth.store import CredentialStore
from agentd.providers.plan_access import PlanSessionInvalid, classify_plan_error

_HTTP_TIMEOUT = httpx.Timeout(30.0)


@dataclass(frozen=True)
class CatalogModel:
    slug: str
    display_name: str


class ModelCatalogError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ChatGPTAuthService:
    def __init__(
        self,
        *,
        store: CredentialStore | None = None,
        http: httpx.AsyncClient | None = None,
        issuer: str = DEFAULT_ISSUER,
        api_base: str = API_RESOURCE,
    ) -> None:
        self.store = store or CredentialStore()
        # No redirects: a token request or model listing that redirects is not one we
        # want to follow with credentials attached.
        self._http = http or httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False)
        self._api_base = api_base.rstrip("/")
        self.oidc = OidcClient(self._http, issuer)
        self.attempts = AttemptManager(self.store, self.oidc)
        self._bearers: dict[str, ChatGPTPlanBearer] = {}

    def bearer(self, registration_id: str) -> ChatGPTPlanBearer:
        bearer = self._bearers.get(registration_id)
        if bearer is None:
            self.store.require(registration_id)
            bearer = self._bearers[registration_id] = ChatGPTPlanBearer(
                registration_id, self.store, self.oidc)
        return bearer

    async def sign_out(self, registration_id: str) -> SignOutResult:
        await asyncio.to_thread(self.store.require, registration_id)
        return await sign_out(registration_id, self.store, self.oidc)

    async def list_models(self, registration_id: str) -> list[CatalogModel]:
        """The account's model catalog, in the server's order: `visibility == "list"`
        entries only. Show `display_name`, send `slug`.
        https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
        """
        bearer = self.bearer(registration_id)
        for retried in (False, True):
            token = await bearer.bearer()
            resp = await self._http.get(f"{self._api_base}/models",
                                        headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 401 and not retried and await bearer.on_unauthorized():
                continue
            break
        if resp.status_code != 200:
            raise self._catalog_error(resp)
        models = resp.json().get("models", [])
        return [
            CatalogModel(slug=str(m["slug"]), display_name=str(m.get("display_name") or m["slug"]))
            for m in models
            if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug")
        ]

    async def aclose(self) -> None:
        await self.attempts.aclose()
        await self._http.aclose()

    @staticmethod
    def _catalog_error(resp: httpx.Response) -> Exception:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            stopped = classify_plan_error(
                error.get("code"), str(error.get("message", "")), status=resp.status_code,
                request_id=resp.headers.get("x-request-id"))
            if stopped is not None:
                return stopped
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif detail:
            message = str(detail)  # direct-route admission bodies are {"detail": …}
        else:
            message = resp.text[:300]
        if resp.status_code == 401:
            return PlanSessionInvalid(message or "ChatGPT sign-in was not accepted",
                                      status=401, request_id=resp.headers.get("x-request-id"))
        return ModelCatalogError(resp.status_code, message or f"HTTP {resp.status_code}")


_SERVICE: ChatGPTAuthService | None = None


def chatgpt_auth_service() -> ChatGPTAuthService:
    """The process's service, created on first use (no network until then)."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ChatGPTAuthService()
    return _SERVICE
