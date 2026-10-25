"""Managed-owner-only per-file restore. Every write section runs without await."""

from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentd.chat.file_restore import FileRestore, RestoreConflict
from agentd.project_ownership import OWNER


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=0)
    anchorMessageId: str = Field(min_length=1, max_length=4096)
    createdAt: str = Field(min_length=1, max_length=128)


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["preview", "apply", "history", "undo"]
    process_id: str = Field(min_length=1, max_length=128)
    path: str | None = Field(default=None, max_length=4096)
    checkpoint: Identity | None = None
    supplying: Identity | None = None
    fingerprint: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    token: str | None = Field(default=None, max_length=128)
    receipt: str | None = Field(default=None, max_length=128)


def install_file_restore_routes(router, controller):
    def idle():
        if not OWNER.active:
            raise RestoreConflict("A managed project owner is required for file restore.")
        read = getattr(controller, "project_lifecycle", None)
        if not callable(read):
            raise RestoreConflict("Current reactive activity is required for file restore.")
        report = read(OWNER.root)
        if not report.get("supported") or not isinstance(report.get("threads"), list):
            raise RestoreConflict("Project activity is unavailable. Restore is disabled.")
        if any(
            t.get("turn_active")
            or t.get("agents_running")
            or t.get("sessions_running")
            or t.get("gates")
            or t.get("agents")
            or t.get("teams")
            or t.get("sessions")
            for t in report["threads"]
        ):
            raise RestoreConflict(
                "Finish all project turns, agents, teams, commands and decisions before restoring."
            )

    service = None

    @router.post("/chat/threads/{thread_id}/file-restore")
    async def file_restore(thread_id: str, body: RestoreRequest, request: Request):
        # Explicit capability check also protects alternate ASGI embeddings lacking middleware.
        if (
            not OWNER.active
            or not OWNER.owns(request.scope["headers"])
            or body.process_id != OWNER.process_id
        ):
            raise HTTPException(
                409, "Only the current controlling project session can restore files."
            )
        nonlocal service
        if service is None:
            rewind = getattr(controller, "_rewind", None)
            if rewind is None:
                raise HTTPException(409, "Retained file recovery is unavailable.")
            service = FileRestore(
                controller._store, rewind, OWNER.root, idle, lambda: OWNER.process_id
            )
        try:
            if body.operation == "history":
                return service.history(thread_id)
            if (
                body.operation == "preview"
                and body.path
                and body.checkpoint
                and body.supplying
                and body.fingerprint
            ):
                return service.prepare(
                    thread_id,
                    body.checkpoint.model_dump(),
                    body.supplying.model_dump(),
                    body.path,
                    body.fingerprint,
                )
            if body.operation == "undo" and body.receipt:
                return service.prepare_undo(thread_id, body.receipt)
            if body.operation == "apply" and body.token:
                return service.apply(thread_id, body.token)
            raise HTTPException(
                422, "Restore requires a complete preview, confirmation or recovery identity."
            )
        except (RestoreConflict, OSError) as exc:
            raise HTTPException(
                409,
                str(exc)
                if isinstance(exc, RestoreConflict)
                else "The project file is unavailable. Refresh before retrying.",
            ) from None
