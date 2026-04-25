"""Tests for SemanticIndex — skipped automatically when lancedb/sentence-transformers absent."""
from __future__ import annotations

import pytest

pytest.importorskip("lancedb", reason="lancedb not installed; skipping semantic index tests")
pytest.importorskip(
    "sentence_transformers", reason="sentence-transformers not installed; skipping semantic index tests"
)

from pathlib import Path

from agentd.retrieval.chunker import CodeChunker, ScoredChunk
from agentd.retrieval.semantic_index import IndexStats, SemanticIndex


# ── Snapshot helpers ──────────────────────────────────────────────────────────


def _node(node_id: str, path: str, name: str, kind: str, line: int) -> dict[str, object]:
    return {"id": node_id, "path": path, "name": name, "kind": kind, "line": line}


def _snapshot(nodes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "generated_at_ms": 1_000_000,
        "graph": {"nodes": nodes, "edges": []},
    }


def _write_file(workspace: Path, rel: str, content: str) -> None:
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_is_ready_before_build(tmp_path: Path) -> None:
    idx = SemanticIndex(index_path=tmp_path / "idx")
    assert idx.is_ready() is False


def test_build_or_update_returns_stats(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(workspace, "auth.py", "def login(user: str) -> bool:\n    return True\n")

    snapshot = _snapshot([
        _node("function:file:auth.py:login", "auth.py", "login", "Function", 1),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    stats = idx.build_or_update(workspace, snapshot)

    assert isinstance(stats, IndexStats)
    assert stats.total_chunks == 1
    assert stats.updated_files == 1
    assert idx.is_ready()


def test_query_returns_scored_chunks(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(
        workspace, "token.py",
        "def validate_token(tok: str) -> bool:\n    \"\"\"Validate a JWT token.\"\"\"\n    return bool(tok)\n",
    )

    snapshot = _snapshot([
        _node("function:file:token.py:validate_token", "token.py", "validate_token", "Function", 1),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    idx.build_or_update(workspace, snapshot)

    results = idx.query("JWT token authentication validation", top_k=5)
    assert len(results) >= 1
    for r in results:
        assert isinstance(r, ScoredChunk)
        assert 0.0 <= r.score <= 1.0


def test_query_returns_empty_when_not_ready(tmp_path: Path) -> None:
    idx = SemanticIndex(index_path=tmp_path / "idx")
    results = idx.query("something")
    assert results == []


def test_delta_indexing_skips_unchanged_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(workspace, "util.py", "def helper() -> None:\n    pass\n")

    snapshot = _snapshot([
        _node("function:file:util.py:helper", "util.py", "helper", "Function", 1),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    first = idx.build_or_update(workspace, snapshot)
    assert first.updated_files == 1

    # Same snapshot, same mtime → should skip all files
    second = idx.build_or_update(workspace, snapshot)
    assert second.updated_files == 0
    assert second.skipped_files == 1


def test_delta_indexing_reindexes_modified_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    src = workspace / "util.py"
    _write_file(workspace, "util.py", "def helper() -> None:\n    pass\n")

    snapshot = _snapshot([
        _node("function:file:util.py:helper", "util.py", "helper", "Function", 1),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    idx.build_or_update(workspace, snapshot)

    # Touch the file to change mtime
    import time
    time.sleep(0.01)
    src.write_text("def helper() -> None:\n    \"\"\"Updated.\"\"\"\n    pass\n", encoding="utf-8")

    second = idx.build_or_update(workspace, snapshot)
    assert second.updated_files == 1


def test_chunks_for_file_filters_by_path(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(workspace, "a.py", "def foo() -> None:\n    pass\n")
    _write_file(workspace, "b.py", "def bar() -> None:\n    pass\n")

    snapshot = _snapshot([
        _node("function:file:a.py:foo", "a.py", "foo", "Function", 1),
        _node("function:file:b.py:bar", "b.py", "bar", "Function", 1),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    idx.build_or_update(workspace, snapshot)

    results = idx.chunks_for_file("a.py", query="foo function", top_k=5)
    assert all(r.chunk.path == "a.py" for r in results)


def test_path_filter_in_query(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(workspace, "a.py", "def foo() -> None:\n    pass\n")
    _write_file(workspace, "b.py", "def bar() -> None:\n    pass\n")

    snapshot = _snapshot([
        _node("function:file:a.py:foo", "a.py", "foo", "Function", 1),
        _node("function:file:b.py:bar", "b.py", "bar", "Function", 1),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    idx.build_or_update(workspace, snapshot)

    results = idx.query("function", top_k=10, path_filter=["a.py"])
    assert all(r.chunk.path == "a.py" for r in results)


def test_exclude_tests_filter(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(workspace, "core.py", "def process() -> None:\n    pass\n")
    _write_file(workspace, "tests/test_core.py", "def test_process() -> None:\n    pass\n")

    snapshot = _snapshot([
        _node("function:file:core.py:process", "core.py", "process", "Function", 1),
        _node(
            "function:file:tests/test_core.py:test_process",
            "tests/test_core.py", "test_process", "Function", 1,
        ),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    idx.build_or_update(workspace, snapshot)

    results = idx.query("process", top_k=10, exclude_tests=True)
    assert all("test" not in r.chunk.path for r in results)


def test_kind_filter(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_file(workspace, "m.py", "class Foo:\n    def method(self) -> None:\n        pass\n")

    snapshot = _snapshot([
        _node("class:file:m.py:Foo", "m.py", "Foo", "Class", 1),
        _node("method:file:m.py:Foo:method:2", "m.py", "method", "Method", 2),
    ])

    idx = SemanticIndex(index_path=tmp_path / "idx")
    idx.build_or_update(workspace, snapshot)

    results = idx.query("foo method", top_k=10, kind_filter=["Class"])
    assert all(r.chunk.kind == "Class" for r in results)
