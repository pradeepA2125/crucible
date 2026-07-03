import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from agentd.mcp.admin import read_raw_servers, remove_server, upsert_server
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader


def _seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "$schema": "https://example/schema.json",  # unknown top-level key
                "mcpServers": {
                    "web": {
                        "command": "uv",
                        "args": ["run", "x.py"],
                        "env": {"OLLAMA_API_KEY": "${OLLAMA_API_KEY}"},
                        "enabled": True,
                        "x-custom": 1,
                    },
                },
            }
        )
    )


def test_upsert_preserves_unknown_keys_and_var_refs(tmp_path: Path) -> None:
    cfg = tmp_path / ".ai-editor" / "mcp.json"
    _seed(cfg)
    upsert_server(
        cfg,
        "gh",
        {
            "type": "http",
            "url": "https://x",
            "headers": {"Authorization": "${GITHUB_PAT}"},
            "enabled": True,
        },
    )
    raw = json.loads(cfg.read_text())
    assert raw["$schema"] == "https://example/schema.json"
    assert raw["mcpServers"]["web"]["x-custom"] == 1
    assert raw["mcpServers"]["web"]["env"]["OLLAMA_API_KEY"] == "${OLLAMA_API_KEY}"
    assert raw["mcpServers"]["gh"]["headers"]["Authorization"] == "${GITHUB_PAT}"


def test_upsert_creates_file_and_rejects_bad_name(tmp_path: Path) -> None:
    cfg = tmp_path / ".ai-editor" / "mcp.json"
    upsert_server(cfg, "a1", {"command": "x", "enabled": True})
    assert "a1" in read_raw_servers(cfg)
    with pytest.raises(ValueError):
        upsert_server(cfg, "bad__name", {"command": "x"})


def test_remove_server(tmp_path: Path) -> None:
    cfg = tmp_path / ".ai-editor" / "mcp.json"
    _seed(cfg)
    assert remove_server(cfg, "web") is True
    assert remove_server(cfg, "web") is False
    assert read_raw_servers(cfg) == {}


class _StubSession:
    async def list_tools(self):
        class _R:  # noqa: N801
            tools: list = []

        return _R()


@asynccontextmanager
async def _stub_factory(cfg):
    yield _StubSession()


@pytest.mark.asyncio
async def test_reconcile_disabled_filters_and_reconnect(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".ai-editor" / "mcp.json"
    _seed(cfg_path)
    upsert_server(cfg_path, "gh", {"type": "http", "url": "https://x", "enabled": True})
    loader = McpConfigLoader(tmp_path)
    manager = McpConnectionManager(loader, session_factory=_stub_factory)
    await manager.reconcile(loader.load(), disabled=frozenset({"gh"}))
    states = {s.name: s.state for s in manager.statuses()}
    assert "web" in states and "gh" not in states

    await manager.reconnect("web")
    assert {s.name for s in manager.statuses()} >= {"web"}
    await manager.shutdown()
