"""Read-modify-write helpers over .ai-editor/mcp.json for the settings UI routes.
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
