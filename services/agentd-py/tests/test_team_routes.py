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


@pytest.mark.asyncio
async def test_rewind_refused_while_a_team_is_live(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from agentd.chat.models import ChatMessage
    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    message_id = store.append_message(tid, ChatMessage(role="user", content="hi"))
    ctrl._rewind = SimpleNamespace()        # rewind wired; the guard answers before it is used
    async with _client(tmp_path, ctrl) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": message_id})
    assert r.status_code == 409 and "auth" in r.json()["detail"]


@pytest.mark.asyncio
async def test_team_plan_decision_route_errors(tmp_path: Path, monkeypatch) -> None:
    from agentd.chat.models import GateNotFoundError
    from agentd.teams.validation import TeamPlanConflict, TeamPlanInvalid

    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    outcomes: list[object] = [GateNotFoundError("x"), TeamPlanConflict("y"),
                              TeamPlanInvalid("z"), {"team_id": "team-1", "phase": "IMPLEMENTING"}]
    seen: list[tuple[str, str, str, str | None]] = []

    def decide(thread_id, gate_id, decision, feedback):  # type: ignore[no-untyped-def]
        seen.append((thread_id, gate_id, decision, feedback))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ctrl, "decide_team_plan", decide)
    async with _client(tmp_path, ctrl) as client:
        url = f"/v1/chat/threads/{tid}/team-plan-decision"
        body = {"gate_id": "g1", "decision": "approve"}
        assert (await client.post(url, json=body)).status_code == 404
        assert (await client.post(url, json=body)).status_code == 409
        assert (await client.post(url, json={**body, "decision": "feedback",
                                             "feedback": "x"})).status_code == 422
        ok = await client.post(url, json=body)
        assert ok.status_code == 200 and ok.json() == {"team_id": "team-1",
                                                       "phase": "IMPLEMENTING"}
        assert (await client.post(url, json={**body, "decision": "maybe"})).status_code == 422
    assert seen[2] == (tid, "g1", "feedback", "x")


@pytest.mark.asyncio
async def test_usage_breaks_down_main_agents_and_team_members(
    tmp_path: Path, monkeypatch,
) -> None:
    from agentd.providers.usage import METER, Usage
    store, ctrl, tid, _other = _seed(tmp_path, monkeypatch)
    store.insert_agent(AgentRecord(
        agent_id="agent-helper", thread_id=tid, turn_id="turn-1", depth=2,
        parent_agent_id="agent-alice", name="explore", label="helper", prompt="p",
        status="completed"))
    store.add_thread_usage(tid, Usage(requests=2, prompt_tokens=1000, completion_tokens=50,
                                      cached_tokens=600))
    store.add_agent_usage("agent-alice", Usage(requests=3, prompt_tokens=3000,
                                               completion_tokens=90, cached_tokens=1000))
    store.add_agent_usage("agent-helper", Usage(requests=1, prompt_tokens=500,
                                                completion_tokens=10))
    # bob is mid-activation: nothing persisted yet, only the meter has his counts.
    METER.record("agent-bob", requests=1, prompt=800, completion=20, cached=400)
    try:
        async with _client(tmp_path, ctrl) as client:
            usage = (await client.get(f"/v1/chat/threads/{tid}/usage")).json()
            missing = await client.get("/v1/chat/threads/nope/usage")
    finally:
        METER.take("agent-bob")
    assert missing.status_code == 404
    assert usage["main"] == {"requests": 2, "input": 1000, "output": 50, "cached": 600}
    assert usage["agents"]["agent-helper"] == {
        "requests": 1, "input": 500, "output": 10, "cached": 0}
    assert usage["agents"]["agent-bob"]["input"] == 800
    team = usage["teams"]["team-1"]
    # alice's row includes her helper's usage; the team total is the members' sum.
    assert team["members"]["alice"] == {"requests": 4, "input": 3500, "output": 100,
                                        "cached": 1000}
    assert team["members"]["bob"] == {"requests": 1, "input": 800, "output": 20, "cached": 400}
    assert team["total"] == {"requests": 5, "input": 4300, "output": 120, "cached": 1400}
    assert usage["total"] == {"requests": 7, "input": 5300, "output": 170, "cached": 2000}
