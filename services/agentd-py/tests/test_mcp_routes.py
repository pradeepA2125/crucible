import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.routes import build_router
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _StubSession:
    async def list_tools(self):
        class _R:
            tools = [type("T", (), {"name": "t1", "description": "", "inputSchema": {}})()]

        return _R()


@asynccontextmanager
async def _stub_factory(cfg):
    yield _StubSession()


class _TwoToolSession:
    async def list_tools(self):
        class _R:
            tools = [type("T", (), {"name": n, "description": f"does {n}", "inputSchema": {}})()
                     for n in ("a", "b")]

        return _R()


@asynccontextmanager
async def _two_tool_factory(cfg):
    yield _TwoToolSession()


def _client(tmp_path: Path, with_manager: bool = True, factory=_stub_factory) -> TestClient:
    manager = None
    if with_manager:
        manager = McpConnectionManager(
            McpConfigLoader(tmp_path), session_factory=factory
        )
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(),
            object(),
            ShadowWorkspaceManager(tmp_path / "shadows"),
            None,
            None,
            mcp_manager=manager,
        )
    )
    return TestClient(app)


def test_get_disabled_when_no_manager(tmp_path: Path) -> None:
    body = _client(tmp_path, with_manager=False).get("/v1/mcp/servers").json()
    assert body == {"enabled": False, "servers": []}
    resp = _client(tmp_path, with_manager=False).put(
        "/v1/mcp/servers/web", json={"entry": {"command": "x", "enabled": True}}
    )
    assert resp.status_code == 409


def test_put_writes_file_connects_and_lists(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.put(
        "/v1/mcp/servers/web",
        json={
            "entry": {"command": "uv", "args": ["run", "x.py"], "enabled": True},
            "disabled": [],
        },
    ).json()
    assert body["enabled"] is True
    (web,) = [s for s in body["servers"] if s["name"] == "web"]
    assert web["state"] == "connected" and web["tool_count"] == 1
    raw = json.loads((tmp_path / ".crucible" / "mcp.json").read_text())
    assert raw["mcpServers"]["web"]["command"] == "uv"


def test_disabled_entry_listed_but_not_connected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.put(
        "/v1/mcp/servers/off", json={"entry": {"command": "x", "enabled": False}}
    ).json()
    (off,) = [s for s in body["servers"] if s["name"] == "off"]
    assert off["enabled_in_file"] is False and off["state"] == "not_connected"


def test_delete_and_reconnect(tmp_path: Path) -> None:
    # Context-manager form: reconnect/delete await serve tasks spawned by the
    # earlier PUT, so all requests must share one event loop (lifespan portal).
    with _client(tmp_path) as client:
        client.put(
            "/v1/mcp/servers/web", json={"entry": {"command": "uv", "enabled": True}}
        )
        assert client.post("/v1/mcp/servers/web/reconnect", json={}).status_code == 200
        body = client.request("DELETE", "/v1/mcp/servers/web", json={}).json()
        assert all(s["name"] != "web" for s in body["servers"])
        assert client.request("DELETE", "/v1/mcp/servers/web", json={}).status_code == 404


def _seed_web(tmp_path: Path) -> None:
    path = tmp_path / ".crucible" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"web": {"command": "x", "enabled": True}}}))


def test_put_tools_filters_and_lists_every_tool(tmp_path: Path) -> None:
    _seed_web(tmp_path)
    # One event loop for both requests: the PUT's reconcile awaits serve tasks the
    # reconnect spawned (same reason as test_delete_and_reconnect).
    with _client(tmp_path, factory=_two_tool_factory) as client:
        client.post("/v1/mcp/servers/web/reconnect", json={"disabled": []})
        resp = client.put("/v1/mcp/servers/web/tools",
                          json={"disabled_tools": ["b"], "disabled": []})
    assert resp.status_code == 200
    (web,) = [s for s in resp.json()["servers"] if s["name"] == "web"]
    assert web["tools"] == [{"name": "a", "description": "does a", "enabled": True},
                            {"name": "b", "description": "does b", "enabled": False}]
    assert web["tool_count"] == 1
    assert web["enabled_tools"] is None and web["disabled_tools"] == ["b"]


def test_listing_shows_no_tools_for_unconnected_server(tmp_path: Path) -> None:
    _seed_web(tmp_path)
    (web,) = _client(tmp_path, factory=_two_tool_factory).get("/v1/mcp/servers").json()["servers"]
    assert web["tools"] == [] and web["enabled_tools"] is None and web["disabled_tools"] == []


def test_put_tools_unknown_server_404(tmp_path: Path) -> None:
    _seed_web(tmp_path)
    resp = _client(tmp_path).put("/v1/mcp/servers/nope/tools", json={"disabled_tools": []})
    # The route's own 404, not FastAPI's "Not Found" for a missing route.
    assert resp.status_code == 404 and resp.json()["detail"] == "no MCP server named 'nope'"


def test_put_tools_when_mcp_disabled_409(tmp_path: Path) -> None:
    resp = _client(tmp_path, with_manager=False).put(
        "/v1/mcp/servers/web/tools", json={"disabled_tools": []})
    assert resp.status_code == 409


def test_malformed_filter_is_listed_safely_with_a_reason(tmp_path: Path) -> None:
    # The loader skips this server; the listing must still be valid for the client's schema
    # (lists of strings or null) and say why the server is not connected.
    path = tmp_path / ".crucible" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"web": {
        "command": "x", "enabled": True, "enabled_tools": "a", "disabled_tools": ["b", 1]}}}))
    (web,) = _client(tmp_path).get("/v1/mcp/servers").json()["servers"]
    assert web["enabled_tools"] is None and web["disabled_tools"] == []
    assert web["detail"] == "enabled_tools/disabled_tools must be lists of tool names"
