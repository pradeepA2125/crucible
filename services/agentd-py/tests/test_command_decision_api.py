"""Tests for POST /v1/tasks/{id}/command-decision route (T6)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import (
    CommandApprovalRequest,
    CommandDecision,
    TaskRecord,
    TaskStatus,
)
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
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


def _make_app(orch: AgentOrchestrator, store: InMemoryTaskStore, tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_router(
            store=store,
            orchestrator=orch,
            workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        )
    )
    return app


async def _seed_paused_task(
    orch: AgentOrchestrator,
    store: InMemoryTaskStore,
    task_id: str = "t1",
) -> tuple[TaskRecord, asyncio.Future[CommandDecision]]:
    task = TaskRecord(task_id=task_id, goal="g", workspace_path=".")
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "approval")
    task = transition(task, TaskStatus.PLANNED, "planned")
    task = transition(task, TaskStatus.EXECUTING, "executing")
    task = transition(task, TaskStatus.AWAITING_COMMAND_DECISION, "command gate")
    task.execution_state.pending_command_request = CommandApprovalRequest(
        decision_id="dec-1",
        command="python",
        args=["-c", "print(1)"],
        cwd=".",
        step_id="s1",
    )
    await store.create(task)
    future: asyncio.Future[CommandDecision] = asyncio.get_event_loop().create_future()
    orch._pending_command_decisions[task_id] = future
    return task, future


@pytest.mark.asyncio
async def test_command_decision_resolves_future_on_approve(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    task, future = await _seed_paused_task(orch, store)
    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post(
            f"/v1/tasks/{task.task_id}/command-decision",
            json={"approve": True, "remember": True, "scope": "prefix", "rule_value": "python -c"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task.task_id
    assert body["status"] == TaskStatus.EXECUTING
    assert future.done()
    decision = future.result()
    assert decision.approve is True
    assert decision.remember is True
    assert decision.scope == "prefix"
    assert decision.rule_value == "python -c"


@pytest.mark.asyncio
async def test_command_decision_resolves_future_on_reject(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    task, future = await _seed_paused_task(orch, store, task_id="t2")
    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post(
            f"/v1/tasks/{task.task_id}/command-decision",
            json={"approve": False},
        )
    assert r.status_code == 200
    assert future.done()
    assert future.result().approve is False


@pytest.mark.asyncio
async def test_command_decision_409_when_not_awaiting(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    # Task in EXECUTING — not awaiting a command decision.
    task = TaskRecord(task_id="t3", goal="g", workspace_path=".")
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "approval")
    task = transition(task, TaskStatus.PLANNED, "planned")
    task = transition(task, TaskStatus.EXECUTING, "executing")
    await store.create(task)
    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post(
            f"/v1/tasks/{task.task_id}/command-decision",
            json={"approve": True},
        )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_command_decision_404_for_unknown_task(tmp_path: Path) -> None:
    orch, store = _make_orch(tmp_path)
    app = _make_app(orch, store, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post(
            "/v1/tasks/nope/command-decision",
            json={"approve": True},
        )
    assert r.status_code == 404
