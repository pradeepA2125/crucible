"""McpConnectionManager: per-server background serve tasks (the SDK's anyio cancel
scopes must enter/exit in the same task), reconcile() diffing, degrade-not-raise."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from agentd.mcp.client import McpConnectionManager, McpServerUnavailable, McpToolNotExposed
from agentd.mcp.models import McpServerConfig


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"desc of {name}"
        self.inputSchema = {"type": "object", "properties": {"q": {"type": "string"}}}


class _FakeListResult:
    def __init__(self, tools):
        self.tools = tools


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeCallResult:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.content = [_FakeBlock(text)]
        self.isError = is_error


class _FakeSession:
    def __init__(self, tools: list[str]) -> None:
        self._tools = [_FakeTool(t) for t in tools]
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return _FakeListResult(self._tools)

    async def call_tool(self, name: str, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        return _FakeCallResult(f"ran {name}")


def _factory(sessions: dict[str, _FakeSession]):
    @asynccontextmanager
    async def open_session(cfg: McpServerConfig):
        if cfg.name == "broken":
            raise RuntimeError("handshake failed")
        yield sessions[cfg.name]
    return open_session


def _cfg(name: str, **filters) -> McpServerConfig:
    return McpServerConfig(name=name, transport="stdio", command="x", enabled=True, **filters)


class _StaticLoader:
    def __init__(self, configs):
        self._configs = configs

    def load(self):
        return self._configs


@pytest.mark.asyncio
async def test_connects_and_exposes_namespaced_tools():
    sessions = {"echo": _FakeSession(["say", "shout"])}
    mgr = McpConnectionManager(_StaticLoader([_cfg("echo")]), session_factory=_factory(sessions))
    await mgr.start()
    names = [d.name for d in mgr.tool_definitions()]
    assert names == ["mcp__echo__say", "mcp__echo__shout"]
    st = {s.name: s for s in mgr.statuses()}
    assert st["echo"].state == "connected" and st["echo"].tool_count == 2
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_failed_server_contributes_zero_tools_without_raising():
    sessions = {"ok": _FakeSession(["t"])}
    mgr = McpConnectionManager(
        _StaticLoader([_cfg("broken"), _cfg("ok")]), session_factory=_factory(sessions))
    await mgr.start()  # must NOT raise
    assert [d.name for d in mgr.tool_definitions()] == ["mcp__ok__t"]
    st = {s.name: s for s in mgr.statuses()}
    assert st["broken"].state == "failed" and "handshake failed" in st["broken"].detail
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_call_tool_routes_to_right_session():
    sessions = {"a": _FakeSession(["t"]), "b": _FakeSession(["t"])}
    mgr = McpConnectionManager(
        _StaticLoader([_cfg("a"), _cfg("b")]), session_factory=_factory(sessions))
    await mgr.start()
    result = await mgr.call_tool("b", "t", {"q": "hi"})
    assert result.content[0].text == "ran t"
    assert sessions["b"].calls == [("t", {"q": "hi"})] and sessions["a"].calls == []
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_call_tool_on_unknown_or_failed_server_raises_unavailable():
    mgr = McpConnectionManager(
        _StaticLoader([_cfg("broken")]), session_factory=_factory({}))
    await mgr.start()
    with pytest.raises(McpServerUnavailable):
        await mgr.call_tool("broken", "t", {})
    with pytest.raises(McpServerUnavailable):
        await mgr.call_tool("never-configured", "t", {})
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_reconcile_stops_removed_and_starts_new():
    sessions = {"a": _FakeSession(["t"]), "b": _FakeSession(["t"])}
    mgr = McpConnectionManager(_StaticLoader([_cfg("a")]), session_factory=_factory(sessions))
    await mgr.start()
    assert [d.name for d in mgr.tool_definitions()] == ["mcp__a__t"]
    await mgr.reconcile([_cfg("b")])  # a removed, b added — the P4 UI seam
    assert [d.name for d in mgr.tool_definitions()] == ["mcp__b__t"]
    st = {s.name: s for s in mgr.statuses()}
    assert set(st) == {"b"}
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_shutdown_disconnects_everything():
    sessions = {"a": _FakeSession(["t"])}
    mgr = McpConnectionManager(_StaticLoader([_cfg("a")]), session_factory=_factory(sessions))
    await mgr.start()
    await mgr.shutdown()
    assert mgr.tool_definitions() == []


@pytest.mark.asyncio
async def test_real_stdio_round_trip():
    """Spec §7 integration test: real subprocess, real protocol — not the stub."""
    import sys
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    cfg = McpServerConfig(
        name="echo", transport="stdio",
        command=sys.executable, args=[str(fixture)], enabled=True)
    mgr = McpConnectionManager(_StaticLoader([cfg]))  # default (real) session factory
    await mgr.start()
    try:
        assert [d.name for d in mgr.tool_definitions()] == ["mcp__echo__echo"]
        result = await mgr.call_tool("echo", "echo", {"text": "hi"})
        texts = [getattr(b, "text", "") for b in result.content]
        assert any("echo: hi" in t for t in texts)
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,disabled,visible", [
    (None, [], ["say", "shout", "whisper"]),
    (["say", "shout"], [], ["say", "shout"]),
    (None, ["shout"], ["say", "whisper"]),
    (["say", "shout"], ["shout"], ["say"]),
    ([], [], []),
])
async def test_filters_decide_visible_tools(enabled, disabled, visible):
    sessions = {"echo": _FakeSession(["say", "shout", "whisper"])}
    cfg = _cfg("echo", enabled_tools=enabled, disabled_tools=disabled)
    mgr = McpConnectionManager(_StaticLoader([cfg]), session_factory=_factory(sessions))
    await mgr.start()
    assert [d.name for d in mgr.tool_definitions()] == [f"mcp__echo__{t}" for t in visible]
    assert {s.name: s for s in mgr.statuses()}["echo"].tool_count == len(visible)
    inventory = mgr.tool_inventory()["echo"]
    assert [t.name for t in inventory] == ["say", "shout", "whisper"]
    assert [t.name for t in inventory if t.enabled] == visible
    assert inventory[0].description == "desc of say"
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_unknown_filter_names_warn_once(caplog):
    sessions = {"echo": _FakeSession(["say", "shout"])}
    cfg = _cfg("echo", enabled_tools=["say", "gone"], disabled_tools=["nope"])
    mgr = McpConnectionManager(_StaticLoader([cfg]), session_factory=_factory(sessions))
    await mgr.start()
    warnings = [r.getMessage() for r in caplog.records if "not offered" in r.getMessage()]
    assert len(warnings) == 1 and "gone" in warnings[0] and "nope" in warnings[0]
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_call_tool_refuses_filtered_tool():
    sessions = {"echo": _FakeSession(["say", "shout"])}
    cfg = _cfg("echo", disabled_tools=["shout"])
    mgr = McpConnectionManager(_StaticLoader([cfg]), session_factory=_factory(sessions))
    await mgr.start()
    assert mgr.is_exposed("echo", "say") and not mgr.is_exposed("echo", "shout")
    assert not mgr.is_exposed("missing", "say")
    with pytest.raises(McpToolNotExposed):
        await mgr.call_tool("echo", "shout", {})
    assert sessions["echo"].calls == []
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_filter_change_applies_without_reconnecting():
    # Toggling a tool in Settings must not restart the server: for Playwright that
    # would close the live browser and fail in-flight calls.
    sessions = {"echo": _FakeSession(["say", "shout"])}
    opened = []
    inner = _factory(sessions)

    @asynccontextmanager
    async def counting(cfg):
        opened.append(cfg.name)
        async with inner(cfg) as session:
            yield session

    mgr = McpConnectionManager(_StaticLoader([_cfg("echo")]), session_factory=counting)
    await mgr.start()
    await mgr.reconcile([_cfg("echo", disabled_tools=["shout"])])
    assert [d.name for d in mgr.tool_definitions()] == ["mcp__echo__say"]
    assert [t.name for t in mgr.tool_inventory()["echo"] if t.enabled] == ["say"]
    await mgr.reconcile([_cfg("echo", enabled_tools=["shout"])])
    assert [d.name for d in mgr.tool_definitions()] == ["mcp__echo__shout"]
    assert {s.name: s for s in mgr.statuses()}["echo"].tool_count == 1
    assert opened == ["echo"]
    # A connection-level change still reconnects.
    await mgr.reconcile([McpServerConfig(name="echo", transport="stdio", command="y",
                                         enabled=True)])
    assert opened == ["echo", "echo"]
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_refusal_names_the_real_reason():
    # A down server is "not connected", not "unknown tool": the model must not conclude
    # a working tool does not exist. Filtered or unlisted tools on a live server are unknown.
    sessions = {"echo": _FakeSession(["say", "shout"])}
    cfg = _cfg("echo", disabled_tools=["shout"])
    mgr = McpConnectionManager(_StaticLoader([cfg, _cfg("broken")]),
                               session_factory=_factory(sessions))
    await mgr.start()
    assert mgr.refusal("echo", "say") is None
    assert mgr.refusal("echo", "shout") == "unknown tool 'mcp__echo__shout'"
    assert mgr.refusal("echo", "nope") == "unknown tool 'mcp__echo__nope'"
    assert mgr.refusal("broken", "x").startswith("MCP server 'broken' is not connected (failed:")
    assert mgr.refusal("missing", "x") == "MCP server 'missing' is not connected"
    await mgr.shutdown()
    assert mgr.refusal("echo", "say") == "MCP server 'echo' is not connected"


@pytest.mark.asyncio
async def test_keeps_each_connected_servers_instructions():
    sessions = {"a": _FakeSession(["x"]), "b": _FakeSession(["y"])}
    sessions["a"].server_instructions = "Drives a headless browser.\nMore detail."
    mgr = McpConnectionManager(_StaticLoader([_cfg("a"), _cfg("b"), _cfg("broken")]),
                               session_factory=_factory(sessions))
    await mgr.start()
    assert mgr.server_instructions() == {"a": "Drives a headless browser.\nMore detail."}
    await mgr.shutdown()
    assert mgr.server_instructions() == {}
