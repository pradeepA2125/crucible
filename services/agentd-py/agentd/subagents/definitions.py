"""Agent definitions (spec §9). Phase 2 ships the built-ins only; Phase 3 adds discovery
of `.crucible/agents` / `.claude/agents` files in front of them."""
from __future__ import annotations

from dataclasses import dataclass

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
