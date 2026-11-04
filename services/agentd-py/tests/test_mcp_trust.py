"""An untrusted .crucible/mcp.json connects nothing (prompt-injection Phase 1, spec §4.1).

The managed backend always enables MCP, so before Phase 1 a cloned repository's enabled stdio
server ran, unsandboxed, the moment the project opened."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentd.mcp.admin import set_tool_filters, upsert_server
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader
from agentd.trust.project_trust import ProjectTrust
from tests.test_mcp_routes import _client, _stub_factory


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    (workspace / ".crucible").mkdir(parents=True)
    return workspace


def _plant(ws: Path, marker: Path) -> Path:
    path = ws / ".crucible" / "mcp.json"
    script = f"open({str(marker)!r}, 'w').write('ran')"
    path.write_text(json.dumps({"mcpServers": {"evil": {
        "command": sys.executable, "args": ["-c", script], "enabled": True}}}))
    return path


def _trust(ws: Path) -> None:
    trust = ProjectTrust(ws)
    trust.trust("mcp", trust.fingerprint("mcp"))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_untrusted_mcp_json_connects_nothing_at_start(ws: Path, tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    _plant(ws, marker)
    loader = McpConfigLoader(ws)
    assert loader.load() == []
    manager = McpConnectionManager(loader)
    await manager.start()
    assert manager.statuses() == []
    await manager.shutdown()
    assert not marker.exists()


@pytest.mark.asyncio
async def test_trusting_mcp_json_connects_without_restart(ws: Path, tmp_path: Path) -> None:
    _plant(ws, tmp_path / "marker")
    loader = McpConfigLoader(ws)
    manager = McpConnectionManager(loader, session_factory=_stub_factory)
    await manager.start()
    assert manager.statuses() == []
    _trust(ws)
    await manager.reconcile(loader.load(), disabled=manager.last_disabled)
    assert [s.name for s in manager.statuses()] == ["evil"]
    await manager.shutdown()


def test_settings_write_keeps_trusted_mcp_json_trusted(ws: Path) -> None:
    path = ws / ".crucible" / "mcp.json"
    upsert_server(path, "web", {"type": "http", "url": "https://x", "enabled": True})
    assert ProjectTrust(ws).state("mcp") == "trusted"  # created by the user's own write
    set_tool_filters(path, "web", None, ["b"])
    assert ProjectTrust(ws).state("mcp") == "trusted"
    path.write_text(path.read_text().replace("https://x", "https://evil"))
    upsert_server(path, "web2", {"type": "http", "url": "https://y", "enabled": True})
    assert ProjectTrust(ws).state("mcp") == "untrusted"  # a write on top of foreign bytes


def test_mcp_listing_marks_untrusted(ws: Path, tmp_path: Path) -> None:
    _plant(ws, tmp_path / "marker")
    body = _client(ws).get("/v1/mcp/servers").json()
    assert body["trusted"] is False
    assert [(s["name"], s["state"]) for s in body["servers"]] == [("evil", "untrusted")]
    _trust(ws)
    body = _client(ws).get("/v1/mcp/servers").json()
    assert body["trusted"] is True
    assert body["servers"][0]["state"] == "not_connected"
