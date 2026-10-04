"""Backend fields for the background-agent UI (spec §6)."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentd.chat.models import AgentRecord, ChatMessage, PendingGate
from tests.test_background_dispatch import _setup


def _row(tid: str, agent_id: str, status: str, ended: datetime | None) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id=tid, turn_id="u", depth=1, name="explore",
                       label=agent_id, prompt="p", status=status, dispatcher_id="main",
                       ended_at=ended, activation_count=2, on_finish="wake")


def test_summary_fields() -> None:
    summary = _row("t", "a", "completed", None).summary()
    assert summary["activation_count"] == 2 and summary["on_finish"] == "wake"
    assert {"team_id", "dispatcher_id", "activation_started_at",
            "activation_ended_at"} <= summary.keys()


def test_live_agents_are_live_or_recent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    store.append_message(tid, ChatMessage(role="user", content="go"))
    now = datetime.now(UTC)
    store.insert_agent(_row(tid, "old", "completed", now - timedelta(days=1)))
    store.insert_agent(_row(tid, "recent", "completed", now + timedelta(seconds=1)))
    store.insert_agent(_row(tid, "busy", "running", None))
    assert sorted(a["agent_id"] for a in ctrl.live_agents(tid)) == ["busy", "recent"]


@pytest.mark.asyncio
async def test_resume_writes_an_agent_message(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [],
                              {"a1": [{"type": "report", "thought": "t", "summary": "x"}]})
    store.insert_agent(_row(tid, "a1", "completed", None))
    ctrl.resume_agent(tid, "a1", "look again")
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert messages[-1].type == "agent_message"
    assert messages[-1].metadata["agent_id"] == "a1"
    assert messages[-1].metadata["activation"] == 3
    await ctrl.stop_all_agents(tid)


@pytest.mark.asyncio
async def test_attention_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_subagent_routes import _client

    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    quiet = store.create_thread(store.get_thread(tid).workspace_path, title="q").thread_id  # type: ignore[union-attr]
    store.add_controller_gate(tid, PendingGate.new("edit", {}))
    store.insert_agent(_row(tid, "busy", "running", None))
    async with _client(tmp_path, ctrl) as client:
        response = await client.get(
            "/v1/chat/attention",
            params={"workspace": store.get_thread(tid).workspace_path})  # type: ignore[union-attr]
    assert response.json() == [{"thread_id": tid, "pending_gates": 1, "agents_running": 1}]
    assert quiet not in str(response.json())
