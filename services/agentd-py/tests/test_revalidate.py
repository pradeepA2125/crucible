from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.domain.models import (
    Diagnostic,
    PlanDocument,
    PlanStep,
    TaskRecord,
    TaskStatus,
    ValidationResult,
)
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _StubReasoner:
    pass


class _StubValidator:
    def __init__(self, result: ValidationResult) -> None:
        self._result = result

    async def run(self, workspace_path: str) -> ValidationResult:
        _ = workspace_path
        return self._result


def _orchestrator(store: InMemoryTaskStore, validator: _StubValidator, tmp_path: Path) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=store,
        reasoning_engine=_StubReasoner(),
        validator=validator,
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


def _plan() -> PlanDocument:
    return PlanDocument(
        analysis="a",
        steps=[PlanStep(id="s1", goal="g", targets=[{"path": "f.txt", "intent": "new"}], risk="low")],
        expected_files=["f.txt"],
        stop_conditions=["validation passes"],
    )


async def _make_completed_task(
    store: InMemoryTaskStore,
    tmp_path: Path,
    *,
    baseline: list[str] | None = None,
    shadow_exists: bool = True,
) -> TaskRecord:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    shadow = tmp_path / "shadow"
    if shadow_exists:
        shadow.mkdir(parents=True, exist_ok=True)
    task = TaskRecord(
        task_id="t-reval",
        goal="g",
        workspace_path=str(ws),
        status=TaskStatus.PLANNED,
        plan=_plan(),
        completed_step_ids=["s1"],
        shadow_workspace_path=str(shadow),
        baseline_error_fingerprints=baseline or [],
    )
    await store.create(task)
    return task


@pytest.mark.asyncio
async def test_revalidate_passes_to_ready_for_review(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    validator = _StubValidator(ValidationResult(success=True, diagnostics=[], duration_ms=1))
    orch = _orchestrator(store, validator, tmp_path)
    await _make_completed_task(store, tmp_path)

    result = await orch.revalidate_task("t-reval")
    assert result.status == TaskStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_revalidate_filters_baselined_error(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    fingerprint = AgentOrchestrator._normalize_error_message("preexisting failure")
    validator = _StubValidator(
        ValidationResult(
            success=False,
            diagnostics=[Diagnostic(source="v", message="preexisting failure", level="error")],
            duration_ms=1,
        )
    )
    orch = _orchestrator(store, validator, tmp_path)
    await _make_completed_task(store, tmp_path, baseline=[fingerprint])

    # The only error is in the original baseline → filtered → success.
    result = await orch.revalidate_task("t-reval")
    assert result.status == TaskStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_revalidate_fails_when_shadow_missing(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    validator = _StubValidator(ValidationResult(success=True, diagnostics=[], duration_ms=1))
    orch = _orchestrator(store, validator, tmp_path)
    await _make_completed_task(store, tmp_path, shadow_exists=False)

    result = await orch.revalidate_task("t-reval")
    assert result.status == TaskStatus.FAILED


def _build_app(store, orchestrator, workspace_manager) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(store, orchestrator, workspace_manager))
    return app


async def _wait_for_status(client: AsyncClient, task_id: str, expected: str, timeout_sec: float = 2.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    last: dict | None = None
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/v1/tasks/{task_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] == expected:
            return last
        await asyncio.sleep(0.01)
    pytest.fail(f"Timed out waiting for {expected}; last={last}")


@pytest.mark.asyncio
async def test_resume_validate_rejects_when_steps_incomplete(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    workspace_manager = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = _orchestrator(store, _StubValidator(ValidationResult(success=True, diagnostics=[], duration_ms=1)), tmp_path)
    shadow = tmp_path / "shadow"
    shadow.mkdir(parents=True)
    parent = TaskRecord(
        task_id="p-incomplete",
        goal="g",
        workspace_path=str(tmp_path / "ws"),
        status=TaskStatus.FAILED,
        plan=_plan(),
        completed_step_ids=[],  # s1 NOT done
        shadow_workspace_path=str(shadow),
    )
    await store.create(parent)
    app = _build_app(store, orch, workspace_manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/p-incomplete/resume", json={"stage": "validate"})
    assert resp.status_code == 409
    assert "steps are complete" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_resume_validate_happy_path_reaches_ready_for_review(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    workspace_manager = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = _orchestrator(store, _StubValidator(ValidationResult(success=True, diagnostics=[], duration_ms=1)), tmp_path)
    (tmp_path / "ws").mkdir(parents=True)
    shadow = tmp_path / "shadow"
    shadow.mkdir(parents=True)
    (shadow / "f.txt").write_text("done")
    parent = TaskRecord(
        task_id="p-complete",
        goal="g",
        workspace_path=str(tmp_path / "ws"),
        status=TaskStatus.FAILED,
        plan=_plan(),
        completed_step_ids=["s1"],  # all steps done
        shadow_workspace_path=str(shadow),
    )
    await store.create(parent)
    app = _build_app(store, orch, workspace_manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/p-complete/resume", json={"stage": "validate"})
        assert resp.status_code == 200
        child_id = resp.json()["task_id"]
        assert child_id != "p-complete"
        payload = await _wait_for_status(client, child_id, "READY_FOR_REVIEW")
    assert payload["resume_of_task_id"] == "p-complete"


async def _wait_for_gate(store: InMemoryTaskStore, task_id: str, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if (await store.get(task_id)).status == TaskStatus.AWAITING_VALIDATION_DECISION:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("validation-decision gate not reached")


def _failing_validator() -> _StubValidator:
    return _StubValidator(
        ValidationResult(
            success=False,
            diagnostics=[Diagnostic(source="v", message="boom", level="error")],
            duration_ms=1,
        )
    )


@pytest.mark.asyncio
async def test_revalidate_failure_opens_gate_then_accept(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    orch = _orchestrator(store, _failing_validator(), tmp_path)
    await _make_completed_task(store, tmp_path)

    run = asyncio.create_task(orch.revalidate_task("t-reval"))
    await _wait_for_gate(store, "t-reval")
    orch._pending_validation_decisions["t-reval"].set_result(True)  # accept
    result = await run
    assert result.status == TaskStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_revalidate_failure_gate_reject_fails(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    orch = _orchestrator(store, _failing_validator(), tmp_path)
    await _make_completed_task(store, tmp_path)

    run = asyncio.create_task(orch.revalidate_task("t-reval"))
    await _wait_for_gate(store, "t-reval")
    orch._pending_validation_decisions["t-reval"].set_result(False)  # reject
    result = await run
    assert result.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_validation_decision_endpoint_accept_reaches_ready(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    workspace_manager = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = _orchestrator(store, _failing_validator(), tmp_path)
    await _make_completed_task(store, tmp_path)
    app = _build_app(store, orch, workspace_manager)

    run = asyncio.create_task(orch.revalidate_task("t-reval"))
    await _wait_for_gate(store, "t-reval")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/t-reval/validation-decision", json={"decision": "accept"})
        assert resp.status_code == 200
    result = await run
    assert result.status == TaskStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_validation_decision_endpoint_409_when_not_awaiting(tmp_path: Path) -> None:
    store = InMemoryTaskStore()
    workspace_manager = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    orch = _orchestrator(store, _failing_validator(), tmp_path)
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    await store.create(
        TaskRecord(task_id="t-x", goal="g", workspace_path=str(tmp_path / "ws"), status=TaskStatus.FAILED)
    )
    app = _build_app(store, orch, workspace_manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/tasks/t-x/validation-decision", json={"decision": "accept"})
    assert resp.status_code == 409
