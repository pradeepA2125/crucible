"""Desktop metadata uses real storage/routes without transcript reads or cursor writes."""
import asyncio
import json
from datetime import datetime, UTC

import pytest
from httpx import ASGITransport, AsyncClient
from test_chat_activity import fixture, agent, sessions  # noqa: F401
from agentd.teams.models import TeamRecord

async def read(fixture, workspace=None):
    app, _, _, _, root = fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/v1/chat/lifecycle", params={"workspace": str(root) if workspace is None else workspace, "request_id": "aabb-001"})

@pytest.mark.asyncio
async def test_named_resources_gates_and_teams_are_readonly(fixture):
    _, handler, store, tid, root = fixture
    handler._active_turns[tid] = asyncio.current_task()
    agent(store, tid, "running")
    sessions(handler, tid)
    store.teams.create_team(TeamRecord(team_id="team", thread_id=tid, name="Reviewers", goal="g", max_rounds=2, budget=100, created_turn_id="turn",created_at=datetime.now(UTC)))
    store._conn.execute("UPDATE chat_threads SET title=?,controller_gate_json=?,messages_json=?,controller_history_json=? WHERE thread_id=?", ("Alpha", json.dumps([{"kind":"command","gate_id":"g","payload":{}}]), "broken transcript", "broken history", tid))
    store._conn.commit()
    before = list(store._conn.iterdump())
    response = await read(fixture)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["supported"] is True
    assert body["process_id"] == handler.context_process_id()
    assert body["workspace_path"] == str(root.resolve())
    row = body["threads"][0]
    assert row["title"] == "Alpha" and row["gates"] == 1 and row["turn_active"] is True
    assert row["agents"] == [{"id":"agent-1","label":"a"}]
    assert row["teams"] == [{"id":"team","label":"Reviewers"}]
    assert row["sessions"] == [{"id":"0","label":"display only"}]
    assert list(store._conn.iterdump()) == before

@pytest.mark.asyncio
async def test_canonical_alias_and_no_legacy_false_idle(fixture):
    _, handler, _, _, root = fixture
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert (await read(fixture, str(alias))).json()["supported"] is True
    handler._task_subsystem_enabled = True
    assert (await read(fixture)).json()["supported"] is False

@pytest.mark.asyncio
async def test_invalid_gate_unknown_team_and_limits_remain_unknown(fixture):
    _, _, store, tid, _ = fixture
    store._conn.execute("UPDATE chat_threads SET controller_gate_json='invalid' WHERE thread_id=?", (tid,))
    store._conn.commit()
    assert (await read(fixture)).json()["supported"] is False
    store._conn.execute("UPDATE chat_threads SET controller_gate_json=NULL WHERE thread_id=?", (tid,))
    store.teams.create_team(TeamRecord(team_id="team",thread_id=tid,name="Mystery",goal="g",phase="UNKNOWN",max_rounds=2,budget=1,created_turn_id="turn",created_at=datetime.now(UTC)))
    assert (await read(fixture)).json()["supported"] is False
    store._conn.execute("DELETE FROM teams")
    for i in range(1025):
        agent(store,tid,"running",str(i))
    assert (await read(fixture)).json()["supported"] is False
