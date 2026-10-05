"""Agent definitions (spec §9). Phase 2 ships the built-ins only; Phase 3 adds discovery
of `.crucible/agents` / `.claude/agents` files in front of them."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentd.prompting.tagged import Permission


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str          # one line for the dispatcher's catalog
    permission: Permission
    tools: frozenset[str] | None  # None = every tool the dispatcher's child may have
    persona: str
    model: str = "inherit"
    max_turns: int | None = None
    # Applied before `tools` (spec §9.3). "edit" in either set is the edit ACTION type.
    disallowed_tools: frozenset[str] = frozenset()
    skills: tuple[str, ...] = ()  # pre-seeded into the child's active skills (spec §5.2)
    source: str = "built-in"      # the file it came from, for warnings and logs
    trust: str = "trusted"        # "trusted" | "capped" (spec §3.12)
    content_sha256: str = ""      # the file's hash when loaded; empty for built-ins
    # What the loader noticed (spec §10.1): shown in Settings, never fatal.
    warnings: tuple[str, ...] = ()


_EXPLORE_PERSONA = (
    "Locate, then read. Find the code your task names with search_code (and query_graph "
    "when it is available), read the relevant ranges with read_file, and stop as soon as "
    "more reading would not change your findings. Your report is dense but complete: "
    "every claim cites path:line, and it says plainly what you did not find.")

BUILTIN_AGENTS: dict[str, AgentDefinition] = {
    "explore": AgentDefinition(
        name="explore",
        description=("Read-only investigator: locates and reads code, returns findings "
                     "with path:line citations. Never edits files or runs commands."),
        permission="plan",
        tools=frozenset({"search_code", "read_file", "list_directory", "query_graph",
                         "search_semantic"}),
        persona=_EXPLORE_PERSONA),
    "general-purpose": AgentDefinition(
        name="general-purpose",
        description=("Implements one self-contained part of a change: reads, edits the "
                     "files it is assigned, runs checks, and reports what it changed."),
        permission="default",
        tools=None,
        persona=""),
}


def definition_to_json(d: AgentDefinition) -> dict[str, Any]:
    """The snapshot stored on the agent row (spec §3.1): resume uses it, so editing or
    deleting the .md file never changes an agent already running a task."""
    return {"name": d.name, "description": d.description, "permission": d.permission,
            "tools": sorted(d.tools) if d.tools is not None else None, "persona": d.persona,
            "model": d.model, "max_turns": d.max_turns,
            "disallowed_tools": sorted(d.disallowed_tools), "skills": list(d.skills),
            "source": d.source, "trust": d.trust, "content_sha256": d.content_sha256}


def definition_from_json(data: dict[str, Any]) -> AgentDefinition:
    tools = data.get("tools")
    return AgentDefinition(
        name=str(data["name"]), description=str(data.get("description", "")),
        permission=data["permission"],
        tools=frozenset(tools) if tools is not None else None,
        persona=str(data.get("persona", "")), model=str(data.get("model", "inherit")),
        max_turns=data.get("max_turns"),
        disallowed_tools=frozenset(data.get("disallowed_tools", [])),
        skills=tuple(data.get("skills", [])), source=str(data.get("source", "built-in")),
        trust=str(data.get("trust", "trusted")),
        content_sha256=str(data.get("content_sha256", "")))
