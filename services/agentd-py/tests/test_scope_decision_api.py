"""Tests for POST /v1/tasks/{id}/scope-decision route."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import (
    ScopeExtensionRequest,
    TaskRecord,
    TaskStatus,
)
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.tools.loop import ScopeDecision
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _AlwaysPassValidator:
    async def run(self, workspace_path):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


def _make_app(orchestrator: AgentOrchestrator, store: InMemoryTaskStore, tmp_path: Path):
    app = FastAPI()
    app.include_router(
        build_router(
            store=store,
            orchestrator=orchestrator,
            workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        )
    )
    return app


async def _seed_paused_task(
    orch: AgentOrchestrator,
    store: InMemoryTaskStore,
    task_id: str,
    *,
    requested_files: list[str],
) -> tuple[TaskRecord, asyncio.Future[ScopeDecision]]:
    """Create a task in AWAITING_SCOPE_DECISION with a pending future + request."""
    task = TaskRecord(task_id=task_id, goal="g", workspace_path=".")
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "approval")
    task = transition(task, TaskStatus.PLANNED, "planned")
    task = transition(task, TaskStatus.EXECUTING, "executing")
    task = transition(task, TaskStatus.AWAITING_SCOPE_DECISION, "scope gate")
    task.execution_state.pending_scope_request = ScopeExtensionRequest(
        decision_id="dec-1", files=requested_files, reason="pytest convention", step_id="s1",
    )
    await store.create(task)
    future: asyncio.Future[ScopeDecision] = asyncio.get_event_loop().create_future()
    orch._pending_scope_decisions[task_id] = future
    return task, future


def _make_orch(tmp_path: Path) -> tuple[AgentOrchestrator, InMemoryTaskStore]:
    store = InMemoryTaskStore()
    orch = AgentOrchestrator(
        store=store,
        reasoning_engine=_NoopReasoning(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )
    return orch, store


@pytest.mark.asyncio
async def test_approve_resolves_pending_future(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    _, future = await _seed_paused_task(orch, store, "t1", requested_files=["tests/__init__.py"])

    app = _make_app(orch, store, tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/tasks/t1/scope-decision",
            json={"decision": "approve", "files": ["tests/__init__.py"], "remember": False},
        )
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "t1", "status": "EXECUTING"}
    assert future.done()
    decision = future.result()
    assert decision.approve is True
    assert decision.extended_files == ["tests/__init__.py"]


@pytest.mark.asyncio
async def test_reject_resolves_with_rejection(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    _, future = await _seed_paused_task(orch, store, "t1", requested_files=["tests/__init__.py"])

    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/t1/scope-decision", json={"decision": "reject"})
    assert resp.status_code == 200
    assert future.done()
    assert future.result().approve is False


@pytest.mark.asyncio
async def test_returns_409_when_task_not_paused(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    task = TaskRecord(task_id="t1", goal="g", workspace_path=".", status=TaskStatus.PLANNED)
    await store.create(task)

    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/t1/scope-decision", json={"decision": "approve"})
    assert resp.status_code == 409
    assert "awaiting scope decision" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_returns_400_when_approving_unrequested_files(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    await _seed_paused_task(orch, store, "t1", requested_files=["tests/__init__.py"])

    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/tasks/t1/scope-decision",
            json={"decision": "approve", "files": ["src/secret.py"]},
        )
    assert resp.status_code == 400
    assert "not in the original request" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_returns_409_when_no_pending_future(tmp_path: Path) -> None:
    """Task has AWAITING_SCOPE_DECISION status but no future (e.g. orphan from restart)."""
    orch, store = _make_orch(tmp_path)
    task = TaskRecord(task_id="t1", goal="g", workspace_path=".")
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "")
    task = transition(task, TaskStatus.PLANNED, "")
    task = transition(task, TaskStatus.EXECUTING, "")
    task = transition(task, TaskStatus.AWAITING_SCOPE_DECISION, "")
    task.execution_state.pending_scope_request = ScopeExtensionRequest(
        decision_id="dec-1", files=["tests/__init__.py"], reason="r", step_id="s1",
    )
    await store.create(task)
    # Note: NO future registered in orch._pending_scope_decisions

    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/t1/scope-decision", json={"decision": "approve"})
    assert resp.status_code == 409
    assert "no pending scope decision" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_returns_404_when_task_missing(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/tasks/missing-id/scope-decision", json={"decision": "approve"},
        )
    assert resp.status_code == 404
