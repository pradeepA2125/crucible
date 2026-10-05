"""Team routes and /live teams (spec v2 §9, §11.3 phase 4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.teams.models import TeamMember, TeamRecord
from agentd.workspace.shadow import ShadowWorkspaceManager


def _client(tmp_path: Path, ctrl: ChatController) -> AsyncClient:
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    other = store.create_thread(str(tmp_path), title="o").thread_id
    now = datetime.now(UTC)
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id=tid, name="auth", goal="Add login", max_rounds=3,
        round_started_at=now, approval_gate=False, budget=160, created_turn_id="turn-1",
        checkpoint_seq=0, created_at=now))
    for label in ("alice", "bob"):
        store.insert_agent(AgentRecord(
            agent_id=f"agent-{label}", thread_id=tid, turn_id="turn-1", depth=1,
            name="general-purpose", label=label, prompt="p", status="awaiting_peer",
            team_id="team-1"))
        store.teams.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}",
                                          label=label))
    store.teams.append_post("team-1", author="main", kind="proposal", text="plan", round=0,
                            payload={"assignments": [], "shared_files": [], "supersedes": []})
    store.teams.append_post("team-1", author="alice", kind="post", text="dm", recipient="bob")
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    return store, ctrl, tid, other


@pytest.mark.asyncio
async def test_list_detail_live_and_disband(tmp_path: Path, monkeypatch) -> None:
    store, ctrl, tid, other = _seed(tmp_path, monkeypatch)
    async with _client(tmp_path, ctrl) as client:
        listed = (await client.get(f"/v1/chat/threads/{tid}/teams")).json()["teams"]
        detail = (await client.get(f"/v1/chat/threads/{tid}/teams/team-1")).json()
        foreign = await client.get(f"/v1/chat/threads/{other}/teams/team-1")
        missing_thread = await client.get("/v1/chat/threads/nope/teams")
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        disbanded = (await client.post(f"/v1/chat/threads/{tid}/teams/team-1/disband")).json()
        live_after = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    assert [t["team_id"] for t in listed] == ["team-1"]
    assert listed[0]["phase"] == "DELIBERATING" and "created_at" in listed[0]
    assert [p["text"] for p in detail["posts"]] == ["plan", "dm"]   # the user sees DMs
    assert detail["last_seq"] == 2
    assert foreign.status_code == 404 and missing_thread.status_code == 404
    entry = live["teams"][0]
    assert {k: entry[k] for k in ("team_id", "name", "phase", "round", "max_rounds",
                                  "paused_reason")} == {
        "team_id": "team-1", "name": "auth", "phase": "DELIBERATING", "round": 1,
        "max_rounds": 3, "paused_reason": None}
    assert [(m["label"], m["status"]) for m in entry["members"]] == [
        ("alice", "awaiting_peer"), ("bob", "awaiting_peer")]
    assert disbanded == {"team_id": "team-1", "phase": "DISBANDED"}
    assert live_after["teams"] is None


@pytest.mark.asyncio
async def test_routes_soft_when_teams_off(tmp_path: Path, monkeypatch) -> None:
    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "0")
    async with _client(tmp_path, ctrl) as client:
        listed = (await client.get(f"/v1/chat/threads/{tid}/teams")).json()
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    # Rows written while the flag was on stay readable; /live stays quiet.
    assert [t["team_id"] for t in listed["teams"]] == ["team-1"]
    assert live["teams"] is None


@pytest.mark.asyncio
async def test_detail_carries_activity_and_live_carries_state(tmp_path: Path, monkeypatch) -> None:
    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    ctrl._teams.record("team-1", "alice", "woke", activation=1, cause_seq=1,
                       payload={"cause": "kickoff", "by": "main", "post_seq": 1})
    ctrl._teams.record("team-1", "alice", "wrapped_up", activation=1,
                       payload={"status": "completed", "report": "all good"})
    store.teams.append_post("team-1", author="bob", kind="agree", text="ok", ref_id="P1")
    async with _client(tmp_path, ctrl) as client:
        detail = (await client.get(f"/v1/chat/threads/{tid}/teams/team-1")).json()
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    assert [e["kind"] for e in detail["activity"]] == ["woke", "wrapped_up"]
    assert detail["last_aseq"] == 2
    team = live["teams"][0]
    alice = next(m for m in team["members"] if m["label"] == "alice")
    assert alice["last"]["kind"] == "wrapped_up" and alice["last"]["status"] == "completed"
    bob = next(m for m in team["members"] if m["label"] == "bob")
    assert bob["last"] is None
    assert team["latest"]["kind"] == "post" and team["latest"]["label"] == "bob"
    assert team["counts"]["posts"] == 3
    assert team["counts"]["proposals"] == [{"id": "P1", "agree": 1, "object": 0, "pending": 1}]
    assert {m["label"]: m["name"] for m in detail["members"]} == {
        "alice": "general-purpose", "bob": "general-purpose"}
