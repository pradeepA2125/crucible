"""Which MCP tools are loaded, read from the history being sent (pure).

A tool is loaded when a bundle carrying its current schema version is in that history,
so restart, rewind and compaction need no separate state: evicting a bundle unloads."""
from __future__ import annotations

import json

from agentd.capabilities.catalog import LOADER_TOOL, tool_version


def _bundles(history: list[dict[str, object]]) -> list[dict[str, object]]:
    out = []
    for m in history:
        if m.get("role") != "tool_result" or m.get("tool") != LOADER_TOOL or m.get("is_error"):
            continue
        try:
            parsed = json.loads(str(m.get("content", "")))
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("kind") == "capability_bundle":
            out.append(parsed)
    return out


def loaded_versions(history: list[dict[str, object]]) -> dict[str, str]:
    """Empty unless a guidance-carrying bundle is also visible: guidance (approvals still
    apply, results are external data) lives only in the first bundle, so once that one is
    evicted the remaining bundles' tools count as unloaded and the next load re-carries it."""
    if not guidance_visible(history):
        return {}
    loaded: dict[str, str] = {}
    for bundle in _bundles(history):
        versions = bundle.get("versions")
        if isinstance(versions, dict):
            loaded.update({str(k): str(v) for k, v in versions.items()})
    return loaded


def guidance_visible(history: list[dict[str, object]]) -> bool:
    return any(b.get("guidance") for b in _bundles(history))


def unloaded_reason(
    tool: str, definitions_by_name: dict[str, dict[str, object]], loaded: dict[str, str],
) -> str | None:
    parts = tool.split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp" or tool not in definitions_by_name:
        return None
    version = loaded.get(tool)
    current = tool_version(definitions_by_name[tool])
    if version == current:
        return None
    namespace = f"mcp.{parts[1]}"
    if version is not None:
        return (f"`{tool}` is not loaded: its schema changed since it was loaded. Call "
                f'load_capabilities with namespace "{namespace}" again, alone in its step.')
    return (f"`{tool}` is not loaded. Call load_capabilities with namespace "
            f'"{namespace}" first, alone in its step.')
