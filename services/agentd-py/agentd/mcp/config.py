"""Config for the MCP client: .crucible/mcp.json loader + env knobs.

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
from agentd.mcp.secrets import McpSecrets
from agentd.trust.config import is_injection_defense_enabled
from agentd.trust.project_trust import project_trust_for

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _nonneg_float(env: str, default: float) -> float:
    raw = os.getenv(env, "").strip()
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val >= 0 else default


def mcp_decision_timeout_sec() -> float:
    """0 = wait forever (mirrors CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC)."""
    return _nonneg_float("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", 0.0)


def mcp_connect_timeout_sec() -> float:
    return _nonneg_float("CRUCIBLE_MCP_CONNECT_TIMEOUT_SEC", 30.0)


def mcp_call_timeout_sec() -> float:
    return _nonneg_float("CRUCIBLE_MCP_CALL_TIMEOUT_SEC", 120.0)


TOOL_FILTERS_MALFORMED = "enabled_tools/disabled_tools must be lists of tool names"


def parse_tool_filters(entry: dict[str, object]) -> dict[str, list[str]] | None:
    """The entry's set tool filters; JSON null means unset. None when malformed."""
    filters: dict[str, list[str]] = {}
    for key in ("enabled_tools", "disabled_tools"):
        value = entry.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None
        filters[key] = value
    return filters


class McpMissingEnvVar(ValueError):
    """A ${VAR} reference in env/headers names an unset environment variable."""


def interpolate_env(mapping: dict[str, str], secrets: McpSecrets | None = None) -> dict[str, str]:
    """Resolve ${VAR} references: a secret saved in Settings first, then the real process
    environment. Raises McpMissingEnvVar naming the variable — the server then fails to
    connect with a clear message rather than connecting with a blank credential."""
    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        saved = secrets.get(var) if secrets is not None else None
        val = saved if saved is not None else os.environ.get(var)
        if val is None:
            raise McpMissingEnvVar(var)
        return val

    return {k: _VAR_RE.sub(_sub, v) for k, v in mapping.items()}


class McpConfigLoader:
    def __init__(self, workspace_path: str | Path) -> None:
        self._path = Path(workspace_path) / ".crucible" / "mcp.json"
        self._trust = project_trust_for(Path(workspace_path))
        self._sig: tuple[int, int] | None = None
        self._cached: list[McpServerConfig] = []

    @property
    def config_path(self) -> Path:
        return self._path

    def is_trusted(self) -> bool:
        return self._trust.is_effective("mcp")

    def load(self) -> list[McpServerConfig]:
        """The servers to connect: none while mcp.json is untrusted. A stdio entry runs a
        command outside the sandbox, so a cloned repository's file must not start it
        (prompt-injection Phase 1, spec §4.1)."""
        if is_injection_defense_enabled():
            data, trusted = self._trust.read_checked("mcp")
            if not trusted or data is None:
                return []
            text_sig = (hash(data), len(data))
            if text_sig != self._sig:
                self._cached = self._parse(data.decode("utf-8", errors="replace"))
                self._sig = text_sig
            return self._cached
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

    def _parse(self, text: str | None = None) -> list[McpServerConfig]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8") if text is None else text)
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
            filters = parse_tool_filters(entry)
            if filters is None:
                logger.warning("[mcp] server %r: %s — skipped", name, TOOL_FILTERS_MALFORMED)
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
                    **filters,
                ))
            except ValidationError as exc:
                logger.warning("[mcp] server %r: invalid entry: %s — skipped", name, exc)
        return out
