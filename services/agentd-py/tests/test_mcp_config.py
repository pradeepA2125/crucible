"""McpConfigLoader: mtime-cached .ai-editor/mcp.json reader (mirrors the
ProjectInstructionsLoader cache discipline) + ${VAR} interpolation helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentd.mcp.config import (
    McpConfigLoader,
    McpMissingEnvVar,
    interpolate_env,
    mcp_decision_timeout_sec,
    mcp_tools_max_chars,
)


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / ".ai-editor" / "mcp.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_missing_file_returns_empty(tmp_path: Path):
    assert McpConfigLoader(str(tmp_path)).load() == []


def test_malformed_json_returns_empty(tmp_path: Path):
    p = tmp_path / ".ai-editor" / "mcp.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert McpConfigLoader(str(tmp_path)).load() == []


def test_parses_stdio_and_http_entries(tmp_path: Path):
    _write(tmp_path, {"mcpServers": {
        "echo": {"command": "python", "args": ["srv.py"], "env": {"K": "v"}, "enabled": True},
        "gh": {"type": "http", "url": "https://x/mcp", "headers": {"A": "b"}, "enabled": True},
    }})
    cfgs = {c.name: c for c in McpConfigLoader(str(tmp_path)).load()}
    assert cfgs["echo"].transport == "stdio" and cfgs["echo"].command == "python"
    assert cfgs["gh"].transport == "http" and cfgs["gh"].url == "https://x/mcp"


def test_streamable_http_type_alias(tmp_path: Path):
    _write(tmp_path, {"mcpServers": {
        "s": {"type": "streamable-http", "url": "https://x/mcp", "enabled": True}}})
    assert McpConfigLoader(str(tmp_path)).load()[0].transport == "http"


def test_enabled_gate_excludes_absent_and_false(tmp_path: Path):
    # Decision 4: presence in the file is NOT trust — only enabled:true connects.
    _write(tmp_path, {"mcpServers": {
        "on": {"command": "x", "enabled": True},
        "off": {"command": "x", "enabled": False},
        "absent": {"command": "x"},
    }})
    assert [c.name for c in McpConfigLoader(str(tmp_path)).load()] == ["on"]


def test_invalid_names_and_shapes_skipped(tmp_path: Path):
    _write(tmp_path, {"mcpServers": {
        "bad__name": {"command": "x", "enabled": True},   # __ breaks namespacing
        "no-transport": {"enabled": True},                 # neither command nor url
        "ok": {"command": "x", "enabled": True},
    }})
    assert [c.name for c in McpConfigLoader(str(tmp_path)).load()] == ["ok"]


def test_mtime_cache_and_self_update(tmp_path: Path):
    p = _write(tmp_path, {"mcpServers": {"a": {"command": "x", "enabled": True}}})
    loader = McpConfigLoader(str(tmp_path))
    assert [c.name for c in loader.load()] == ["a"]
    assert loader.load() is loader.load()  # cached list object on unchanged mtime
    import os
    p.write_text(json.dumps(
        {"mcpServers": {"b": {"command": "x", "enabled": True}}}), encoding="utf-8")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    assert [c.name for c in loader.load()] == ["b"]


def test_interpolate_env_resolves_and_raises(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    assert interpolate_env({"Authorization": "Bearer ${MY_TOKEN}"}) == {
        "Authorization": "Bearer s3cret"}
    monkeypatch.delenv("NOPE_VAR", raising=False)
    with pytest.raises(McpMissingEnvVar, match="NOPE_VAR"):
        interpolate_env({"k": "${NOPE_VAR}"})


def test_env_knob_defaults(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_MCP_TOOLS_MAX_CHARS", raising=False)
    monkeypatch.delenv("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", raising=False)
    assert mcp_tools_max_chars() == 16000
    assert mcp_decision_timeout_sec() == 0.0
    monkeypatch.setenv("CRUCIBLE_MCP_TOOLS_MAX_CHARS", "500")
    monkeypatch.setenv("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", "2.5")
    assert mcp_tools_max_chars() == 500
    assert mcp_decision_timeout_sec() == 2.5
