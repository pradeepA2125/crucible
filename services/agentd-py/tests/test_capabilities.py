"""On-demand capability loading: catalog split, bundles and loaded state (Part B)."""
from __future__ import annotations

import json

import pytest

from agentd.capabilities.bundle import render_bundle, select_tools
from agentd.capabilities.catalog import render_directory, split_definitions, tool_version
from agentd.capabilities.history import guidance_visible, loaded_versions, unloaded_reason


def _d(name: str, description: str = "") -> dict[str, object]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": {}}}


def _bundle_result(namespace: str, defs: list[dict[str, object]], *, guidance: str | None = None,
                   is_error: bool = False) -> dict[str, object]:
    return {"role": "tool_result", "tool": "load_capabilities", "is_error": is_error,
            "content": render_bundle(namespace, defs, guidance)}


BROWSER = [
    _d("mcp__pw__browser_click", "Click an element on the page"),
    _d("mcp__pw__browser_type", "Type text into an editable element"),
    _d("mcp__pw__browser_snapshot", "Capture an accessibility snapshot of the page"),
]


def test_split_routes_mcp_tools_to_their_server():
    split = split_definitions([_d("read_file"), _d("mcp__web__fetch"), *BROWSER])
    assert [d["name"] for d in split.core] == ["read_file"]
    assert sorted(split.namespaces) == ["mcp.pw", "mcp.web"]
    assert [d["name"] for d in split.namespaces["mcp.pw"]] == [d["name"] for d in BROWSER]


def test_directory_lines():
    many = [_d(f"mcp__big__t{i:02d}") for i in range(12)]
    text = render_directory({"mcp.web": [_d("mcp__web__fetch")], "mcp.big": many})
    assert text.splitlines() == [
        "- mcp.big (12 tools): t00, t01, t02, t03, t04, t05, t06, t07, t08, t09, +2 more",
        "- mcp.web (1 tools): fetch",
    ]
    assert render_directory({}) == ""


def test_select_all_names_and_errors():
    assert select_tools(BROWSER, query=None, tool_names=None, limit=4) == BROWSER
    picked = select_tools(BROWSER, query=None, tool_names=["browser_type", "browser_click"],
                          limit=4)
    assert [d["name"] for d in picked] == ["mcp__pw__browser_type", "mcp__pw__browser_click"]
    with pytest.raises(KeyError, match="browser_drag"):
        select_tools(BROWSER, query=None, tool_names=["browser_drag"], limit=4)


def test_select_by_query_ranks_and_limits():
    ranked = select_tools(BROWSER, query="click a button on the page", tool_names=None, limit=4)
    assert ranked[0]["name"] == "mcp__pw__browser_click"
    assert len(select_tools(BROWSER, query="page", tool_names=None, limit=1)) == 1
    assert select_tools(BROWSER, query="spreadsheet", tool_names=None, limit=4) == []


def test_bundle_round_trips():
    bundle = json.loads(render_bundle("mcp.pw", BROWSER[:1], "Approvals apply."))
    assert bundle["kind"] == "capability_bundle" and bundle["namespace"] == "mcp.pw"
    assert bundle["tools"] == BROWSER[:1]
    assert bundle["versions"] == {"mcp__pw__browser_click": tool_version(BROWSER[0])}
    assert bundle["guidance"] == "Approvals apply."
    assert bundle["effective_from"] == "next_model_request"


def test_loaded_versions_from_history():
    history = [
        {"role": "user", "content": "go"},
        _bundle_result("mcp.pw", BROWSER[:1]),
        _bundle_result("mcp.pw", BROWSER[1:2], is_error=True),
        {"role": "tool_result", "tool": "load_capabilities", "content": "Already loaded: x"},
    ]
    assert loaded_versions(history) == {"mcp__pw__browser_click": tool_version(BROWSER[0])}
    changed = {**BROWSER[0], "description": "new"}
    history.append(_bundle_result("mcp.pw", [changed]))
    assert loaded_versions(history)["mcp__pw__browser_click"] == tool_version(changed)


def test_guidance_visible():
    assert not guidance_visible([_bundle_result("mcp.pw", BROWSER)])
    assert guidance_visible([_bundle_result("mcp.pw", BROWSER, guidance="g")])


def test_changed_schema_is_not_loaded():
    old, new = BROWSER[0], {**BROWSER[0], "description": "Click, now with modifiers"}
    by_name = {new["name"]: new}
    loaded = {old["name"]: tool_version(old)}
    reason = unloaded_reason("mcp__pw__browser_click", by_name, loaded)
    assert reason is not None and "schema changed" in reason and '"mcp.pw"' in reason


def test_unloaded_reason_cases():
    by_name = {d["name"]: d for d in BROWSER}
    assert unloaded_reason("read_file", by_name, {}) is None
    assert unloaded_reason("mcp__gone__x", by_name, {}) is None  # McpToolSource refuses it
    reason = unloaded_reason("mcp__pw__browser_type", by_name, {})
    assert reason == ('`mcp__pw__browser_type` is not loaded. Call load_capabilities with '
                      'namespace "mcp.pw" first, alone in its step.')
    loaded = {"mcp__pw__browser_type": tool_version(BROWSER[1])}
    assert unloaded_reason("mcp__pw__browser_type", by_name, loaded) is None
