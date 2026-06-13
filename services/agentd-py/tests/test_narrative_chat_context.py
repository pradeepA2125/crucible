from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import TaskRecord
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoReason:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError
    async def summarize_run(self, **k): return {"headline": "Did X", "points": ["a"]}


class _OkValidator:
    async def run(self, _p):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


@pytest.mark.asyncio
async def test_narrative_persisted_as_transcript_message(tmp_path: Path):
    chat = ChatThreadStore(tmp_path / "chat.db")
    thread = chat.create_thread(str(tmp_path))
    orch = AgentOrchestrator(
        store=SQLiteTaskStore(tmp_path / "db.sqlite3"), reasoning_engine=_NoReason(),
        validator=_OkValidator(), patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        chat_store=chat,
    )
    task = TaskRecord(task_id="t1", goal="g", workspace_path=str(tmp_path),
                      chat_channel_id=f"chat:{thread.thread_id}")
    await orch._finalize_task_narrative(task, "succeeded")
    orch._write_chat_narrative(task)
    msgs = chat.get_thread(thread.thread_id).messages
    assert any("Did X" in (m.content or "") for m in msgs)
