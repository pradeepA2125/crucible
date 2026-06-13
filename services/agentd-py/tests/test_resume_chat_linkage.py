"""POST /resume keeps the chat thread attached to the child task.

The resume route used to build the child TaskRecord without the parent's
chat_channel_id and never repointed the thread's active_task_id — so a resumed
chat-originated task wrote no breadcrumbs/records to its thread, /live kept
rendering the FAILED parent, and the child's gates were invisible (parked
forever with decision timeout 0).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import TaskRecord, TaskStatus, ValidationResult
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


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_resume_carries_chat_channel_and_repoints_thread(tmp_path: Path) -> None:
    app, store, chat_store = _build(tmp_path)
    thread = chat_store.create_thread(str(tmp_path))
    parent = TaskRecord(
        task_id="parent-1", goal="g", workspace_path=str(tmp_path),
        status=TaskStatus.FAILED,
        chat_channel_id=f"chat:{thread.thread_id}",
    )
    await store.create(parent)
    chat_store.set_active_task(thread.thread_id, parent.task_id)

    async with _client(app) as client:
        resp = await client.post("/v1/tasks/parent-1/resume", json={"stage": "plan"})

    assert resp.status_code == 200
    child_id = resp.json()["task_id"]

    child = await store.get(child_id)
    assert child.chat_channel_id == f"chat:{thread.thread_id}"

    refreshed = chat_store.get_thread(thread.thread_id)
    assert refreshed.active_task_id == child_id
    crumbs = [m.content for m in refreshed.messages
              if (m.metadata or {}).get("breadcrumb")]
    assert any("Resumed" in c for c in crumbs)


@pytest.mark.asyncio
async def test_resume_persists_task_card_for_child(tmp_path: Path) -> None:
    """The resumed child needs a task_card anchor in the transcript: it gives the run
    a durable transcript row (the chat-created path writes one; resume did not), and it
    lets a later _find_recent_task discover the CHILD for a resume-of-resume instead of
    the FAILED parent."""
    app, store, chat_store = _build(tmp_path)
    thread = chat_store.create_thread(str(tmp_path))
    parent = TaskRecord(
        task_id="parent-3", goal="g", workspace_path=str(tmp_path),
        status=TaskStatus.FAILED,
        chat_channel_id=f"chat:{thread.thread_id}",
    )
    await store.create(parent)
    chat_store.set_active_task(thread.thread_id, parent.task_id)

    async with _client(app) as client:
        resp = await client.post("/v1/tasks/parent-3/resume", json={"stage": "plan"})

    assert resp.status_code == 200
    child_id = resp.json()["task_id"]

    refreshed = chat_store.get_thread(thread.thread_id)
    task_cards = [m for m in refreshed.messages if m.type == "task_card"]
    assert any(m.task_id == child_id for m in task_cards), [
        (m.type, m.task_id) for m in refreshed.messages
    ]


@pytest.mark.asyncio
async def test_resume_without_chat_linkage_stays_detached(tmp_path: Path) -> None:
    app, store, _chat_store = _build(tmp_path)
    parent = TaskRecord(
        task_id="parent-2", goal="g", workspace_path=str(tmp_path),
        status=TaskStatus.FAILED,
    )
    await store.create(parent)

    async with _client(app) as client:
        resp = await client.post("/v1/tasks/parent-2/resume", json={"stage": "plan"})

    assert resp.status_code == 200
    child = await store.get(resp.json()["task_id"])
    assert child.chat_channel_id is None
