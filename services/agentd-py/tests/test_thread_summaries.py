"""GET /chat/threads carries message_count, updated_at, and a status chip."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.agent import ChatAgent
from agentd.chat.live_state import thread_status_chip
from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import TaskRecord, TaskStatus, ValidationResult
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


class _NullTransport:
    async def generate_text(self, **_) -> str:
        return "x"

    async def generate_json(self, *, schema_name, **_) -> dict:
        return {"intent": "qa", "rationale": "", "likely_targets": []}


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _build(tmp_path: Path):
    store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    orch = AgentOrchestrator(
        store=store,
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ws_manager,
        chat_store=chat_store,
    )
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=_NullTransport(),
        model="test-model",
        thread_store=chat_store,
        orchestrator=orch,
        broadcaster=orch.broadcaster,
    )
    app = FastAPI()
    app.include_router(build_router(store, orch, ws_manager, None, agent))
    return app, store, chat_store


# ── chip mapping (pure) ──────────────────────────────────────────────────────


def test_chip_mapping() -> None:
    assert thread_status_chip("EXECUTING") == "running"
    assert thread_status_chip("QUEUED") == "running"
    assert thread_status_chip("VALIDATING") == "running"
    assert thread_status_chip("AWAITING_PLAN_APPROVAL") == "review"
    assert thread_status_chip("AWAITING_STEP_REVIEW") == "review"
    assert thread_status_chip("AWAITING_COMMAND_DECISION") == "review"
    assert thread_status_chip("READY_FOR_REVIEW") == "review"
    assert thread_status_chip("SUCCEEDED") == "done"
    assert thread_status_chip("FAILED") == "failed"
    assert thread_status_chip("ABORTED") == "failed"
    assert thread_status_chip(None) is None
    assert thread_status_chip("NOT_A_STATUS") is None


# ── route ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_threads_carries_count_updated_at_and_status(tmp_path: Path) -> None:
    app, store, chat_store = _build(tmp_path)
    thread = chat_store.create_thread(str(tmp_path))
    chat_store.append_message(thread.thread_id, ChatMessage(role="user", content="hi"))
    chat_store.append_message(thread.thread_id, ChatMessage(role="agent", content="yo"))

    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path))
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "gate")
    await store.create(task)
    chat_store.set_active_task(thread.thread_id, "t1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(f"/v1/chat/threads?workspace={tmp_path}")

    assert resp.status_code == 200
    [summary] = resp.json()["threads"]
    assert summary["message_count"] == 2
    assert summary["status"] == "review"
    # updated_at must be the LAST message's timestamp, not created_at.
    assert summary["updated_at"] >= summary["created_at"]


@pytest.mark.asyncio
async def test_list_threads_without_task_has_null_status(tmp_path: Path) -> None:
    app, _store, chat_store = _build(tmp_path)
    chat_store.create_thread(str(tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(f"/v1/chat/threads?workspace={tmp_path}")

    [summary] = resp.json()["threads"]
    assert summary["status"] is None
    assert summary["message_count"] == 0
    assert summary["updated_at"] == summary["created_at"]
