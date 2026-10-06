"""Sign in with ChatGPT routes (spec 2026-10-06 §5.5).

All behind the backend's bearer middleware. Responses never carry a token; the one
sensitive value that leaves is the authorize URL (it can hold an ID token hint), and it
goes only to the extension, which hands it to the system browser.

Model listing is a POST: listing may refresh the access token, which rewrites the
credential record, and GET routes here are read-only (`test_get_routes_read_only`).
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentd.chatgpt_auth.oauth import AttemptStatus, SignInInProgress
from agentd.chatgpt_auth.service import ChatGPTAuthService, ModelCatalogError
from agentd.chatgpt_auth.store import UnknownRegistration, summarize
from agentd.providers.openai_compatible_transport import TransientTransportError
from agentd.providers.plan_access import ProviderAccessStopped


class AttemptBody(BaseModel):
    registration_id: str | None = None
    reconsent: bool = False


def _attempt_payload(status: AttemptStatus) -> dict[str, object]:
    return {
        "attempt_id": status.attempt_id,
        "state": status.state,
        "registration_id": status.registration_id,
        "plan_enabled": status.plan_enabled,
        "first_plan_sign_in": status.first_plan_sign_in,
        "reason": status.reason,
        "message": status.message,
    }


def _access_detail(exc: ProviderAccessStopped) -> dict[str, object]:
    return {"kind": exc.kind, "message": str(exc), "code": exc.code,
            "request_id": exc.request_id}


def build_chatgpt_auth_router(service: Callable[[], ChatGPTAuthService]) -> APIRouter:
    router = APIRouter(prefix="/v1/auth/chatgpt", tags=["chatgpt-auth"])

    @router.post("/attempts")
    async def start_attempt(body: AttemptBody) -> dict[str, object]:
        try:
            status, url = await service().attempts.start(
                registration_id=body.registration_id, reconsent=body.reconsent)
        except SignInInProgress as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except UnknownRegistration as exc:
            raise HTTPException(status_code=404, detail="unknown ChatGPT account") from exc
        return {**_attempt_payload(status), "authorize_url": url}

    @router.get("/attempts/{attempt_id}")
    async def get_attempt(attempt_id: str) -> dict[str, object]:
        status = service().attempts.status(attempt_id)
        if status is None:
            raise HTTPException(status_code=404, detail="unknown sign-in attempt")
        return _attempt_payload(status)

    @router.post("/attempts/{attempt_id}/cancel")
    async def cancel_attempt(attempt_id: str) -> dict[str, object]:
        svc = service()
        if svc.attempts.status(attempt_id) is None:
            raise HTTPException(status_code=404, detail="unknown sign-in attempt")
        await svc.attempts.cancel(attempt_id)
        return _attempt_payload(svc.attempts.status(attempt_id))  # type: ignore[arg-type]

    @router.get("/registrations")
    async def list_registrations() -> dict[str, object]:
        return {"registrations": [summarize(r).model_dump() for r in service().store.list()]}

    @router.post("/registrations/{registration_id}/sign-out")
    async def sign_out(registration_id: str) -> dict[str, object]:
        try:
            result = await service().sign_out(registration_id)
        except UnknownRegistration as exc:
            raise HTTPException(status_code=404, detail="unknown ChatGPT account") from exc
        return {"remote_revoked": result.remote_revoked}

    @router.post("/registrations/{registration_id}/models")
    async def list_models(registration_id: str) -> dict[str, object]:
        try:
            models = await service().list_models(registration_id)
        except UnknownRegistration as exc:
            raise HTTPException(status_code=404, detail="unknown ChatGPT account") from exc
        except ProviderAccessStopped as exc:
            raise HTTPException(status_code=409, detail=_access_detail(exc)) from exc
        except (ModelCatalogError, TransientTransportError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"models": [{"slug": m.slug, "display_name": m.display_name,
                            "context_window": m.context_window} for m in models]}

    return router
