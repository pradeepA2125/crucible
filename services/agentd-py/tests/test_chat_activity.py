"""Fresh reactive authority over real SQLite/controller/routes; no providers run."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.exec_sessions.manager import SessionManager
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "0")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path))
    handler = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[]),
        thread_store=store,
        orchestrator=None,
        broadcaster=EventBroadcaster(),
        retrieval_client=None,
    )
    app = FastAPI()
    orch = SimpleNamespace(broadcaster=handler._broadcaster, _running_tasks=set())
    app.include_router(build_router(InMemoryTaskStore(), orch, None, None, handler))
    yield app, handler, store, thread.thread_id, tmp_path
    store._conn.close()


async def read(fixture, workspace=None, nonce="request-1"):
    app, _, _, _, root = fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(
            "/v1/chat/activity",
            params={
                "workspace": str(root) if workspace is None else workspace,
                "request_id": nonce,
            },
        )


def agent(store, thread_id, status, identity="agent-1", depth=1):
    store._conn.execute(
        "INSERT INTO chat_agents (agent_id,thread_id,turn_id,depth,name,label,prompt,status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (identity, thread_id, "turn", depth, "a", "a", "p", status),
    )
    store._conn.commit()


def sessions(handler, thread_id, count=1):
    manager = object.__new__(SessionManager)
    proc = SimpleNamespace(is_running=lambda: True, exit_code=lambda: None)
    manager._sessions = {
        str(i): SimpleNamespace(
            session_id=str(i),
            thread_id=thread_id,
            command_line="display only",
            proc=proc,
            started_at=1,
        )
        for i in range(count)
    }
    handler._exec_sessions = manager
    return manager


@pytest.mark.asyncio
async def test_complete_project_process_nonce_binding_and_no_store(fixture):
    _, handler, _, tid, root = fixture
    response = await read(fixture)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "version": 1,
        "supported": True,
        "request_id": "request-1",
        "requested_workspace": str(root),
        "workspace_path": str(root.resolve()),
        "process_id": handler.context_process_id(),
        "threads": [
            {"thread_id": tid, "turn_active": False, "agents_running": 0, "sessions_running": 0}
        ],
    }


@pytest.mark.asyncio
async def test_all_main_descendant_and_command_activity_is_explicit(fixture):
    _, handler, store, tid, _ = fixture
    handler._active_turns[tid] = asyncio.current_task()
    for index, status in enumerate(("queued", "running", "waiting", "completed", "stopped")):
        agent(store, tid, status, str(index), depth=index)
    manager = sessions(handler, tid, 2)
    before = list(store._conn.iterdump())
    body = (await read(fixture)).json()
    assert body["threads"] == [
        {"thread_id": tid, "turn_active": True, "agents_running": 3, "sessions_running": 2}
    ]
    assert list(store._conn.iterdump()) == before
    assert "thread_id" not in manager.live_summaries(tid)[0]  # old per-thread shape
    handler._active_turns.clear()


@pytest.mark.asyncio
async def test_aliases_included_other_projects_excluded_without_reading_transcripts(fixture):
    _, _, store, tid, root = fixture
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    same = store.create_thread(str(alias))
    store.create_thread(str(root / "other"))
    store._conn.execute("UPDATE chat_threads SET messages_json='not-json'")
    store._conn.commit()
    before = list(store._conn.iterdump())
    response = await read(fixture, str(alias))
    assert response.json()["supported"] is True
    assert {t["thread_id"] for t in response.json()["threads"]} == {tid, same.thread_id}
    assert list(store._conn.iterdump()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,reason",
    [
        ("wrong-project", "different_project"),
        ("task-mode", "legacy_task_activity"),
        ("legacy-work", "legacy_task_activity"),
        ("legacy", "legacy_backend"),
        ("missing-process", "unknown_process"),
        ("unbound-turn", "unbound_activity"),
        ("orphan-session", "unbound_session"),
        ("unknown-session", "unknown_session"),
        ("unknown-manager", "unknown_session_manager"),
        ("session-limit", "session_limit"),
        ("unknown-agent", "unknown_agent"),
        ("orphan-agent", "unavailable"),
        ("thread-limit", "thread_limit"),
        ("metadata", "unknown_project_metadata"),
    ],
)
async def test_incomplete_or_unsupported_evidence_is_never_idle(fixture, case, reason):
    _, handler, store, tid, root = fixture
    workspace = str(root)
    if case == "wrong-project":
        workspace = str(root / "other")
    elif case == "task-mode":
        handler._task_subsystem_enabled = True
    elif case == "legacy-work":
        handler._orchestrator = SimpleNamespace(_running_tasks={"task"})
    elif case == "legacy":
        handler.project_activity = None
    elif case == "missing-process":
        handler.context_process_id = lambda: ""
    elif case == "unbound-turn":
        handler._active_turns["missing"] = asyncio.current_task()
    elif case == "orphan-session":
        sessions(handler, "missing")
    elif case == "unknown-session":
        sessions(handler, tid).live_summaries = lambda _: [{"status": "future"}]
    elif case == "unknown-manager":
        handler._exec_sessions = object()
    elif case == "session-limit":
        sessions(handler, tid, 1025)
    elif case == "unknown-agent":
        agent(store, tid, "future")
    elif case == "orphan-agent":
        agent(store, "missing", "queued")
    elif case == "metadata":
        store._conn.execute("UPDATE chat_threads SET workspace_path=''")
        store._conn.commit()
    elif case == "thread-limit":
        store._conn.executemany(
            "INSERT INTO chat_threads (thread_id,workspace_path,created_at) VALUES (?,?,?)",
            [(str(i), str(root), "2026-10-23T00:00:00Z") for i in range(1000)],
        )
        store._conn.commit()
    body = (await read(fixture, workspace)).json()
    assert body == {
        "version": 1,
        "supported": False,
        "reason": reason,
        "requested_workspace": workspace,
        "request_id": "request-1",
    }
    handler._active_turns.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workspace,nonce", [("", "r"), ("x", ""), ("x", "invalid /"), ("x", "a" * 129)]
)
async def test_invalid_identity_does_not_read_activity(fixture, workspace, nonce):
    _, handler, _, _, _ = fixture
    handler.project_activity = lambda _: pytest.fail("invalid request reached activity reader")
    assert (await read(fixture, workspace, nonce)).status_code == 422
