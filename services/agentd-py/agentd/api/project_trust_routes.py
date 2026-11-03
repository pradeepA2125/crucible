"""Trust for repository files that change Crucible's behavior (prompt-injection Phase 1, §4.1).

The workspace is always the backend's own, never one named in a request. Content goes out with
every invisible character shown as a ⟦U+XXXX⟧ marker, so the user reviews what the model reads.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentd.trust.invisible import render_visible
from agentd.trust.project_trust import (
    ALL_KINDS,
    ProjectTrust,
    TrustKind,
    TrustMismatch,
    TrustRefused,
)


class TrustRequest(BaseModel):
    kind: TrustKind
    sha256: str


class SavedRequest(BaseModel):
    kind: TrustKind
    before_sha256: str | None


def build_project_trust_router(
    trust: ProjectTrust, on_trusted: Callable[[TrustKind], Awaitable[None]],
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["project-trust"])

    def _file(kind: TrustKind) -> dict[str, object]:
        view = trust.view(kind)
        return {
            "kind": view.kind, "state": view.state, "sha256": view.sha256,
            "content": None if view.content is None else render_visible(view.content),
            "diff_base": None if view.diff_base is None else render_visible(view.diff_base),
        }

    @router.get("/project-trust")
    async def get_project_trust() -> dict[str, object]:
        return {"files": [_file(kind) for kind in ALL_KINDS]}

    @router.post("/project-trust")
    async def post_project_trust(request: TrustRequest) -> dict[str, bool]:
        try:
            trust.trust(request.kind, request.sha256)
        except (TrustMismatch, TrustRefused) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await on_trusted(request.kind)
        return {"ok": True}

    @router.post("/project-trust/saved")
    async def post_project_file_saved(request: SavedRequest) -> dict[str, bool]:
        trusted = trust.record_own_write(request.kind, request.before_sha256)
        if trusted:
            await on_trusted(request.kind)
        return {"trusted": trusted}

    return router
