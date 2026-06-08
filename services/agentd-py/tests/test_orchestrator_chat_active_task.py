"""Task 4: the chat thread follows its task.

create_task_from_chat and resume_from_execute repoint the thread's active_task_id,
and the validation gate persists its payload on execution_state so /live can surface it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import (
    Diagnostic,
    PlanDocument,
    PlanStep,
    TaskRecord,
    TaskStatus,
    ValidationResult,
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
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


def _make_orchestrator(tmp_path: Path, *, chat_store: ChatThreadStore | None = None) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        chat_store=chat_store,
    )


@pytest.mark.asyncio
async def test_create_task_from_chat_anchors_thread(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    thread = chat_store.create_thread(str(ws))
    orch = _make_orchestrator(tmp_path, chat_store=chat_store)

    task_id = await orch.create_task_from_chat(
        thread_id=thread.thread_id,
        goal="add a thing",
        workspace_path=str(ws),
        explore_context=[],
        store=chat_store,
    )

    assert chat_store.get_thread(thread.thread_id).active_task_id == task_id


@pytest.mark.asyncio
async def test_resume_from_execute_repoints_thread_to_child(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    shadow = tmp_path / "parent-shadow"
    shadow.mkdir()
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    thread = chat_store.create_thread(str(ws))
    chat_store.set_active_task(thread.thread_id, "task-parent")
    orch = _make_orchestrator(tmp_path, chat_store=chat_store)

    parent = TaskRecord(
        task_id="task-parent",
        goal="g",
        workspace_path=str(ws),
        status=TaskStatus.FAILED,
        plan=PlanDocument(
            analysis="a",
            steps=[PlanStep(id="s1", goal="g", targets=[], risk="low")],
            expected_files=[],
            stop_conditions=[],
        ),
        plan_markdown="# Plan",
        shadow_workspace_path=str(shadow),
    )
    await orch._store.create(parent)

    child_id = await orch.resume_from_execute(
        "task-parent", chat_channel_id=f"chat:{thread.thread_id}"
    )

    # Synchronously repointed before the background run; the thread now follows the child.
    assert child_id != "task-parent"
    assert chat_store.get_thread(thread.thread_id).active_task_id == child_id


async def _seed_validating_task(orch: AgentOrchestrator, ws: Path) -> TaskRecord:
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(ws))
    for status, reason in [
        (TaskStatus.CONTEXT_READY, "ctx"),
        (TaskStatus.AWAITING_PLAN_APPROVAL, "approval"),
        (TaskStatus.PLANNED, "planned"),
        (TaskStatus.EXECUTING, "executing"),
        (TaskStatus.VALIDATING, "validating"),
    ]:
        task = transition(task, status, reason)
    await orch._store.create(task)
    return task


@pytest.mark.asyncio
async def test_validation_gate_persists_then_clears_pending_validation(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    orch = _make_orchestrator(tmp_path)
    task = await _seed_validating_task(orch, ws)
    validation = ValidationResult(
        success=False,
        duration_ms=1,
        diagnostics=[
            Diagnostic(source="pytest", message="x failed", level="error"),
            Diagnostic(source="pytest", message="y failed", level="error"),
        ],
    )

    gate = asyncio.create_task(orch._pause_for_validation_decision(task, validation))
    for _ in range(200):
        await asyncio.sleep(0)
        if task.task_id in orch._pending_validation_decisions:
            break
    assert task.task_id in orch._pending_validation_decisions

    paused = await orch._store.get(task.task_id)
    assert paused.status == TaskStatus.AWAITING_VALIDATION_DECISION
    pv = paused.execution_state.pending_validation
    assert pv is not None
    assert pv["summary"] == "2 validation error(s)"
    assert len(pv["diagnostics"]) == 2

    orch._pending_validation_decisions[task.task_id].set_result(True)
    resolved = await gate

    assert resolved.status == TaskStatus.READY_FOR_REVIEW
    assert resolved.execution_state.pending_validation is None
