"""Lifecycle failures without optional ML packages or downloaded models."""

from pathlib import Path

import pytest

from agentd.retrieval.semantic_index import SemanticIndex


class _Table:
    def __init__(self) -> None:
        self.rows = [{"path": "deleted.py", "file_mtime": 1.0}]

    def search(self):
        return self

    def select(self, _columns):
        return self

    def limit(self, _limit):
        return self

    def to_list(self):
        return list(self.rows)

    def delete(self, _predicate):
        self.rows.clear()

    def count_rows(self):
        return len(self.rows)


def test_empty_snapshot_removes_previously_indexed_chunks(tmp_path: Path, monkeypatch) -> None:
    table = _Table()
    index = SemanticIndex(tmp_path / "index")
    monkeypatch.setattr(index, "_get_or_create_table", lambda _path: table)
    stats = index.build_or_update(tmp_path, {"graph": {"nodes": [], "edges": []}})
    assert table.rows == [], "deleted code must not remain searchable when the last file disappears"
    assert stats.total_chunks == table.count_rows()


def test_failed_embedding_preserves_previous_chunks(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "deleted.py").write_text("def changed():\n    return 2\n")
    table = _Table()
    index = SemanticIndex(tmp_path / "index")
    monkeypatch.setattr(index, "_get_or_create_table", lambda _path: table)

    def fail(_texts):
        raise RuntimeError("injected embedder failure")

    monkeypatch.setattr(index, "_encode_batch", fail)
    snapshot = {
        "graph": {
            "nodes": [
                {
                    "id": "function:changed",
                    "path": "deleted.py",
                    "kind": "Function",
                    "name": "changed",
                    "line": 1,
                },
            ],
            "edges": [],
        }
    }
    with pytest.raises(RuntimeError, match="injected embedder failure"):
        index.build_or_update(tmp_path, snapshot)
    assert table.rows == [{"path": "deleted.py", "file_mtime": 1.0}]


def test_failed_deletion_is_not_reported_as_a_successful_build(tmp_path: Path, monkeypatch) -> None:
    table = _Table()
    index = SemanticIndex(tmp_path / "index")
    monkeypatch.setattr(index, "_get_or_create_table", lambda _path: table)

    def fail(_predicate):
        raise RuntimeError("injected deletion failure")

    monkeypatch.setattr(table, "delete", fail)
    with pytest.raises(RuntimeError, match="injected deletion failure"):
        index.build_or_update(tmp_path, {"graph": {"nodes": [], "edges": []}})
