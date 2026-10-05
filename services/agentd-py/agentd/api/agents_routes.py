"""Settings › Agents routes (spec §10.1).

The workspace is the backend's own (CRUCIBLE_WORKSPACE_PATH, the one the controller's
catalog reads) — never a path from the request, since these routes write files. Trust is
keyed by the path string the catalog loader produces, so a recorded hash is found again.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentd.chat.controller_factory import is_subagents_enabled
from agentd.subagents.agent_files import (
    CONTENT_MAX_BYTES,
    NAME_RE,
    NATIVE_TOOL_NAMES,
    AgentCatalogLoader,
    CatalogEntry,
    CatalogReport,
    trust_clamped_permission,
)
from agentd.subagents.agent_writer import (
    AgentConflictError,
    AgentFields,
    AgentInputError,
    AgentNotFoundError,
    delete_agent_file,
    render_agent_markdown,
    validate_fields,
    write_agent_file,
)
from agentd.subagents.permissions import CHILD_EXCLUDED_TOOLS
from agentd.subagents.trust import TrustStore


class AgentBody(BaseModel):
    description: str
    persona: str = ""
    tools: list[str] | None = None
    disallowed_tools: list[str] = []
    permission: str = "default"
    model: str = "inherit"
    max_turns: int | None = None
    skills: list[str] = []
    rename_from: str | None = None


class TrustBody(BaseModel):
    path: str
    sha256: str


class UntrustBody(BaseModel):
    path: str


def agent_json(entry: CatalogEntry) -> dict[str, object]:
    d = entry.definition
    return {
        "name": d.name,
        "description": d.description,
        "tools": sorted(d.tools) if d.tools is not None else None,
        "disallowed_tools": sorted(d.disallowed_tools),
        "permission": trust_clamped_permission(d),
        "declared_permission": d.permission,
        "model": d.model,
        "max_turns": d.max_turns,
        "skills": list(d.skills),
        "source": entry.source_kind,
        "path": entry.path,
        "sha256": d.content_sha256 or None,
        "trust": d.trust,
        "active": entry.active,
        "warnings": list(d.warnings),
        "shadowed_by": entry.shadowed_by,
        "persona": d.persona,
        "content": entry.content,
    }


def build_agents_router(
    *,
    workspace: str,
    trust_store: TrustStore | None = None,
    user_agents_dir: Path | None = None,
    mcp_server_names: Callable[[], Iterable[str]] = lambda: (),
    enabled: Callable[[], bool] = is_subagents_enabled,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["agents"])
    ws = Path(workspace)
    trust = trust_store or TrustStore()
    loader = AgentCatalogLoader(ws, user_agents_dir=user_agents_dir, trust_store=trust)
    agents_root = ws / ".crucible" / "agents"

    def _require_enabled() -> None:
        if not enabled():
            raise HTTPException(status_code=409, detail="sub-agents are disabled")

    def _crucible_paths(report: CatalogReport, name: str) -> list[Path]:
        return [Path(e.path) for e in report.entries
                if e.definition.name == name and e.source_kind == "crucible" and e.path]

    def _refuse_nested(report: CatalogReport, name: str) -> None:
        for path in _crucible_paths(report, name):
            if path.parent != agents_root:
                raise HTTPException(
                    status_code=409,
                    detail=f"{name!r} is defined by {path}; edit or delete that file instead")

    @router.get("/agents")
    async def list_agents() -> dict[str, object]:
        if not enabled():
            return {"agents": [], "skipped": [], "available_tools": []}
        report = loader.report()
        tools = sorted(NATIVE_TOOL_NAMES - CHILD_EXCLUDED_TOOLS)
        tools += [f"mcp__{name}__*" for name in sorted(set(mcp_server_names()))]
        return {
            "agents": [agent_json(e) for e in report.entries],
            "skipped": [{"path": s.path, "reason": s.reason} for s in report.skipped],
            "available_tools": tools,
        }

    # The /agents/trust routes are declared before /agents/{name} so "trust" never
    # reaches the name routes (and "trust" is also a reserved agent name).
    @router.post("/agents/trust")
    async def trust_agent(body: TrustBody) -> dict[str, bool]:
        _require_enabled()
        report = loader.report()
        entry = next((e for e in report.entries if e.path == body.path
                      and e.source_kind in ("crucible", "claude")), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="not a workspace agent file")
        path = Path(body.path)
        if path.is_symlink():
            raise HTTPException(status_code=409, detail="refusing to trust a symlink")
        raw = path.read_bytes()
        if len(raw) > CONTENT_MAX_BYTES:
            raise HTTPException(status_code=409, detail="file too large to review and trust")
        if hashlib.sha256(raw).hexdigest() != body.sha256:
            raise HTTPException(status_code=409,
                                detail="the file changed since you reviewed it")
        trust.trust(str(ws), body.path, body.sha256)
        return {"ok": True}

    @router.delete("/agents/trust")
    async def untrust_agent(body: UntrustBody) -> dict[str, bool]:
        _require_enabled()
        trust.revoke(str(ws), body.path)
        return {"ok": True}

    @router.put("/agents/{name}")
    async def save_agent(name: str, body: AgentBody) -> dict[str, object]:
        _require_enabled()
        fields = AgentFields(
            name=name, description=body.description, persona=body.persona,
            tools=tuple(body.tools) if body.tools is not None else None,
            disallowed_tools=tuple(body.disallowed_tools), permission=body.permission,
            model=body.model, max_turns=body.max_turns, skills=tuple(body.skills))
        try:
            validate_fields(fields)
        except AgentInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        report = loader.report()
        _refuse_nested(report, name)
        renaming = body.rename_from is not None and body.rename_from != name
        if renaming:
            assert body.rename_from is not None
            if not NAME_RE.match(body.rename_from):
                raise HTTPException(status_code=400, detail="rename_from is not a valid name")
            if _crucible_paths(report, name):
                raise HTTPException(status_code=409,
                                    detail=f"an agent named {name!r} already exists")
            _refuse_nested(report, body.rename_from)
        try:
            target = write_agent_file(ws, name, render_agent_markdown(fields))
        except AgentConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        trust.trust(str(ws), str(target), hashlib.sha256(target.read_bytes()).hexdigest())
        if renaming:
            assert body.rename_from is not None
            try:
                old = delete_agent_file(ws, body.rename_from)
                trust.revoke(str(ws), str(old))
            except AgentNotFoundError:
                pass  # renaming a definition that was never saved here
            except AgentConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        saved = next(e for e in loader.report().entries if e.path == str(target))
        return {"agent": agent_json(saved)}

    @router.delete("/agents/{name}")
    async def delete_agent(name: str) -> dict[str, bool]:
        _require_enabled()
        report = loader.report()
        _refuse_nested(report, name)
        if not _crucible_paths(report, name):
            if any(e.definition.name == name for e in report.entries):
                raise HTTPException(status_code=409, detail=(
                    f"{name!r} is not a .crucible/agents file; built-ins and .claude files "
                    "are read-only here — duplicate it to .crucible to edit"))
            raise HTTPException(status_code=404, detail=f"no agent named {name!r}")
        try:
            path = delete_agent_file(ws, name)
        except AgentConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        trust.revoke(str(ws), str(path))
        return {"ok": True}

    return router
