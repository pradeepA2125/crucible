"""Config for the MCP client: .ai-editor/mcp.json loader + env knobs.

Loader mirrors ProjectInstructionsLoader's mtime-cache discipline: cheap NOOP
until the file changes, so a config edit self-updates without a restart;
best-effort — malformed input degrades to [] with a warning, never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from pydantic import ValidationError

from agentd.mcp.models import McpServerConfig

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _pos_int(env: str, default: int) -> int:
    raw = os.getenv(env, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _nonneg_float(env: str, default: float) -> float:
    raw = os.getenv(env, "").strip()
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val >= 0 else default


def mcp_tools_max_chars() -> int:
    return _pos_int("AI_EDITOR_MCP_TOOLS_MAX_CHARS", 16000)


def mcp_decision_timeout_sec() -> float:
    """0 = wait forever (mirrors AI_EDITOR_COMMAND_DECISION_TIMEOUT_SEC)."""
    return _nonneg_float("AI_EDITOR_MCP_DECISION_TIMEOUT_SEC", 0.0)


def mcp_connect_timeout_sec() -> float:
    return _nonneg_float("AI_EDITOR_MCP_CONNECT_TIMEOUT_SEC", 30.0)


def mcp_call_timeout_sec() -> float:
    return _nonneg_float("AI_EDITOR_MCP_CALL_TIMEOUT_SEC", 120.0)


class McpMissingEnvVar(ValueError):
    """A ${VAR} reference in env/headers names an unset environment variable."""


def interpolate_env(mapping: dict[str, str]) -> dict[str, str]:
    """Resolve ${VAR} references against the real process environment. Raises
    McpMissingEnvVar naming the variable — the server then fails to connect with
    a clear message rather than connecting with a blank credential."""
    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        val = os.environ.get(var)
        if val is None:
            raise McpMissingEnvVar(var)
        return val

    return {k: _VAR_RE.sub(_sub, v) for k, v in mapping.items()}


class McpConfigLoader:
    def __init__(self, workspace_path: str | Path) -> None:
        self._path = Path(workspace_path) / ".ai-editor" / "mcp.json"
        self._sig: tuple[int, int] | None = None
        self._cached: list[McpServerConfig] = []

    @property
    def config_path(self) -> Path:
        return self._path

    def load(self) -> list[McpServerConfig]:
        try:
            stat = self._path.stat()
        except OSError:
            self._sig, self._cached = None, []
            return self._cached
        sig = (stat.st_mtime_ns, stat.st_size)
        if sig == self._sig:
            return self._cached
        self._cached = self._parse()
        self._sig = sig
        return self._cached

    def _parse(self) -> list[McpServerConfig]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[mcp] unreadable %s: %s — no servers", self._path, exc)
            return []
        servers = raw.get("mcpServers") if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            return []
        out: list[McpServerConfig] = []
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                logger.warning("[mcp] server %r: entry is not an object — skipped", name)
                continue
            if not _NAME_RE.match(str(name)) or "__" in str(name):
                logger.warning("[mcp] server %r: invalid name (must match "
                               "[A-Za-z0-9][A-Za-z0-9_-]* and not contain '__') — skipped", name)
                continue
            if entry.get("enabled") is not True:  # decision 4: explicit allowlist
                continue
            transport = str(entry.get("type", "")).strip().lower()
            if transport == "streamable-http":  # MCP spec's name for this transport
                transport = "http"
            if transport not in ("stdio", "http", "sse"):
                transport = "http" if entry.get("url") else (
                    "stdio" if entry.get("command") else "")
            if (transport == "stdio" and not entry.get("command")) or (
                    transport in ("http", "sse") and not entry.get("url")) or not transport:
                logger.warning("[mcp] server %r: missing command/url for transport — skipped", name)
                continue
            try:
                out.append(McpServerConfig(
                    name=str(name),
                    transport=transport,  # type: ignore[arg-type]
                    command=entry.get("command"),
                    args=[str(a) for a in entry.get("args", []) or []],
                    env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                    url=entry.get("url"),
                    headers={str(k): str(v) for k, v in (entry.get("headers") or {}).items()},
                    enabled=True,
                ))
            except ValidationError as exc:
                logger.warning("[mcp] server %r: invalid entry: %s — skipped", name, exc)
        return out
