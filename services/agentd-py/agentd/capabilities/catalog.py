"""Split the tool catalog into core tools and deferred MCP namespaces (pure)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

LOADER_TOOL = "load_capabilities"
_DIRECTORY_NAMES = 10


def tool_version(definition: dict[str, object]) -> str:
    raw = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class CapabilitySet:
    core: list[dict[str, object]]
    namespaces: dict[str, list[dict[str, object]]]


def split_definitions(definitions: list[dict[str, object]]) -> CapabilitySet:
    core: list[dict[str, object]] = []
    namespaces: dict[str, list[dict[str, object]]] = {}
    for d in definitions:
        parts = str(d.get("name", "")).split("__", 2)
        if len(parts) == 3 and parts[0] == "mcp":
            namespaces.setdefault(f"mcp.{parts[1]}", []).append(d)
        else:
            core.append(d)
    return CapabilitySet(core=core, namespaces=namespaces)


def short_name(name: str) -> str:
    return name.split("__", 2)[-1]


def render_directory(namespaces: dict[str, list[dict[str, object]]]) -> str:
    lines = []
    for ns in sorted(namespaces):
        names = [short_name(str(d.get("name", ""))) for d in namespaces[ns]]
        shown = ", ".join(names[:_DIRECTORY_NAMES])
        more = len(names) - _DIRECTORY_NAMES
        suffix = f", +{more} more" if more > 0 else ""
        count = f"{len(names)} tool" if len(names) == 1 else f"{len(names)} tools"
        lines.append(f"- {ns} ({count}): {shown}{suffix}")
    return "\n".join(lines)
