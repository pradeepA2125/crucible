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


# --- Task 8: the load_capabilities tool -------------------------------------------------

from agentd.capabilities.bundle import MAX_BUNDLE_CHARS  # noqa: E402
from agentd.capabilities.tool_source import (  # noqa: E402
    LOAD_CAPABILITIES,
    CapabilitySession,
    CapabilityToolSource,
)
from agentd.tools.batching import Call, ExecutionPool, run_batch  # noqa: E402


def _source(history: list[dict[str, object]] | None = None, guidance: str = "Approvals apply."):
    namespaces = {"mcp.pw": list(BROWSER), "mcp.web": [_d("mcp__web__fetch")]}
    session = CapabilitySession(namespaces=namespaces,
                                history=history if history is not None else [],
                                guidance=guidance)
    return session, CapabilityToolSource(session)


def test_loader_definition_shape():
    params = LOAD_CAPABILITIES.parameters
    assert LOAD_CAPABILITIES.name == "load_capabilities"
    assert params["required"] == ["namespace"]
    assert params["additionalProperties"] is False
    assert set(params["properties"]) == {"namespace", "query", "tool_names", "limit"}
    _, source = _source()
    assert source.owns("load_capabilities") and not source.owns("read_file")
    assert [d.name for d in source.definitions()] == ["load_capabilities"]


@pytest.mark.asyncio
async def test_unknown_namespace_lists_known():
    _, source = _source()
    out = await source.execute("load_capabilities", {"namespace": "mcp.nope"})
    assert out.is_error and "mcp.pw" in out.output and "mcp.web" in out.output


@pytest.mark.asyncio
async def test_query_and_names_together_is_an_error():
    _, source = _source()
    out = await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "query": "click", "tool_names": ["browser_click"]})
    assert out.is_error


@pytest.mark.asyncio
async def test_missing_names_are_named():
    _, source = _source()
    out = await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "tool_names": ["browser_click", "browser_drag"]})
    assert out.is_error and "browser_drag" in out.output and "browser_click" not in out.output


@pytest.mark.asyncio
async def test_no_query_match_is_not_an_error():
    _, source = _source()
    out = await source.execute("load_capabilities", {"namespace": "mcp.pw", "query": "zebra"})
    assert not out.is_error
    assert out.output == ("No tools in mcp.pw match that query; try other words or "
                          "tool_names.")


@pytest.mark.asyncio
async def test_whole_server_bundle_with_guidance_once():
    session, source = _source()
    out = await source.execute("load_capabilities", {"namespace": "mcp.pw"})
    assert not out.is_error and out.whole
    bundle = json.loads(out.output)
    assert [d["name"] for d in bundle["tools"]] == [d["name"] for d in BROWSER]
    assert bundle["guidance"] == "Approvals apply."
    session.history.append({"role": "tool_result", "tool": "load_capabilities",
                            "is_error": False, "content": out.output})
    again = await source.execute("load_capabilities", {"namespace": "mcp.web"})
    assert json.loads(again.output)["guidance"] is None


@pytest.mark.asyncio
async def test_query_respects_limit():
    _, source = _source()
    out = await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "query": "page element", "limit": 1})
    assert len(json.loads(out.output)["tools"]) == 1


@pytest.mark.asyncio
async def test_already_loaded_is_a_receipt():
    history = [_bundle_result("mcp.pw", BROWSER[:1])]
    _, source = _source(history)
    out = await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "tool_names": ["browser_click"]})
    assert not out.is_error and not out.whole
    assert out.output == ("Already loaded: browser_click. Their schemas are earlier in this "
                          "conversation.")


@pytest.mark.asyncio
async def test_changed_schema_reloads():
    old = {**BROWSER[0], "description": "old"}
    _, source = _source([_bundle_result("mcp.pw", [old])])
    out = await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "tool_names": ["browser_click"]})
    assert json.loads(out.output)["tools"] == [BROWSER[0]]


@pytest.mark.asyncio
async def test_bundle_too_large_activates_nothing():
    huge = [_d(f"mcp__big__t{i}", "x" * 5000) for i in range(8)]
    session = CapabilitySession(namespaces={"mcp.big": huge}, history=[], guidance="")
    out = await CapabilityToolSource(session).execute("load_capabilities", {"namespace": "mcp.big"})
    assert out.is_error and "bundle_too_large" in out.output
    assert "capability_bundle" not in out.output


@pytest.mark.asyncio
async def test_bad_limit_and_names_count_are_errors():
    _, source = _source()
    assert (await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "query": "click", "limit": 9})).is_error
    assert (await source.execute("load_capabilities", {
        "namespace": "mcp.pw", "tool_names": [f"t{i}" for i in range(9)]})).is_error


@pytest.mark.asyncio
async def test_bundle_survives_small_result_budget():
    tools = [_d(f"mcp__big__t{i}", "y" * 3700) for i in range(8)]
    session = CapabilitySession(namespaces={"mcp.big": tools}, history=[], guidance="")
    source = CapabilityToolSource(session)
    probe = await source.execute("load_capabilities", {"namespace": "mcp.big"})
    assert 28_000 < len(probe.output) <= MAX_BUNDLE_CHARS

    async def prepare(call):
        async def run():
            return await source.execute(call.tool, call.args)
        return run

    async def update(outcome):
        pass

    (result,) = await run_batch(
        [Call("a", "load_capabilities", {"namespace": "mcp.big"})], prepare=prepare,
        resources=lambda c: (), pool=ExecutionPool(1), update=update, budget=16_000)
    assert result.output.output == probe.output
