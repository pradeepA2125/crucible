"""Snapshot-only retrieval work is cached per snapshot version.

Path normalization and the repository-structure walk depend only on the snapshot and the
file tree, not on the goal, yet ran on every new chat (~0.5 s on this repo). Only these
small results are kept — not the parsed graph, which would hold ~220 MB.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentd.retrieval import artifact_client
from agentd.retrieval.artifact_client import RetrievalArtifactClient


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/auth.py").write_text("def build_auth():\n    pass\n", encoding="utf-8")
    (workspace / "src/token.py").write_text("def check():\n    pass\n", encoding="utf-8")
    return workspace


def _payload(workspace: Path, generated_at_ms: int) -> dict[str, object]:
    return {
        "workspace_root": str(workspace),
        "generated_at_ms": generated_at_ms,
        "graph": {
            "nodes": [
                {"id": "file:auth", "path": "src/auth.py", "name": "auth.py", "kind": "File"},
                {"id": "fn:auth", "path": "src/auth.py", "name": "build_auth", "kind": "Function",
                 "line": 1},
                {"id": "fn:token", "path": str(workspace / "src/token.py"), "name": "check",
                 "kind": "Function", "line": 1},
            ],
            "edges": [{"from": "fn:auth", "to": "fn:token", "kind": "Calls"}],
        },
        "diagnostics": [{"file": "src/auth.py", "line": 1, "message": "unused"}],
    }


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    seen = {"normalize": 0, "walk": 0}
    original_normalize = RetrievalArtifactClient._normalize_snapshot_path
    original_walk = artifact_client.os.walk

    def normalize(self, **kwargs):  # type: ignore[no-untyped-def]
        seen["normalize"] += 1
        return original_normalize(self, **kwargs)

    def walk(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen["walk"] += 1
        return original_walk(*args, **kwargs)

    monkeypatch.setattr(RetrievalArtifactClient, "_normalize_snapshot_path", normalize)
    monkeypatch.setattr(artifact_client.os, "walk", walk)
    return seen


def test_a_second_chat_on_the_same_snapshot_reuses_paths_and_structure(
        tmp_path: Path, counts: dict[str, int]) -> None:
    workspace = _workspace(tmp_path)
    client = RetrievalArtifactClient()
    first = client._build_context(_payload(workspace, 1), "build auth", 0.0, str(workspace))
    after_first = dict(counts)

    second = client._build_context(_payload(workspace, 1), "check token", 0.0, str(workspace))

    assert after_first["normalize"] > 0 and after_first["walk"] == 1
    assert counts == after_first  # nothing snapshot-only ran again
    assert second.repository_structure == first.repository_structure
    assert "src/auth.py" in first.related_files
    assert "src/token.py" in second.related_files  # goal-dependent work still ran


def test_a_new_snapshot_version_recomputes(tmp_path: Path, counts: dict[str, int]) -> None:
    workspace = _workspace(tmp_path)
    client = RetrievalArtifactClient()
    client._build_context(_payload(workspace, 1), "build auth", 0.0, str(workspace))
    (workspace / "src/new.py").write_text("def fresh():\n    pass\n", encoding="utf-8")
    time.sleep(0.01)

    context = client._build_context(_payload(workspace, 2), "build auth", 0.0, str(workspace))

    assert counts["walk"] == 2
    assert any("3 source files" in line for line in context.repository_structure)


def test_cached_and_uncached_results_are_identical(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    warm = RetrievalArtifactClient()
    warm._build_context(_payload(workspace, 1), "anything", 0.0, str(workspace))

    cached = warm._build_context(_payload(workspace, 1), "build auth token", 0.0, str(workspace))
    fresh = RetrievalArtifactClient()._build_context(
        _payload(workspace, 1), "build auth token", 0.0, str(workspace))

    assert cached.as_prompt_payload() == fresh.as_prompt_payload()


def test_an_index_build_fills_the_cache_before_the_next_chat(
        tmp_path: Path, counts: dict[str, int]) -> None:
    """The indexer rewrites the snapshot after every edit; filling the cache in the build's
    background thread keeps the first chat after an edit from paying for it."""
    import json
    from unittest.mock import MagicMock

    workspace = _workspace(tmp_path)
    snapshot_path = workspace / ".crucible" / "index-snapshot.json"
    snapshot_path.parent.mkdir()
    snapshot_path.write_text(json.dumps(_payload(workspace, 7)), encoding="utf-8")
    semantic = MagicMock()
    semantic.is_ready.return_value = True
    semantic.query.return_value = []
    client = RetrievalArtifactClient(snapshot_path_template=str(snapshot_path),
                                     semantic_index=semantic)

    client.trigger_index_build(str(workspace))
    after_build = dict(counts)
    client.load_context(str(workspace), "build auth")

    assert after_build["walk"] == 1
    assert counts == after_build
