"""Tests for POST /v1/index/build and RetrievalArtifactClient.trigger_index_build."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.storage.in_memory import InMemoryTaskStore


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_retrieval_client(*, semantic_index: object = None) -> RetrievalArtifactClient:
    return RetrievalArtifactClient(semantic_index=semantic_index)


def _make_app(retrieval_client: RetrievalArtifactClient | None) -> FastAPI:
    store = InMemoryTaskStore()
    orchestrator = MagicMock(spec=AgentOrchestrator)
    orchestrator.broadcaster = MagicMock()
    orchestrator._running_tasks = set()
    workspace_manager = MagicMock()
    router = build_router(store, orchestrator, workspace_manager, retrieval_client)
    app = FastAPI()
    app.include_router(router)
    return app


# ── Unit tests: trigger_index_build ───────────────────────────────────────────


def test_trigger_index_build_no_semantic_index():
    """Returns None immediately when semantic retrieval is disabled."""
    client = _make_retrieval_client(semantic_index=None)
    result = client.trigger_index_build("/some/workspace")
    assert result is None


def test_trigger_index_build_missing_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Returns None when the snapshot is missing and the auto-indexer can't produce one."""
    fake_index = MagicMock()
    client = RetrievalArtifactClient(
        snapshot_path_template=str(tmp_path / "{workspace}" / "snapshot.json"),
        semantic_index=fake_index,
    )
    attempts: list[str] = []
    monkeypatch.setattr(
        client, "_attempt_build_snapshot", lambda ws, sp: attempts.append(ws) or []
    )
    result = client.trigger_index_build(str(tmp_path / "workspace"))
    assert result is None
    assert attempts == [str(tmp_path / "workspace")]
    fake_index.build_or_update.assert_not_called()


