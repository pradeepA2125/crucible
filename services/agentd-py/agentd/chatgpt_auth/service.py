"""The process-wide ChatGPT sign-in service: store, OIDC client, attempts, bearers.

One per backend process. Bearers are cached per registration so every transport in
the process (the live one, a validate ping, a hot-swap candidate) shares one
in-process refresh lock; the file lock in the store covers other processes.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
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
    # The model's usable window, as the catalog reports it (live: 272000 for gpt-5.6).
    context_window: int | None = None
    input_modalities: tuple[str, ...] | None = None


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
        capacity_ttl_sec: float = 60.0,
        capacity_timeout_sec: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store or CredentialStore()
        # No redirects: a token request or model listing that redirects is not one we
        # want to follow with credentials attached.
        self._http = http or httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False)
        self._api_base = api_base.rstrip("/")
        self.oidc = OidcClient(self._http, issuer)
        self.attempts = AttemptManager(self.store, self.oidc)
        self._bearers: dict[str, ChatGPTPlanBearer] = {}
        if not math.isfinite(capacity_ttl_sec) or capacity_ttl_sec <= 0:
            raise ValueError("capacity TTL must be positive and finite")
        if not math.isfinite(capacity_timeout_sec) or capacity_timeout_sec <= 0:
            raise ValueError("capacity timeout must be positive and finite")
        self._capacity_ttl = capacity_ttl_sec
        self._capacity_timeout = capacity_timeout_sec
        self._clock = clock
        self._capacities: dict[tuple[object, ...], tuple[float, int | None]] = {}
        self._capacity_tasks: dict[tuple[object, ...], asyncio.Task[int | None]] = {}
        self._plan_models: dict[tuple[object, ...], tuple[float, CatalogModel | None]] = {}
        self._plan_model_tasks: dict[tuple[object, ...], asyncio.Task[CatalogModel | None]] = {}

    def context_identity(self, registration_id: str) -> tuple[object, ...]:
        """Private equality key, never suitable for an event or public response."""
        try:
            record = self.store.get(registration_id)
            return record.context_identity if record is not None else (registration_id, None)
        except Exception:
            return (registration_id, None)

    def subscribe(self, registration_id: str, callback: Callable[[], None]) -> Callable[[], None]:
        return self.store.subscribe(registration_id, callback)

    async def context_capacity(self, registration_id: str, model: str) -> int | None:
        """Best effort catalog-only resolution. Call from a background state task,
        never await on the inference path. No tokens or full catalogs are cached.
        """
        identity = self.context_identity(registration_id)
        key = (*identity, model)
        now = self._clock()
        self._capacities = {k: v for k, v in self._capacities.items() if v[0] > now}
        cached = self._capacities.get(key)
        if cached is not None:
            return cached[1]
        task = self._capacity_tasks.get(key)
        if task is None:
            task = asyncio.create_task(
                self._resolve_capacity(registration_id, model, identity, key))
            self._capacity_tasks[key] = task
        # Cancellation of one viewer must not cancel a coalesced lookup.
        return await asyncio.shield(task)

    async def _resolve_capacity(
        self, registration_id: str, model: str, identity: tuple[object, ...],
        key: tuple[object, ...],
    ) -> int | None:
        value = None
        try:
            async with asyncio.timeout(self._capacity_timeout):
                models = await self.list_models(registration_id)
                value = next((m.context_window for m in models if m.slug == model), None)
                value = _positive_int(value)
        except Exception:
            pass  # capacity is optional; transport/plan errors remain on the chat path
        finally:
            self._capacity_tasks.pop(key, None)
        if self.context_identity(registration_id) != identity:
            return None  # external-process reauthorization/signout won the race
        self._capacities[key] = (self._clock() + self._capacity_ttl, value)
        return value

    async def plan_model(self, registration_id: str, model: str) -> CatalogModel | None:
        """Exact visible catalog entry; failures are distinct from an unlisted model.

        Identity keys remain private. Cache only selected metadata, never credentials.
        """
        identity = self.context_identity(registration_id)
        key = (*identity, model)
        now = self._clock()
        self._plan_models = {k: v for k, v in self._plan_models.items() if v[0] > now}
        cached = self._plan_models.get(key)
        if cached is not None:
            return cached[1]
        task = self._plan_model_tasks.get(key)
        if task is None:
            if len(self._plan_model_tasks) >= 32:
                raise ModelCatalogError(503, "ChatGPT model catalog is busy; retry shortly")
            task = asyncio.create_task(
                self._resolve_plan_model(registration_id, model, identity, key))
            self._plan_model_tasks[key] = task
            # Retrieve errors even when every coalesced caller was cancelled.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def _resolve_plan_model(
        self, registration_id: str, model: str, identity: tuple[object, ...],
        key: tuple[object, ...],
    ) -> CatalogModel | None:
        try:
            try:
                async with asyncio.timeout(self._capacity_timeout):
                    models = await self.list_models(registration_id)
            except (ModelCatalogError, PlanSessionInvalid):
                raise
            except TimeoutError:
                raise ModelCatalogError(
                    504, "ChatGPT model catalog timed out; retry shortly") from None
            except Exception:
                raise ModelCatalogError(
                    503, "ChatGPT model catalog unavailable; retry or sign in again") from None
            if self.context_identity(registration_id) != identity:
                raise ModelCatalogError(409, "ChatGPT account changed during model lookup; retry")
            value = next((entry for entry in models if entry.slug == model), None)
            if len(self._plan_models) >= 128:
                self._plan_models.pop(next(iter(self._plan_models)))
            self._plan_models[key] = (self._clock() + self._capacity_ttl, value)
            return value
        finally:
            self._plan_model_tasks.pop(key, None)

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
        try:
            body = resp.json()
        except ValueError:
            raise ModelCatalogError(
                502, "ChatGPT model catalog returned invalid JSON; retry") from None
        if not isinstance(body, dict) or not isinstance(body.get("models"), list):
            raise ModelCatalogError(502, "ChatGPT model catalog returned invalid models; retry")
        models = body["models"]
        return [
            CatalogModel(slug=str(m["slug"]), display_name=str(m.get("display_name") or m["slug"]),
                         context_window=_positive_int(m.get("context_window")),
                         input_modalities=_input_modalities(m))
            for m in models
            if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug")
        ]

    async def aclose(self) -> None:
        tasks = (*self._capacity_tasks.values(), *self._plan_model_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._capacity_tasks.clear()
        self._plan_model_tasks.clear()
        self._plan_models.clear()
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


def _input_modalities(model: dict[str, object]) -> tuple[str, ...] | None:
    source = model
    if "input_modalities" not in source:
        architecture = model.get("architecture")
        if not isinstance(architecture, dict) or "input_modalities" not in architecture:
            return None
        source = architecture
    value = source["input_modalities"]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return ()  # Present but malformed is never optimistic absence.
    return tuple(value)


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


_SERVICE: ChatGPTAuthService | None = None


def chatgpt_auth_service() -> ChatGPTAuthService:
    """The process's service, created on first use (no network until then)."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ChatGPTAuthService()
    return _SERVICE
