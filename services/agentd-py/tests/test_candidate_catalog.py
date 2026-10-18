"""Compact discovery catalogs preserve symbols, ranges and relationships."""
import json

from agentd.retrieval.candidates import format_catalog, render_candidates


def decode_rows(text):
    rows = []
    for line in text.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[0].isdigit():
            rows.append([json.loads(cell) for cell in parts[1:]])
    return rows


def test_text_catalog_preserves_all_symbol_range_and_relationship_metadata():
    candidates = [
        {"path": "src/a|b\n.py", "symbols": [
            {"name": "run", "kind": "Function", "line": 10},
            {"name": "Worker", "kind": "Class", "line": 40}],
         "ranges": [[10, 30], [40, 60]], "excerpt": "DO NOT SEND SOURCE"},
        {"path": "src/other.py", "symbols": [], "ranges": []},
    ]
    internal = render_candidates(candidates, [
        {"from": candidates[0]["path"], "to": candidates[1]["path"], "kind": "Calls"}
    ], query="work")
    catalog = format_catalog(internal)
    assert decode_rows(catalog) == json.loads(internal)["candidates"]
    assert '[0,1,"Calls"]' in catalog
    assert "read_file" in catalog and "query_graph" in catalog and "search_semantic" in catalog
    assert "DO NOT SEND SOURCE" not in catalog
    assert format_catalog("") == format_catalog('{"candidates":[]}') == ""


def test_catalog_budget_keeps_complete_rows_and_no_dangling_relationships():
    data = render_candidates([
        {"path": "a.py", "symbols": [], "ranges": []},
        {"path": "b" * 600 + ".py", "symbols": [], "ranges": []},
    ], [{"from": "a.py", "to": "b" * 600 + ".py", "kind": "Imports"}], query="work")
    text = format_catalog(data, max_chars=300)
    assert len(text) <= 300 and decode_rows(text) == [["a.py", [], []]]
    assert "relations[" not in text
    assert format_catalog(data, max_chars=10) == ""
