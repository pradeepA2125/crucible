"""Accepting the validation gate must advance the CALLER's task object in place.

`run_task`'s `finally` writes the chat completion line from its local `task`
variable. The repair path exits via `return await _pause_for_validation_decision(...)`,
which never rebinds that local — so if the pause re-fetches a fresh record
(SQLite store returns a copy) and transitions THAT, the finally sees a stale
AWAITING_VALIDATION_DECISION object and writes
"Execution failed: <validation diagnostics>" into the transcript even though
the user accepted and the task proceeded to READY_FOR_REVIEW.

Uses SQLiteTaskStore deliberately: InMemoryTaskStore returns the same object
reference and masks this bug class (see CLAUDE.md testing patterns).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import Diagnostic, TaskRecord, TaskStatus, ValidationResult
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _make(tmp_path: Path) -> tuple[AgentOrchestrator, ChatThreadStore, str]:
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    thread = chat_store.create_thread(str(tmp_path))
    orch = AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "tasks.sqlite3"),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        chat_store=chat_store,
    )
    return orch, chat_store, thread.thread_id


async def _seed_validating(orch, thread_id: str, ws: Path) -> TaskRecord:
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(ws),
                      chat_channel_id=f"chat:{thread_id}")
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


def _failed_validation() -> ValidationResult:
    return ValidationResult(
        success=False,
        diagnostics=[Diagnostic(source="validator:pytest", message="boom", level="error")],
        duration_ms=1,
    )


async def _wait_pending(d: dict, key: str) -> None:
    for _ in range(200):
        await asyncio.sleep(0)
        if key in d:
            return
    raise AssertionError("gate future never registered")


@pytest.mark.asyncio
async def test_accept_advances_callers_task_object(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    orch, _chat_store, thread_id = _make(tmp_path)
    task = await _seed_validating(orch, thread_id, ws)

    gate = asyncio.create_task(
        orch._pause_for_validation_decision(task, _failed_validation())
    )
    await _wait_pending(orch._pending_validation_decisions, task.task_id)
    orch._pending_validation_decisions[task.task_id].set_result(True)
    returned = await gate

    assert returned.status == TaskStatus.READY_FOR_REVIEW
    # The invariant under test: the CALLER's object advanced in place. run_task's
    # finally writes the completion line from this reference, not the returned one.
    assert task.status == TaskStatus.READY_FOR_REVIEW
    assert task.execution_state.pending_validation is None


@pytest.mark.asyncio
async def test_accept_writes_complete_not_failed_completion(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    orch, chat_store, thread_id = _make(tmp_path)
    task = await _seed_validating(orch, thread_id, ws)

    gate = asyncio.create_task(
        orch._pause_for_validation_decision(task, _failed_validation())
    )
    await _wait_pending(orch._pending_validation_decisions, task.task_id)
    orch._pending_validation_decisions[task.task_id].set_result(True)
    await gate

    # Mirror run_task's finally: completion line is written from the caller's object.
    orch._write_chat_completion(task)

    thread = chat_store.get_thread(thread_id)
    completions = [m.content for m in thread.messages
                   if m.type == "text" and not (m.metadata or {}).get("breadcrumb")]
    assert any("Execution complete" in c for c in completions)
    assert not any("Execution failed" in c for c in completions)
