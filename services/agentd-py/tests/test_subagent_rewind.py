"""Rewind removes the rewound turns' children (spec §11.6)."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord, ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.memory.harness import MemoryHarness
from agentd.memory.models import CompactionSegment
from agentd.memory.store import MemoryStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _Compactor:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store


def _row(agent_id: str, turn_id: str, tid: str) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id=tid, turn_id=turn_id, depth=1,
                       name="general-purpose", label=agent_id, prompt="p", status="completed",
                       transcript=[ChatMessage(role="agent", content="", metadata={
                           "tool_events": [{"tool": "run_command"}, {"tool": "read_file"}]})])


@pytest.mark.asyncio
async def test_rewind_deletes_children_memory_and_the_write_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    rewind = RewindStore(store, tmp_path)
    memory = MemoryStore(tmp_path / "m.sqlite3")
    harness = MemoryHarness(enabled=True, compactor=_Compactor(memory))
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None, rewind_store=rewind, memory_harness=harness)
    first = store.append_message(tid, ChatMessage(role="user", content="one"))
    rewind.open_checkpoint(tid, first, "turn-1", thread=store.get_thread(tid),
                           memory_anchor_md=None)
    second = store.append_message(tid, ChatMessage(role="user", content="two"))
    rewind.open_checkpoint(tid, second, "turn-2", thread=store.get_thread(tid),
                           memory_anchor_md=None)
    store.insert_agent(_row("agent-keep", "turn-1", tid))
    store.insert_agent(_row("agent-gone", "turn-2", tid))
    run = f"{tid}:agent-gone"
    memory.upsert_anchor(run, "summary")
    memory.add_segments([CompactionSegment(id="s1", run_id=run, seq=0, content="x",
                                              created_at=datetime.now(UTC))])
    log = ctrl._write_log_for(tid)
    assert log is not None
    log.register_agent("agent-gone")

    preview = rewind.preview(tid, second)
    assert preview is not None and preview.commands_run == 1  # the child's run_command

    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = (await client.post(f"/v1/chat/threads/{tid}/rewind",
                                      json={"message_id": second})).json()
    assert response["removed_agents"] == 1
    assert [r.agent_id for r in store.list_agents(tid)] == ["agent-keep"]
    assert memory.get_anchor(run) is None and memory.get_segments(run) == []
    assert ctrl._write_log_for(tid) is not log  # reset: a fresh, empty log


def test_agents_are_deleted_by_checkpoint_stamp(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from agentd.chat.models import AgentRecord
    from agentd.chat.storage import ChatThreadStore

    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    for agent_id, seq, turn in (("old", 0, "t0"), ("cont", 1, "uuid-continuation"),
                                ("new", 2, "t2"), ("before", -1, "tx")):
        store.insert_agent(AgentRecord(
            agent_id=agent_id, thread_id=tid, turn_id=turn, depth=1, name="general-purpose",
            label=agent_id, prompt="p", status="completed", checkpoint_seq=seq,
            dispatcher_id="main"))
    # A continuation-turn agent (turn id matches no checkpoint) is caught by its stamp.
    assert sorted(store.delete_agents_from_checkpoint(tid, 1)) == ["cont", "new"]
    assert sorted(r.agent_id for r in store.list_agents(tid)) == ["before", "old"]
