from __future__ import annotations

import dataclasses
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentd.retrieval import artifact_client
from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.retrieval.discovery_cache import DiscoverySnapshotCache


def fixture(tmp_path):
    for name in ("auth", "token", "other"):
        (tmp_path / f"{name}.py").write_text(f"def {name}(): pass\n")
    payload = {
        "workspace_root": str(tmp_path), "generated_at_ms": 1,
        "graph": {"nodes": [
            {"id": name, "path": f"{name}.py", "name": name, "kind": "Function", "line": 1}
            for name in ("auth", "token", "other")],
            "edges": [{"from": "auth", "to": "token", "kind": "Calls"},
                      {"from": "token", "to": "auth", "kind": "Imports"},
                      {"from": "token", "to": "other", "kind": "References"}]},
        "diagnostics": [{"message": "Large diagnostic payload is not discovery metadata"}],
    }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload))
    return path, payload


def test_discovery_reuses_decode_and_lookups_but_scores_each_query(tmp_path, monkeypatch):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    reads = []
    original = Path.read_text

    def read(self, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    first, _ = client.load_context(str(tmp_path), "auth", discovery_only=True, current_query="auth")
    prepared = client._discovery_path_cache.discovery
    second, _ = client.load_context(
        str(tmp_path), "token", discovery_only=True, current_query="token")
    assert reads == [path]
    assert client._discovery_path_cache.discovery is prepared
    assert first.related_files[0] == "auth.py" and second.related_files[0] == "token.py"
    for query, actual in [("auth", first), ("token", second)]:
        fresh = RetrievalArtifactClient()._build_context(
            payload, query, 0., str(tmp_path), discovery_only=True, current_query=query)
        assert dataclasses.replace(actual, snapshot_age_sec=0.) == fresh


@pytest.mark.parametrize("atomic", [False, True])
def test_same_generation_same_size_replacement_is_not_stale(tmp_path, atomic):
    path, payload = fixture(tmp_path)
    client = RetrievalArtifactClient(snapshot_path_template=str(path))
    client.load_context(str(tmp_path), "auth", discovery_only=True)
    old = path.stat()
    payload["graph"]["nodes"][0]["name"] = "new!"
    assert len(json.dumps(payload)) == path.stat().st_size
    if atomic:
        replacement = tmp_path / "replacement.json"
        replacement.write_text(json.dumps(payload))
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        replacement.replace(path)
    else:
        path.write_text(json.dumps(payload))
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    context, _ = client.load_context(str(tmp_path), "new", discovery_only=True)
    assert context.discovery_candidates[0]["symbols"][0]["name"] == "new!"


@pytest.mark.parametrize("failure", ["deleted", "corrupt", "array"])
def test_failed_load_never_reuses_old_snapshot(tmp_path, failure):
    path, _ = fixture(tmp_path)
    cache = DiscoverySnapshotCache()
    cache.load(path)
    if failure == "deleted":
        path.unlink()
    else:
        path.write_text("{" if failure == "corrupt" else "[]")
    with pytest.raises((OSError, ValueError)):
        cache.load(path)
    assert cache._payload is None and cache._key is None


def test_change_during_read_retries_before_publishing(tmp_path, monkeypatch):
    path, payload = fixture(tmp_path)
    original = Path.read_text
    count = 0

    def read(self, *args, **kwargs):
        nonlocal count
        text = original(self, *args, **kwargs)
        count += 1
        if count == 1:
            payload["graph"]["nodes"][0]["name"] = "fresh"
            path.write_text(json.dumps(payload))
        return text

    monkeypatch.setattr(Path, "read_text", read)
    value = DiscoverySnapshotCache().load(path)
    assert count == 2 and value["graph"]["nodes"][0]["name"] == "fresh"


def test_graph_over_the_memory_budget_uses_uncached_path(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    monkeypatch.setenv("CRUCIBLE_DISCOVERY_CACHE_MAX_MB", "0")
    cache = DiscoverySnapshotCache()
    first, second = cache.load(path), cache.load(path)
    assert first == second and first is not second
    assert cache._payload is None and "_discovery_cache_key" not in first


def test_budget_counts_memory_not_rows(tmp_path):
    # More rows than the old 100k-node cap, but small: cached.
    many = {"workspace_root": str(tmp_path), "graph": {"nodes": [
        {"id": f"n{i}", "path": "a.py", "name": "f", "kind": "Function", "line": 1}
        for i in range(100_001)], "edges": []}}
    path = tmp_path / "many.json"
    path.write_text(json.dumps(many))
    cache = DiscoverySnapshotCache()
    assert "_discovery_cache_key" in cache.load(path)
    # Three rows whose strings alone exceed a 1 MB budget: not cached.
    huge = {"workspace_root": str(tmp_path), "graph": {"nodes": [
        {"id": f"n{i}", "path": f"{i}" * 600_000, "name": "f", "kind": "Function"}
        for i in range(3)], "edges": []}}
    path.write_text(json.dumps(huge))
    small = DiscoverySnapshotCache(max_bytes=1_000_000)
    assert "_discovery_cache_key" not in small.load(path) and small._payload is None


@pytest.mark.parametrize(("raw", "expected"), [
    (None, 256 * 1024 * 1024), ("64", 64 * 1024 * 1024), ("0", 0),
    ("-1", 256 * 1024 * 1024), ("lots", 256 * 1024 * 1024), ("nan", 256 * 1024 * 1024),
])
def test_budget_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CRUCIBLE_DISCOVERY_CACHE_MAX_MB", raising=False)
    else:
        monkeypatch.setenv("CRUCIBLE_DISCOVERY_CACHE_MAX_MB", raw)
    assert DiscoverySnapshotCache().max_bytes == expected


def test_concurrent_readers_share_one_snapshot_decode(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    cache = DiscoverySnapshotCache()
    original = Path.read_text
    count = 0

    def read(self, *args, **kwargs):
        nonlocal count
        count += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: cache.load(path), range(16)))
    assert count == 1 and all(v is values[0] for v in values)


def test_cache_switches_snapshot_files_and_keeps_only_discovery_metadata(tmp_path):
    path, payload = fixture(tmp_path)
    cache = DiscoverySnapshotCache()
    first = cache.load(path)
    other = tmp_path / "other.json"
    payload["graph"]["nodes"][0]["name"] = "different"
    other.write_text(json.dumps(payload))
    second = cache.load(other)
    assert second is cache._payload and first is not second
    assert second["graph"]["nodes"][0]["name"] == "different"
    assert "diagnostics" not in second


def test_snapshot_age_is_recomputed_on_cache_hits(tmp_path, monkeypatch):
    path, payload = fixture(tmp_path)
    payload["generated_at_ms"] = 1000
    path.write_text(json.dumps(payload))
    client = RetrievalArtifactClient(snapshot_path_template=str(path), max_age_sec=10)
    monkeypatch.setattr(artifact_client.time, "time", lambda: 2.)
    first, warnings = client.load_context(str(tmp_path), "auth", discovery_only=True)
    assert first.snapshot_age_sec == 1. and not warnings
    monkeypatch.setattr(artifact_client.time, "time", lambda: 20.)
    second, warnings = client.load_context(str(tmp_path), "auth", discovery_only=True)
    assert second.snapshot_age_sec == 19. and any("stale" in w.message for w in warnings)


def test_compact_graph_preserves_duplicate_id_and_parallel_edge_behavior(tmp_path):
    path, payload = fixture(tmp_path)
    payload["graph"]["nodes"].append(
        {"id": "auth", "path": "other.py", "name": "auth_duplicate", "kind": "Function", "line": 1})
    payload["graph"]["edges"] += [
        {"from": "auth", "to": "token", "kind": "References"},
        {"from": "auth", "to": "auth", "kind": "Calls"},
        {"from": "auth", "to": "missing", "kind": "Calls"}]
    path.write_text(json.dumps(payload))
    cached = RetrievalArtifactClient(snapshot_path_template=str(path))
    for query in ("auth", "token", "other", "test auth"):
        actual, _ = cached.load_context(
            str(tmp_path), query, discovery_only=True, current_query=query)
        fresh = RetrievalArtifactClient()._build_context(
            payload, query, 0., str(tmp_path), discovery_only=True, current_query=query)
        assert dataclasses.replace(actual, snapshot_age_sec=0.) == fresh
