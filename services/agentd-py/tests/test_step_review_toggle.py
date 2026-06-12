"""The per-message step_review flag reaches the created task."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


class _NullStore:
    def append_message(self, thread_id: str, message: object) -> None: ...
    def set_active_task(self, thread_id: str, task_id: str) -> None: ...


def _orch(tmp_path: Path) -> tuple[AgentOrchestrator, InMemoryTaskStore]:
    store = InMemoryTaskStore()
    orch = AgentOrchestrator(
        store=store,
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    return orch, store


@pytest.mark.asyncio
async def test_step_review_flag_forces_review(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    orch, store = _orch(tmp_path)
    task_id = await orch.create_task_from_chat(
        thread_id="t", goal="g", workspace_path=str(ws),
        explore_context=[], store=_NullStore(),
        step_review_auto_accept=False,
    )
    task = await store.get(task_id)
    assert task.step_review_auto_accept is False


@pytest.mark.asyncio
async def test_step_review_flag_none_keeps_env_default(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("AI_EDITOR_STEP_REVIEW_AUTO_ACCEPT", "true")
    orch, store = _orch(tmp_path)
    task_id = await orch.create_task_from_chat(
        thread_id="t", goal="g", workspace_path=str(ws),
        explore_context=[], store=_NullStore(),
    )
    task = await store.get(task_id)
    assert task.step_review_auto_accept is True
