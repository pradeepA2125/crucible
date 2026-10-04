"""Notices reach the main agent exactly once (spec §5.2)."""
import asyncio
from pathlib import Path

import pytest

from agentd.subagents.runtime import ChildResult
from tests.test_background_dispatch import DONE, _setup, _tool


def _report_rows(store, tid):  # type: ignore[no-untyped-def]
    return store._conn.execute(
        "SELECT * FROM agent_notices WHERE thread_id = ?", (tid,)).fetchall()


@pytest.mark.asyncio
async def test_wait_delivers_on_persist_and_writes_one_notice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("wait_agents"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = _report_rows(store, tid)
    assert row["delivered_at"] is not None and row["claimed_turn_id"] is not None
    assert store.list_agents(tid)[0].report_delivered_at is not None


@pytest.mark.asyncio
async def test_a_report_during_a_running_turn_arrives_at_an_iteration_top(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [
        _tool("dispatch_agents", agents=[{"agent": "explore", "label": "scout", "prompt": "look"}]),
        _tool("list_directory", path="."), _tool("search_code", query="a"),
        _tool("search_code", query="b"), _tool("search_code", query="c"),
        DONE], {"scout": [{"type": "report", "thought": "t", "summary": "found it"}]})
    step = ctrl._reasoning.create_controller_step

    async def slow_step(*args, **kwargs):  # type: ignore[no-untyped-def]
        # A scripted step never suspends; a real provider call does, which is when a
        # background agent runs.
        await asyncio.sleep(0.02)
        return await step(*args, **kwargs)

    monkeypatch.setattr(ctrl._reasoning, "create_controller_step", slow_step)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    history = store.get_thread(tid).controller_conversation_history or []  # type: ignore[union-attr]
    arrivals = [m for m in history if m.get("role") == "user"
                and "found it" in str(m.get("content", ""))]
    assert len(arrivals) == 1 and "New message:" in str(arrivals[0]["content"])
    [row] = _report_rows(store, tid)
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_a_report_with_no_turn_running_stays_undelivered(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [DONE], {})
    from agentd.chat.models import AgentRecord
    store.insert_agent(AgentRecord(agent_id="a1", thread_id=tid, turn_id="u", depth=1,
                                   name="explore", label="scout", prompt="p",
                                   status="completed", dispatcher_id="main",
                                   on_finish="notify"))
    handle = ctrl._handle_from_record(tid, "a1")
    ctrl._route_report(handle, ChildResult(status="completed", report="late", files_changed=[]))
    [row] = _report_rows(store, tid)
    assert row["claimed_turn_id"] is None and row["delivery"] == "notify"


@pytest.mark.asyncio
async def test_a_turn_that_never_persists_releases_its_claims(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [DONE], {})
    from datetime import UTC, datetime

    from agentd.chat.models import NoticeRecord
    store.insert_notice(NoticeRecord(notice_id="n1", thread_id=tid, source_kind="agent",
                                     source_id="a1", kind="agent_finished",
                                     payload={"report": "r"}, delivery="notify",
                                     created_at=datetime.now(UTC)))
    store.claim_notices(["n1"], "dead-turn", 0)
    ctrl.reap_subagents()
    assert [n.notice_id for n in store.unclaimed_notices(tid)] == ["n1"]
