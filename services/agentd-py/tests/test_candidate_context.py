"""Discovery metadata stays bounded, source-free and correctly linked."""
import json
import time
from unittest.mock import Mock

import pytest
from discovery_helpers import _workspace

from agentd.retrieval import artifact_client
from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.retrieval.candidates import MAX_CHARS, render_candidates


def test_candidate_encoding_has_fixed_headers_and_only_links_between_delivered_rows():
    items = [{"path": f"src/file{i}.py",
              "symbols": [{"name": "run", "kind": "Function", "line": 12}],
              "ranges": [[12, 40]], "excerpt": "never send this source"}
             for i in range(20)]
    relationships = [{"from": "src/file0.py", "to": f"src/file{i}.py", "kind": "imports"}
                     for i in range(1, 20)]
    text = render_candidates(items, relationships, query="Trace the flow")
    data = json.loads(text)
    assert data["symbol_columns"] == ["name", "kind", "line"]
    assert len(data["candidates"]) == 8 and len(data["relations"]) == 6
    assert all(0 <= endpoint < 8 for link in data["relations"] for endpoint in link[:2])
    assert len(text) <= MAX_CHARS
    assert "never send this source" not in text and '"name"' in text
    assert text.count('"name"') == 1


def test_candidate_character_budget_never_slices_a_row_or_breaks_references():
    items = [{"path": f"src/file{i}.py", "symbols": [
        {"name": "large" * 300, "kind": "Function", "line": 12}]} for i in range(8)]
    text = render_candidates(items, [], query="Trace")
    data = json.loads(text)
    assert 0 < len(data["candidates"]) < 8 and len(text) <= MAX_CHARS
    assert all(len(row[1][0][0]) == 1500 for row in data["candidates"])


def test_explicit_relative_paths_survive_index_misses_without_reading_or_inventing_locations():
    text = render_candidates([], [], query="Compare src/new.py and ui/view.ts")
    assert json.loads(text)["candidates"] == [["src/new.py", [], []], ["ui/view.ts", [], []]]
    assert render_candidates([], [], query="Visit https://host/src/new.py and ../src/new.py") == ""


def test_discovery_skips_source_extraction_and_repository_tree(tmp_path, monkeypatch):
    _workspace(tmp_path)
    client = RetrievalArtifactClient()
    monkeypatch.setattr(
        client, "_build_planner_evidence", Mock(side_effect=AssertionError("source")))
    monkeypatch.setattr(artifact_client.os, "walk", Mock(side_effect=AssertionError("tree")))
    context, _ = client.load_context(str(tmp_path), "auth invoice", discovery_only=True)
    assert {row["path"] for row in context.discovery_candidates} == {"auth.py", "invoice.py"}
    assert context.repository_structure == [] and context.file_outlines == {}
    assert not context.planner_evidence.evidence_files and context.file_contents == {}
    empty, _ = client.load_context(str(tmp_path), "nonexistentword", discovery_only=True)
    assert not empty.discovery_candidates and not empty.related_files


def test_discovery_preserves_real_direction_and_filters_relation_endpoints(tmp_path):
    snapshot = tmp_path / ".crucible/index-snapshot.json"
    snapshot.parent.mkdir()
    snapshot.write_text(json.dumps({
        "workspace_root": str(tmp_path), "generated_at_ms": int(time.time() * 1000),
        "graph": {"nodes": [
            {"id": "auth", "path": "auth.py", "name": "auth.py", "kind": "File"},
            {"id": "cache", "path": "cache.py", "name": "cache.py", "kind": "File"},
        ], "edges": [{"from": "auth", "to": "cache", "kind": "Imports"}]},
    }))
    context, _ = RetrievalArtifactClient().load_context(str(tmp_path), "auth", discovery_only=True)
    text = render_candidates(
        context.discovery_candidates, context.graph_relationships, query="auth")
    data = json.loads(text)
    source, target, kind = data["relations"][0]
    assert data["candidates"][source][0] == "auth.py"
    assert data["candidates"][target][0] == "cache.py" and kind == "imports"


def test_tests_do_not_take_discovery_slots_unless_requested(tmp_path):
    _workspace(tmp_path)
    path = tmp_path / ".crucible/index-snapshot.json"
    snapshot = json.loads(path.read_text())
    snapshot["graph"]["nodes"].insert(0, {
        "id": "test", "path": "tests/test_auth.py", "name": "auth", "kind": "Function",
    })
    client = RetrievalArtifactClient()
    context = client._build_context(snapshot, "auth", 0, str(tmp_path), discovery_only=True)
    assert "tests/test_auth.py" not in context.related_files
    context = client._build_context(snapshot, "test auth", 0, str(tmp_path), discovery_only=True)
    assert "tests/test_auth.py" in context.related_files


@pytest.mark.parametrize(("query", "wants_tests"), [
    ("latest auth change", False), ("inspect auth", False), ("auth specifics", False),
    ("contest auth", False), ("auth tests", True), ("pytest auth", True),
    ("auth spec", True), ("testing auth", True),
])
def test_only_whole_word_test_requests_bring_test_files_back(tmp_path, query, wants_tests):
    # "latest", "inspect" and "specific" contain "test"/"spec" but ask nothing about tests.
    _workspace(tmp_path)
    path = tmp_path / ".crucible/index-snapshot.json"
    snapshot = json.loads(path.read_text())
    snapshot["graph"]["nodes"].insert(0, {
        "id": "test", "path": "tests/test_auth.py", "name": "auth", "kind": "Function",
    })
    context = RetrievalArtifactClient()._build_context(
        snapshot, query, 0, str(tmp_path), discovery_only=True)
    assert ("tests/test_auth.py" in context.related_files) is wants_tests
