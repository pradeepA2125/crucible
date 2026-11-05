"""The injection eval's fixture MCP server, through the production MCP client."""
from __future__ import annotations

import json
import sys

import pytest

from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader
from evals.injection.scenarios import MCP_CONTENT_FILE, mcp_config, read_sink


@pytest.mark.asyncio
async def test_fixture_server_serves_content_and_logs_sinks(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "0")  # no trust needed for this check
    ws = tmp_path / "ws"
    (ws / ".crucible").mkdir(parents=True)
    (ws / ".crucible" / "mcp.json").write_text(json.dumps(mcp_config(sys.executable, ws)))
    (ws / MCP_CONTENT_FILE).write_text(json.dumps({"issues": {"42": "body"}, "pages": {}}))
    manager = McpConnectionManager(McpConfigLoader(ws))
    try:
        await manager.start()
        names = {d.name.removeprefix("mcp__tracker__") for d in manager.tool_definitions()}
        assert {"get_issue", "fetch_page", "send_message", "create_gist"} <= names
        issue = await manager.call_tool("tracker", "get_issue", {"number": 42})
        assert "body" in issue.content[0].text
        missing = await manager.call_tool("tracker", "fetch_page", {"url": "https://nowhere"})
        assert "not found" in missing.content[0].text
        await manager.call_tool("tracker", "send_message", {"to": "a", "text": "b"})
        assert read_sink(ws) == [{"tool": "send_message", "args": {"to": "a", "text": "b"}}]
    finally:
        await manager.shutdown()
