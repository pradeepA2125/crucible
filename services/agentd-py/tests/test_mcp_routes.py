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


def _client(tmp_path: Path, with_manager: bool = True) -> TestClient:
    manager = None
    if with_manager:
        manager = McpConnectionManager(
            McpConfigLoader(tmp_path), session_factory=_stub_factory
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
    raw = json.loads((tmp_path / ".ai-editor" / "mcp.json").read_text())
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
