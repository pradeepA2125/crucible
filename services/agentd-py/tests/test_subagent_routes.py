"""Agent routes, /live agents and the config flag (spec §11.1, §11.3, §12)."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord, ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _client(tmp_path: Path, ctrl: ChatController) -> AsyncClient:
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_get_stop_live_and_config(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    other = store.create_thread(str(tmp_path), title="o").thread_id
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    store.insert_agent(AgentRecord(
        agent_id="agent-a", thread_id=tid, turn_id="turn-1", depth=1,
        name="general-purpose", label="impl", prompt="p", status="completed",
        report="R" * 500, files_changed=["a.py"]))
    store.set_agent_transcript("agent-a", [
        ChatMessage(role="agent", content="x", metadata={"seq": 4}),
        ChatMessage(role="agent", content="R", metadata={"report": True, "seq": 9})])
    async with _client(tmp_path, ctrl) as client:
        listed = (await client.get(f"/v1/chat/threads/{tid}/agents")).json()["agents"]
        by_turn = (await client.get(
            f"/v1/chat/threads/{tid}/agents", params={"turn_id": "nope"})).json()["agents"]
        detail = (await client.get(f"/v1/chat/threads/{tid}/agents/agent-a")).json()
        foreign = await client.get(f"/v1/chat/threads/{other}/agents/agent-a")
        missing_thread = await client.get("/v1/chat/threads/nope/agents")
        stopped = (await client.post(f"/v1/chat/threads/{tid}/agents/agent-a/stop")).json()
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        config = (await client.get("/v1/config")).json()
    assert listed == [{
        "agent_id": "agent-a", "turn_id": "turn-1", "parent_agent_id": None, "depth": 1,
        "name": "general-purpose", "label": "impl", "status": "completed",
        "files_changed_count": 1, "started_at": None, "ended_at": None,
        "report_preview": "R" * 200}]
    assert by_turn == []
    assert detail["report"] == "R" * 500 and detail["last_seq"] == 9
    assert [m["content"] for m in detail["transcript"]] == ["x", "R"]
    assert foreign.status_code == 404 and missing_thread.status_code == 404
    assert stopped == {"ok": False}  # already finished
    assert live["agents"] is None
    assert config["subagents_enabled"] is True
