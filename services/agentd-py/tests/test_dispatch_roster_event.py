"""The dispatch roster message is broadcast live, not only persisted (spec §6.1, §10)."""
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_dispatch_integration import DONE, WAIT, _controller, _creates, _dispatch


def _record(ctrl) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    inner = ctrl._broadcaster.broadcast

    def broadcast(channel: str, event: dict) -> None:
        calls.append((channel, event))
        inner(channel, event)

    ctrl._broadcaster.broadcast = broadcast  # SequencedBroadcaster forwards here, stamped
    return calls


@pytest.mark.asyncio
async def test_main_dispatch_broadcasts_its_roster_on_the_thread_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("general-purpose", "impl-a", "Create a.py")), DONE],
        agent_scripts={"impl-a": _creates("a.py", "A = 1\n")})
    ctrl = _controller(ws, tmp_path, store, engine)
    calls = _record(ctrl)

    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    on_thread = [e for ch, e in calls if ch == f"chat:{tid}"]
    types = [e["type"] for e in on_thread]
    assert "agent_dispatch" in types
    assert types.index("agent_dispatch") < types.index("agent_started")
    message = next(e for e in on_thread if e["type"] == "agent_dispatch")["payload"]["message"]
    [row] = store.list_agents(tid)
    assert message["type"] == "agent_dispatch"
    assert message["metadata"]["agent_ids"] == [row.agent_id]


@pytest.mark.asyncio
async def test_nested_roster_is_broadcast_before_it_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("general-purpose", "lead", "Split the work")), WAIT, DONE],
        agent_scripts={
            "lead": [_dispatch(("general-purpose", "leaf", "Create leaf.py")), WAIT,
                     {"type": "report", "thought": "t", "summary": "Lead done."}],
            "leaf": _creates("leaf.py", "LEAF = 1\n")})
    ctrl = _controller(ws, tmp_path, store, engine)
    calls = _record(ctrl)

    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    rows = {r.label: r for r in store.list_agents(tid)}
    lead_channel = f"chat:{tid}:agent:{rows['lead'].agent_id}"
    [event] = [e for ch, e in calls if ch == lead_channel and e["type"] == "agent_dispatch"]
    [persisted] = [m for m in rows["lead"].transcript if m.type == "agent_dispatch"]
    # The persisted copy records the seq of the event that produced it, so a viewer that
    # backfills and then subscribes skips the replayed event (seq <= last_seq).
    assert persisted.metadata["seq"] == event["seq"]
    assert event["payload"]["message"]["metadata"]["agent_ids"] == [rows["leaf"].agent_id]
