"""Split the tool catalog into core tools and deferred MCP namespaces (pure)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

LOADER_TOOL = "load_capabilities"
# High enough to name every tool of a typical server: a name left off the directory is a
# name the model cannot ask for (with 10, Playwright's navigate and snapshot were hidden).
_DIRECTORY_NAMES = 40
# One line per tool and per server summary: enough to choose a namespace, far smaller than
# the schemas it stands in for (42 tools ≈ 2,200 characters, 2026-10-20).
_SENTENCE_CHARS = 120
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


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


def first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    sentence = _SENTENCE_END.split(flat, maxsplit=1)[0]
    if len(sentence) <= _SENTENCE_CHARS:
        return sentence
    return sentence[:_SENTENCE_CHARS - 1].rsplit(" ", 1)[0] + "…"


def server_summary(instructions: str) -> str:
    """First prose sentence of a server's instructions: servers often send Markdown, whose
    title line says nothing about what the server does."""
    prose = []
    for line in instructions.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        prose.append(stripped.lstrip(">-*• ").strip())
    return first_sentence(" ".join(p for p in prose if p))


def _tool_line(definition: dict[str, object], namespace: str) -> str:
    name = str(definition.get("name", ""))
    description = str(definition.get("description") or "")
    # McpConnectionManager's stand-in for a server that sends no description says nothing.
    placeholder = f"{short_name(name)} (MCP server '{namespace.removeprefix('mcp.')}')"
    summary = "" if description == placeholder else first_sentence(description)
    return f"  {name}: {summary}" if summary else f"  {name}"


def render_directory(namespaces: dict[str, list[dict[str, object]]],
                     summaries: dict[str, str] | None = None) -> str:
    """One header per namespace (with the server's own instructions, first sentence, when
    it sent them), then each tool's full call name and the first sentence of its
    description. Without descriptions the model could only guess what opaquely named
    tools do; without full names it glued the namespace onto the tool name."""
    lines = []
    for ns in sorted(namespaces):
        defs = namespaces[ns]
        count = f"{len(defs)} tool" if len(defs) == 1 else f"{len(defs)} tools"
        summary = server_summary((summaries or {}).get(ns, ""))
        lines.append(f"- {ns} ({count}):" + (f" {summary}" if summary else ""))
        lines.extend(_tool_line(d, ns) for d in defs[:_DIRECTORY_NAMES])
        if len(defs) > _DIRECTORY_NAMES:
            lines.append(f"  +{len(defs) - _DIRECTORY_NAMES} more")
    return "\n".join(lines)
