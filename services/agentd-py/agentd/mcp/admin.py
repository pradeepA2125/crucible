"""Read-modify-write helpers over .crucible/mcp.json for the settings UI routes.
The file stays the source of truth (guided-writer pattern — see
docs/superpowers/2026-07-02-mcp-settings-ui-research.md §1). Unknown keys are
preserved; ${VAR} references are stored verbatim, never resolved."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentd.mcp.config import _NAME_RE


def _read_raw(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_raw_servers(path: Path) -> dict[str, dict[str, Any]]:
    servers = _read_raw(path).get("mcpServers")
    return (
        {k: v for k, v in servers.items() if isinstance(v, dict)}
        if isinstance(servers, dict)
        else {}
    )


def upsert_server(path: Path, name: str, entry: dict[str, Any]) -> None:
    if not _NAME_RE.match(name) or "__" in name:
        raise ValueError(
            f"invalid server name {name!r}: must match [A-Za-z0-9][A-Za-z0-9_-]* "
            "and not contain '__'"
        )
    raw = _read_raw(path)
    servers = raw.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raw["mcpServers"] = servers = {}
    servers[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def remove_server(path: Path, name: str) -> bool:
    raw = _read_raw(path)
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return True


def set_tool_filters(
    path: Path, name: str, enabled_tools: list[str] | None, disabled_tools: list[str],
) -> bool:
    """Set one server's tool filters, leaving every other key verbatim. An unset filter
    (None allowlist, empty blocklist) removes its key. False when the server is absent."""
    raw = _read_raw(path)
    servers = raw.get("mcpServers")
    entry = servers.get(name) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return False
    for key, value in (("enabled_tools", enabled_tools), ("disabled_tools", disabled_tools)):
        if value is None or (key == "disabled_tools" and not value):
            entry.pop(key, None)
        else:
            entry[key] = list(value)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return True
