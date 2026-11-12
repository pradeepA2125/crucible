"""MCP secrets: values for ${VAR} references kept out of mcp.json and out of the
backend's own environment, supplied by the host at startup and from Settings."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.routes import build_router
from agentd.child_env import child_env
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader, McpMissingEnvVar, interpolate_env
from agentd.mcp.secrets import McpSecrets, secret_references
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def test_a_saved_secret_wins_over_the_environment_which_still_works(monkeypatch) -> None:
    secrets = McpSecrets()
    secrets.update({"GITHUB_PAT": "saved-token"})
    monkeypatch.setenv("GITHUB_PAT", "env-token")
    monkeypatch.setenv("OTHER", "from-env")
    assert interpolate_env({"Authorization": "Bearer ${GITHUB_PAT}", "X": "${OTHER}"}, secrets) == {
        "Authorization": "Bearer saved-token",
        "X": "from-env",
    }
    monkeypatch.delenv("MISSING", raising=False)
    actionable = r"No value for MISSING\. Set it in Settings › MCP Servers"
    with pytest.raises(McpMissingEnvVar, match=actionable):
        interpolate_env({"K": "${MISSING}"}, secrets)


def test_clearing_a_saved_secret_falls_back_to_the_environment(monkeypatch) -> None:
    secrets = McpSecrets()
    secrets.update({"TOKEN": "saved"})
    secrets.update({"TOKEN": None})
    monkeypatch.setenv("TOKEN", "env")
    assert interpolate_env({"K": "${TOKEN}"}, secrets) == {"K": "env"}
    assert secrets.names() == frozenset()


def test_startup_takes_the_secrets_from_the_host_and_removes_them_from_the_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CRUCIBLE_MCP_SECRETS", json.dumps({"GITHUB_PAT": "tok", "bad name": "x", "NUM": 3})
    )
    secrets = McpSecrets.from_environment()
    assert secrets.get("GITHUB_PAT") == "tok"
    # Invalid names and non-string values are dropped, never fatal.
    assert secrets.names() == frozenset({"GITHUB_PAT"})
    # Commands the agent runs inherit the backend environment: the map must not be in it.
    import os

    assert "CRUCIBLE_MCP_SECRETS" not in os.environ
    assert "CRUCIBLE_MCP_SECRETS" not in child_env({"CRUCIBLE_MCP_SECRETS": "{}", "PATH": "/bin"})


def test_malformed_startup_secrets_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_MCP_SECRETS", "not json")
    assert McpSecrets.from_environment().names() == frozenset()


def test_rejects_invalid_names_and_oversized_values() -> None:
    secrets = McpSecrets()
    with pytest.raises(ValueError, match="name"):
        secrets.update({"lower-case-dash": "x"})
    with pytest.raises(ValueError, match="too long"):
        secrets.update({"BIG": "x" * 65537})


def test_secret_references_lists_the_variables_an_entry_uses() -> None:
    entry = {
        "command": "uv",
        "env": {"A": "${GITHUB_PAT}", "B": "plain"},
        "headers": {"Authorization": "Bearer ${API_KEY} ${GITHUB_PAT}"},
    }
    assert secret_references(entry) == ["API_KEY", "GITHUB_PAT"]
    assert secret_references({"command": "x", "env": "not a dict"}) == []


class _StubSession:
    async def list_tools(self):
        class _R:
            tools = []

        return _R()


def _client(tmp_path: Path, secrets: McpSecrets, connects: list[str]) -> TestClient:
    @asynccontextmanager
    async def factory(cfg):
        connects.append(cfg.name)
        yield _StubSession()

    manager = McpConnectionManager(
        McpConfigLoader(tmp_path), session_factory=factory, secrets=secrets
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


def _write_config(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://example.invalid/mcp",
                        "headers": {"Authorization": "Bearer ${GITHUB_PAT}"},
                        "enabled": True,
                    },
                    "web": {
                        "command": "uv",
                        "args": ["run", "x.py"],
                        "env": {"KEY": "${OLLAMA_API_KEY}"},
                        "enabled": True,
                    },
                }
            }
        )
    )


def test_listing_names_each_variable_and_where_its_value_comes_from_never_the_value(
    tmp_path, monkeypatch
) -> None:
    _write_config(tmp_path)
    monkeypatch.setenv("OLLAMA_API_KEY", "env-key")
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    secrets = McpSecrets()
    client = _client(tmp_path, secrets, [])
    body = client.get("/v1/mcp/servers").json()
    by_name = {s["name"]: s for s in body["servers"]}
    assert by_name["github"]["secrets"] == [{"name": "GITHUB_PAT", "source": None}]
    assert by_name["web"]["secrets"] == [{"name": "OLLAMA_API_KEY", "source": "environment"}]
    secrets.update({"GITHUB_PAT": "super-secret-token"})
    body = client.get("/v1/mcp/servers").json()
    assert {s["name"]: s for s in body["servers"]}["github"]["secrets"] == [
        {"name": "GITHUB_PAT", "source": "saved"}
    ]
    assert "super-secret-token" not in json.dumps(body)


def test_saving_a_secret_reconnects_only_the_servers_that_use_it(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    monkeypatch.setenv("OLLAMA_API_KEY", "env-key")
    secrets, connects = McpSecrets(), []
    client = _client(tmp_path, secrets, connects)
    with client:
        client.post("/v1/mcp/servers/github/reconnect", json={"disabled": []})
        connects.clear()
        resp = client.put("/v1/mcp/secrets", json={"values": {"GITHUB_PAT": "tok"}, "disabled": []})
        assert resp.status_code == 200
        assert connects == ["github"]
        assert secrets.get("GITHUB_PAT") == "tok"
        assert "tok" not in resp.text
        connects.clear()
        client.put("/v1/mcp/secrets", json={"values": {"GITHUB_PAT": None}, "disabled": []})
        assert secrets.get("GITHUB_PAT") is None and connects == ["github"]


def test_secret_route_refuses_bad_input_and_a_disabled_mcp(tmp_path) -> None:
    _write_config(tmp_path)
    client = _client(tmp_path, McpSecrets(), [])
    assert client.put("/v1/mcp/secrets", json={"values": {"bad name": "x"}}).status_code == 400
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(),
            object(),
            ShadowWorkspaceManager(tmp_path / "s2"),
            None,
            None,
            mcp_manager=None,
        )
    )
    assert TestClient(app).put("/v1/mcp/secrets", json={"values": {"A": "x"}}).status_code == 409
