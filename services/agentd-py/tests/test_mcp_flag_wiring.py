"""CRUCIBLE_MCP_ENABLED: default OFF; ON builds the manager into the controller.
Route: POST /chat/threads/{id}/mcp-decision resolves via the handler's resolve_mcp."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller_factory import is_mcp_enabled, select_chat_handler
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_MCP_ENABLED", raising=False)
    assert is_mcp_enabled() is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False),
])
def test_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("CRUCIBLE_MCP_ENABLED", raw)
    assert is_mcp_enabled() is expected


def _handler(tmp_path, monkeypatch):
    from agentd.chat.storage import ChatThreadStore
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "1")
    return select_chat_handler(
        workspace_path=str(tmp_path),
        transport=object(), model="m",
        thread_store=ChatThreadStore(tmp_path / "c.sqlite3"),
        orchestrator=None, broadcaster=object())


def test_factory_off_no_manager(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CRUCIBLE_MCP_ENABLED", raising=False)
    assert _handler(tmp_path, monkeypatch)._mcp_manager is None


def test_factory_on_builds_manager(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_MCP_ENABLED", "1")
    from agentd.mcp.client import McpConnectionManager
    handler = _handler(tmp_path, monkeypatch)
    assert isinstance(handler._mcp_manager, McpConnectionManager)


class _StubChatHandler:
    """Only what the chat route registration + this route touch."""
    def __init__(self):
        self.calls = []
        self._store = None
        self._broadcaster = None

    async def resolve_mcp(self, thread_id, decision):
        self.calls.append((thread_id, decision))
        return True


@pytest.mark.asyncio
async def test_mcp_decision_route(tmp_path: Path):
    stub = _StubChatHandler()
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=stub))
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post("/v1/chat/threads/th1/mcp-decision",
                                 json={"approve": True, "remember": True})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    (thread_id, decision), = stub.calls
    assert thread_id == "th1" and decision.approve is True and decision.remember is True
