"""Settings › Sandbox: status, default and remembered hosts, extra writable folders.

The workspace is always the backend's own (the runtime's), never one named in a request.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentd.sandbox.hosts import DEFAULT_HOSTS
from agentd.sandbox.runtime import SandboxRuntime
from agentd.sandbox.store import WorkspaceSandboxStore


class HostsRequest(BaseModel):
    hosts: list[str]


class FoldersRequest(BaseModel):
    folders: list[str]


def build_sandbox_router(runtime: SandboxRuntime, store: WorkspaceSandboxStore) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["sandbox"])

    def _view() -> dict[str, object]:
        return {"status": runtime.status().model_dump(), "default_hosts": list(DEFAULT_HOSTS),
                "hosts": store.hosts(), "folders": [str(p) for p in store.folders()]}

    @router.get("/sandbox")
    async def get_sandbox() -> dict[str, object]:
        return _view()

    @router.put("/sandbox/hosts")
    async def put_hosts(request: HostsRequest) -> dict[str, object]:
        try:
            store.set_hosts(request.hosts)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _view()

    @router.put("/sandbox/folders")
    async def put_folders(request: FoldersRequest) -> dict[str, object]:
        try:
            store.set_folders([Path(f) for f in request.folders])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _view()

    return router
