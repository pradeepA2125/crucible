"""Authenticated local API: browser URL and public status only, never OAuth tokens."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from agentd.claude_auth.service import ClaudeAuthService, ClaudeSessionInvalid


def build_claude_auth_router(service: Callable[[], ClaudeAuthService]) -> APIRouter:
    router = APIRouter(prefix="/v1/auth/claude", tags=["claude-auth"])

    @router.post("/attempts")
    async def start() -> dict:
        try:
            status, url = await service().start()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {**status, "authorize_url": url}

    @router.get("/attempts/{attempt_id}")
    async def status(attempt_id: str) -> dict:
        try:
            return service().status(attempt_id)
        except KeyError:
            raise HTTPException(404, "Unknown Claude sign-in attempt") from None

    @router.post("/attempts/{attempt_id}/cancel")
    async def cancel(attempt_id: str) -> dict:
        try:
            return await service().cancel(attempt_id)
        except KeyError:
            raise HTTPException(404, "Unknown Claude sign-in attempt") from None

    @router.get("/registrations")
    async def accounts() -> dict:
        return {"registrations": service().accounts()}

    @router.post("/registrations/{registration_id}/models")
    async def models(registration_id: str, refresh: bool = False) -> dict:
        try:
            return {"models": await service().models(registration_id, refresh=refresh)}
        except KeyError:
            raise HTTPException(404, "Unknown Claude connection") from None
        except ClaudeSessionInvalid as exc:
            raise HTTPException(401, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None

    @router.post("/registrations/{registration_id}/sign-out")
    async def sign_out(registration_id: str) -> dict:
        try:
            await service().sign_out(registration_id)
        except KeyError:
            raise HTTPException(404, "Unknown Claude connection") from None
        # Local disconnect only: no documented revocation endpoint is assumed.
        return {"remote_revoked": False}

    return router
