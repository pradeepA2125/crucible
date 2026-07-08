"""McpToolSource: ToolSource over connected MCP servers — mcp__<server>__<tool>
namespacing, order-truncation budget guard, approval gating, text flattening."""
from __future__ import annotations

import pytest

from agentd.mcp.tool_source import McpToolSource, parse_tool_name
from agentd.tools.registry import ToolDefinition


class _Block:
    def __init__(self, text=None):
        if text is not None:
            self.text = text


class _Result:
    def __init__(self, blocks, is_error=False):
        self.content = blocks
        self.isError = is_error


class _StubManager:
    def __init__(self, defs, results=None, raise_on_call=None):
        self._defs = defs
        self._results = results or {}
        self._raise = raise_on_call
        self.calls = []

    def tool_definitions(self):
        return self._defs

    async def call_tool(self, server, tool, args):
        self.calls.append((server, tool, args))
        if self._raise is not None:
            raise self._raise
        return self._results[(server, tool)]


def _def(name, desc="d"):
    return ToolDefinition(name=name, description=desc,
                          parameters={"type": "object", "properties": {}})


async def _approve(server, tool, args):
    return True


async def _reject(server, tool, args):
    return False


def test_parse_tool_name():
    assert parse_tool_name("mcp__gh__create_issue") == ("gh", "create_issue")
    assert parse_tool_name("mcp__gh__list__all") == ("gh", "list__all")  # tool keeps rest
    assert parse_tool_name("mcp__nope") is None
    assert parse_tool_name("read_file") is None


def test_owns_only_mcp_prefix():
    src = McpToolSource(_StubManager([]), _approve)
    assert src.owns("mcp__a__b") is True
    assert src.owns("read_file") is False


def test_definitions_pass_through_and_budget_truncates(monkeypatch):
    defs = [_def(f"mcp__s__t{i}", desc="x" * 200) for i in range(10)]
    src = McpToolSource(_StubManager(defs), _approve)
    assert len(src.definitions()) == 10
    monkeypatch.setenv("CRUCIBLE_MCP_TOOLS_MAX_CHARS", "700")
    kept = src.definitions()
    assert 0 < len(kept) < 10
    assert [d.name for d in kept] == [d.name for d in defs[: len(kept)]]  # order-truncation


@pytest.mark.asyncio
async def test_execute_approved_flattens_text_blocks():
    mgr = _StubManager([_def("mcp__gh__ci")],
                       results={("gh", "ci"): _Result([_Block("made #12"), _Block()])})
    out = await McpToolSource(mgr, _approve).execute("mcp__gh__ci", {"title": "t"})
    assert out.is_error is False
    assert "made #12" in out.output and "non-text" in out.output
    assert mgr.calls == [("gh", "ci", {"title": "t"})]


@pytest.mark.asyncio
async def test_execute_rejected_returns_error_without_calling():
    mgr = _StubManager([_def("mcp__gh__ci")])
    out = await McpToolSource(mgr, _reject).execute("mcp__gh__ci", {})
    assert out.is_error is True and "rejected" in out.output
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_execute_server_error_degrades_to_tool_error():
    mgr = _StubManager([_def("mcp__gh__ci")], raise_on_call=RuntimeError("server died"))
    out = await McpToolSource(mgr, _approve).execute("mcp__gh__ci", {})
    assert out.is_error is True and "server died" in out.output


@pytest.mark.asyncio
async def test_result_isError_maps_to_error_output():
    mgr = _StubManager([_def("mcp__gh__ci")],
                       results={("gh", "ci"): _Result([_Block("boom")], is_error=True)})
    out = await McpToolSource(mgr, _approve).execute("mcp__gh__ci", {})
    assert out.is_error is True and "boom" in out.output