def test_trigger_index_build_missing_snapshot_runs_auto_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The Build-index CTA path: a missing snapshot is auto-built, then embedded."""
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    payload = {"generated_at_ms": 42, "graph": {"nodes": [], "edges": []}}

    fake_index = MagicMock()
    fake_index.build_or_update.return_value = "stats"
    client = RetrievalArtifactClient(
        snapshot_path_template=str(snapshot_path),
        semantic_index=fake_index,
    )

    def fake_auto_index(ws: str, sp: Path) -> list:
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(payload), encoding="utf-8")
        return []

    monkeypatch.setattr(client, "_attempt_build_snapshot", fake_auto_index)
    result = client.trigger_index_build(str(tmp_path))
    assert result == "stats"
    fake_index.build_or_update.assert_called_once_with(str(tmp_path), payload)


def test_trigger_index_build_calls_build_or_update(tmp_path: Path):
    """Calls build_or_update with parsed snapshot payload and returns stats."""
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    payload = {"schema_version": 1, "generated_at_ms": 1000, "graph": {"nodes": [], "edges": []}}
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    fake_stats = object()
    fake_index = MagicMock()
    fake_index.build_or_update.return_value = fake_stats

    client = RetrievalArtifactClient(
        snapshot_path_template=str(tmp_path / ".ai-editor" / "index-snapshot.json"),
        semantic_index=fake_index,
    )
    result = client.trigger_index_build(str(tmp_path))

    assert result is fake_stats
    fake_index.build_or_update.assert_called_once_with(str(tmp_path), payload)


def test_trigger_index_build_updates_last_indexed_ms(tmp_path: Path):
    """_last_indexed_snapshot_ms is updated so load_context skips a redundant rebuild."""
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    payload = {"generated_at_ms": 9999, "graph": {"nodes": [], "edges": []}}
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    fake_index = MagicMock()
    fake_index.build_or_update.return_value = None

    client = RetrievalArtifactClient(
        snapshot_path_template=str(tmp_path / ".ai-editor" / "index-snapshot.json"),
        semantic_index=fake_index,
    )
    assert client._last_indexed_snapshot_ms == 0
    client.trigger_index_build(str(tmp_path))
    assert client._last_indexed_snapshot_ms == 9999


def test_trigger_index_build_building_flag_cleared_on_success(tmp_path: Path):
    """_building is False after a successful build."""
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps({"generated_at_ms": 1}), encoding="utf-8")

    fake_index = MagicMock()
    fake_index.build_or_update.return_value = None

    client = RetrievalArtifactClient(
        snapshot_path_template=str(snapshot_path),
        semantic_index=fake_index,
    )
    client.trigger_index_build(str(tmp_path))
    assert client._building is False


def test_trigger_index_build_building_flag_cleared_on_exception(tmp_path: Path):
    """_building is False even when build_or_update raises (finally block)."""
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps({"generated_at_ms": 1}), encoding="utf-8")

    fake_index = MagicMock()
    fake_index.build_or_update.side_effect = RuntimeError("boom")

    client = RetrievalArtifactClient(
        snapshot_path_template=str(snapshot_path),
        semantic_index=fake_index,
    )
    client.trigger_index_build(str(tmp_path))
    assert client._building is False


def test_index_status_semantic_disabled():
    """index_status reports semantic_enabled=False when no index is configured."""
    client = _make_retrieval_client(semantic_index=None)
    status = client.index_status()
    assert status == {"semantic_enabled": False, "building": False, "last_indexed_snapshot_ms": 0}


def test_index_status_semantic_enabled():
    """index_status reports semantic_enabled=True and building=False when idle."""
    client = _make_retrieval_client(semantic_index=MagicMock())
    status = client.index_status()
    assert status["semantic_enabled"] is True
    assert status["building"] is False


def test_trigger_index_build_swallows_exception(tmp_path: Path):
    """Exceptions from build_or_update are caught and None is returned (non-fatal)."""
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps({"generated_at_ms": 1}), encoding="utf-8")

    fake_index = MagicMock()
    fake_index.build_or_update.side_effect = RuntimeError("embedding model unavailable")

    client = RetrievalArtifactClient(
        snapshot_path_template=str(tmp_path / ".ai-editor" / "index-snapshot.json"),
        semantic_index=fake_index,
    )
    result = client.trigger_index_build(str(tmp_path))
    assert result is None


# ── API tests: POST /v1/index/build ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_index_status_no_retrieval_client():
    """Returns semantic_enabled=False when retrieval_client was not passed to build_router."""
    app = _make_app(retrieval_client=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/index/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["semantic_enabled"] is False
        assert body["building"] is False


@pytest.mark.asyncio
async def test_get_index_status_semantic_disabled():
    """Returns semantic_enabled=False when client has no semantic index."""
    retrieval_client = _make_retrieval_client(semantic_index=None)
    app = _make_app(retrieval_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/index/status")
        assert resp.status_code == 200
        assert resp.json()["semantic_enabled"] is False


@pytest.mark.asyncio
async def test_get_index_status_idle():
    """Returns building=False and semantic_enabled=True when index is configured but not building."""
    retrieval_client = _make_retrieval_client(semantic_index=MagicMock())
    app = _make_app(retrieval_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/index/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["semantic_enabled"] is True
        assert body["building"] is False
        assert body["last_indexed_snapshot_ms"] == 0


@pytest.mark.asyncio
async def test_build_index_missing_workspace_path():
    """Returns 400 when workspace_path is absent."""
    retrieval_client = _make_retrieval_client(semantic_index=MagicMock())
    app = _make_app(retrieval_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/index/build", json={})
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_build_index_semantic_not_enabled():
    """Returns 503 when retrieval_client is None (semantic retrieval disabled)."""
    app = _make_app(retrieval_client=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/index/build", json={"workspace_path": "/tmp/ws"})
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_build_index_semantic_disabled_on_client():
    """Returns 503 when retrieval_client has no semantic index."""
    retrieval_client = _make_retrieval_client(semantic_index=None)
    app = _make_app(retrieval_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/index/build", json={"workspace_path": "/tmp/ws"})
        assert resp.status_code == 503
        assert "CRUCIBLE_SEMANTIC_RETRIEVAL" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_build_index_accepted():
    """Returns 202 with status=building when semantic retrieval is enabled."""
    retrieval_client = _make_retrieval_client(semantic_index=MagicMock())
    app = _make_app(retrieval_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/index/build", json={"workspace_path": "/tmp/my-workspace"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "building"
        assert body["workspace_path"] == "/tmp/my-workspace"


@pytest.mark.asyncio
async def test_build_index_fires_trigger_in_background():
    """trigger_index_build is called (via executor) when the route accepts the request."""
    fake_index = MagicMock()
    fake_index.build_or_update.return_value = None
    retrieval_client = _make_retrieval_client(semantic_index=fake_index)
    app = _make_app(retrieval_client)

    calls: list[str] = []

    original_trigger = retrieval_client.trigger_index_build

    def _capture(ws: str) -> object | None:
        calls.append(ws)
        return original_trigger(ws)

    retrieval_client.trigger_index_build = _capture  # type: ignore[method-assign]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/index/build", json={"workspace_path": "/tmp/ws"})
        assert resp.status_code == 202

    # Yield control so the background task runs
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert calls == ["/tmp/ws"]


# ── Status-during-build tests ─────────────────────────────────────────────────


def _make_snapshot(tmp_path: Path, generated_at_ms: int = 1000) -> Path:
    snapshot_path = tmp_path / ".ai-editor" / "index-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps({"generated_at_ms": generated_at_ms, "graph": {"nodes": [], "edges": []}}),
        encoding="utf-8",
    )
    return snapshot_path


def test_building_flag_true_while_build_or_update_running(tmp_path: Path):
    """_building is True while build_or_update is executing (observed from a second thread)."""
    snapshot_path = _make_snapshot(tmp_path)

    started = threading.Event()
    may_finish = threading.Event()
    observed_during: list[bool] = []

    def slow_build_or_update(*_: object) -> None:
        started.set()
        may_finish.wait(timeout=2)

    fake_index = MagicMock()
    fake_index.build_or_update.side_effect = slow_build_or_update

    client = RetrievalArtifactClient(
        snapshot_path_template=str(snapshot_path),
        semantic_index=fake_index,
    )

    t = threading.Thread(target=client.trigger_index_build, args=(str(tmp_path),))
    t.start()

    started.wait(timeout=2)
    observed_during.append(client._building)  # must be True here
    may_finish.set()
    t.join(timeout=2)

    assert observed_during == [True], "_building was not True while build_or_update was running"
    assert client._building is False, "_building was not cleared after build finished"


@pytest.mark.asyncio
async def test_status_api_building_true_then_false(tmp_path: Path):
    """GET /v1/index/status returns building=True while executor runs, then False after done."""
    snapshot_path = _make_snapshot(tmp_path)

    started = threading.Event()
    may_finish = threading.Event()

    def slow_build_or_update(*_: object) -> None:
        started.set()
        may_finish.wait(timeout=5)

    fake_index = MagicMock()
    fake_index.build_or_update.side_effect = slow_build_or_update

    retrieval_client = RetrievalArtifactClient(
        snapshot_path_template=str(snapshot_path),
        semantic_index=fake_index,
    )
    app = _make_app(retrieval_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        # Trigger the build (runs trigger_index_build in a thread via run_in_executor)
        resp = await http.post("/v1/index/build", json={"workspace_path": str(tmp_path)})
        assert resp.status_code == 202

        # Wait until build_or_update has actually started in the thread
        await asyncio.get_event_loop().run_in_executor(None, started.wait, 2)

        # Now the thread is blocked inside build_or_update — status must show building=True
        status_resp = await http.get("/v1/index/status")
        assert status_resp.json()["building"] is True

        # Let the build finish
        may_finish.set()

        # Drain the executor and background asyncio task
        for _ in range(5):
            await asyncio.sleep(0.05)

        # Status must now show building=False
        status_resp = await http.get("/v1/index/status")
        assert status_resp.json()["building"] is False
        assert status_resp.json()["last_indexed_snapshot_ms"] == 1000
