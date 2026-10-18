from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient
from test_discovery_cache import fixture
from test_index_build_api import _make_app

from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.retrieval.discovery_warmer import DiscoveryWarmer


def test_prepares_only_static_data_and_next_query_reuses_decode_and_lookups(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    semantic = SimpleNamespace(query=Mock(return_value=[]), is_ready=lambda: True)
    client = RetrievalArtifactClient(snapshot_path_template=str(path), semantic_index=semantic)
    original_build = client._build_context
    client._build_context = Mock(side_effect=AssertionError("must not score a fake query"))
    client._build_planner_evidence = Mock(side_effect=AssertionError("must not read source"))
    reads = []
    original_read = Path.read_text

    def read(p, *args, **kwargs):
        reads.append(p)
        return original_read(p, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    status = client.warm_discovery(str(tmp_path))
    assert status["state"] == "ready" and status["warmup_count"] == 1
    assert status["node_count"] == status["edge_count"] == 3
    semantic.query.assert_not_called()
    client._build_context = original_build
    prepared = client._discovery_path_cache.discovery
    client._normalize_snapshot_path = Mock(side_effect=AssertionError("already normalized"))
    context, _ = client.load_context(str(tmp_path), "auth", discovery_only=True, allow_build=False)
    assert context.related_files[0] == "auth.py"
    assert reads == [path] and client._discovery_path_cache.discovery is prepared
    assert client.warm_discovery(str(tmp_path))["warmup_count"] == 1


def test_full_planner_warmup_does_not_evict_discovery_cache(tmp_path):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    client.warm_discovery(str(tmp_path))
    discovery = client._discovery_path_cache
    client._fill_path_cache(payload, str(tmp_path))
    assert client._path_cache is not discovery
    client.load_context(str(tmp_path), "token", discovery_only=True, allow_build=False)
    assert client._discovery_path_cache is discovery


def test_inflight_query_for_old_revision_does_not_evict_new_warm_graph(tmp_path):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    older_payload = client._discovery_snapshot.load(path)
    payload["graph"]["nodes"][0]["name"] = "new!"
    path.write_text(json.dumps(payload))
    client.warm_discovery(str(tmp_path))
    newer_cache = client._discovery_path_cache
    client._build_context(older_payload, "auth", 0, str(tmp_path), discovery_only=True)
    assert client._discovery_path_cache is newer_cache
    assert client.discovery_status()["state"] == "ready"


def test_atomic_replacement_with_same_generation_and_mtime_refreshes_cache(tmp_path):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    client.warm_discovery(str(tmp_path))
    old = path.stat()
    payload["graph"]["nodes"][0]["name"] = "new!"
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(payload))
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    replacement.replace(path)
    assert client.discovery_status()["state"] == "pending"
    assert client.warm_discovery(str(tmp_path))["warmup_count"] == 2
    context, _ = client.load_context(str(tmp_path), "new", discovery_only=True, allow_build=False)
    assert context.discovery_candidates[0]["symbols"][0]["name"] == "new!"


@pytest.mark.parametrize("failure", ["delete", "corrupt"])
def test_invalid_snapshot_drops_prepared_cache_and_does_not_serve_old_data(tmp_path, failure):
    path, _ = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    client.warm_discovery(str(tmp_path))
    if failure == "delete":
        path.unlink()
    else:
        path.write_text("broken")
    assert client.warm_discovery(str(tmp_path))["state"] in {"missing", "unavailable"}
    assert client._discovery_path_cache is None
    context, _ = client.load_context(str(tmp_path), "auth", discovery_only=True, allow_build=False)
    assert context.discovery_candidates == []


def test_change_during_preparation_is_not_published_ready(tmp_path):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    original = client._normalize_snapshot_path
    changed = False

    def normalize(**kwargs):
        nonlocal changed
        if not changed:
            changed = True
            payload["graph"]["nodes"][0]["name"] = "new!"
            path.write_text(json.dumps(payload))
        return original(**kwargs)

    client._normalize_snapshot_path = normalize
    assert client.warm_discovery(str(tmp_path))["state"] == "unavailable"
    assert client.warm_discovery(str(tmp_path))["state"] == "ready"
    context, _ = client.load_context(str(tmp_path), "new", discovery_only=True, allow_build=False)
    assert context.discovery_candidates[0]["symbols"][0]["name"] == "new!"


def test_concurrent_warmers_publish_one_version_and_decode_once(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    original_read = Path.read_text
    reads = []

    def read(p, *args, **kwargs):
        reads.append(p)
        return original_read(p, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: client.warm_discovery(str(tmp_path)), range(8)))
    assert all(s["state"] == "ready" and s["warmup_count"] == 1 for s in statuses)
    assert reads == [path]


def test_oversized_graph_is_not_retained_for_background_warmup(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    monkeypatch.setenv("CRUCIBLE_DISCOVERY_CACHE_MAX_MB", "0")
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    assert client.warm_discovery(str(tmp_path))["state"] == "uncacheable"
    assert client._discovery_path_cache is None and client._discovery_snapshot._payload is None


def test_worker_warms_startup_and_changes_without_notifications_and_stops(tmp_path):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    warmed = threading.Event()
    original = client.warm_discovery

    def warm(workspace):
        status = original(workspace)
        warmed.set()
        return status

    client.warm_discovery = warm
    worker = DiscoveryWarmer(client, str(tmp_path), poll_seconds=.01, debounce_seconds=.01)
    try:
        worker.start()
        assert warmed.wait(2) and client.discovery_status()["warmup_count"] == 1
        warmed.clear()
        payload["graph"]["nodes"][0]["name"] = "new!"
        replacement = tmp_path / "replacement.json"
        replacement.write_text(json.dumps(payload))
        replacement.replace(path)
        assert warmed.wait(2) and client.discovery_status()["warmup_count"] == 2
    finally:
        worker.stop()
    assert not worker._thread.is_alive()


def test_burst_notifications_coalesce_and_reach_latest_version():
    entered, release, complete = threading.Event(), threading.Event(), threading.Event()
    versions = []

    class Client:
        version = 0

        def snapshot_version(self, workspace):
            return self.version

        def warm_discovery(self, workspace):
            versions.append(self.version)
            if len(versions) == 1:
                entered.set()
                release.wait(2)
            else:
                complete.set()
            return {"state": "ready"}

    client = Client()
    worker = DiscoveryWarmer(client, "workspace", poll_seconds=.1, debounce_seconds=.01)
    try:
        worker.start()
        assert entered.wait(2)
        for i in range(1, 21):
            client.version = i
            worker.notify()
        release.set()
        assert complete.wait(2)
    finally:
        release.set()
        worker.stop()
    assert versions == [0, 20]


@pytest.mark.asyncio
async def test_build_notification_warms_discovery_with_semantic_models_disabled(tmp_path):
    path, _ = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    warmed = threading.Event()
    original = client.warm_discovery

    def warm(workspace):
        status = original(workspace)
        warmed.set()
        return status

    client.warm_discovery = warm
    async with AsyncClient(transport=ASGITransport(app=_make_app(client)),
                           base_url="http://test") as http:
        response = await http.post("/v1/index/build", json={"workspace_path": str(tmp_path)})
        assert response.status_code == 202
        assert await asyncio.to_thread(warmed.wait, 2)
        status = (await http.get("/v1/index/status")).json()
    assert not status["semantic_enabled"] and status["discovery"]["state"] == "ready"
