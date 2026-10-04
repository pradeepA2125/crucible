"""Sub-agent permissions and tool sets (spec §5.3, §5.6)."""
from __future__ import annotations

from collections.abc import Iterable

from agentd.prompting.tagged import Permission

AGENT_BASE_TYPES: tuple[str, ...] = ("tool_call", "edit", "progress", "report")
DISPATCH_TOOL = "dispatch_agents"
DISPATCH_GROUP = frozenset({"dispatch_agents", "wait_agents", "message_agent", "stop_agent"})
# PTY sessions are thread-scoped (one child could kill a sibling's process), and
# `remember` writes durable memory — children get recall only (spec §5.2, §5.3).
CHILD_EXCLUDED_TOOLS = frozenset({
    "start_session", "write_stdin", "kill_session", "list_sessions", "remember"})
_READ_ONLY_EXCLUDED = frozenset({"run_command"})


def effective_permission(own: Permission, dispatcher: Permission | None) -> Permission:
    """What propagates down a dispatch tree is read-only-ness, not the whole mode: the
    modes are not one restrictiveness scale (dontAsk is stricter than default on commands
    and looser on edits), so "most restrictive" would be ill-defined (spec §5.6)."""
    if own == "plan" or dispatcher == "plan":
        return "plan"
    return own


def tool_matches(name: str, patterns: frozenset[str]) -> bool:
    """`mcp__<server>__*` matches every tool of that server (spec §9.3); anything else
    is an exact name."""
    if name in patterns:
        return True
    return any(p.endswith("__*") and name.startswith(p[:-1]) for p in patterns)


def definition_allows_edit(tools: frozenset[str] | None, disallowed: frozenset[str]) -> bool:
    """Whether a definition keeps the edit action (spec §5.3): `edit` is the mapped name of
    Claude Code's Edit/Write/MultiEdit/NotebookEdit, which are actions here, not tools."""
    return "edit" not in disallowed and (tools is None or "edit" in tools)


def child_allowed_types(permission: Permission, *, can_edit: bool = True) -> tuple[str, ...]:
    if permission == "plan" or not can_edit:
        return tuple(t for t in AGENT_BASE_TYPES if t != "edit")
    return AGENT_BASE_TYPES


def child_tool_names(
    available: Iterable[str], *, definition_tools: frozenset[str] | None,
    permission: Permission, may_dispatch: bool,
    definition_disallowed: frozenset[str] = frozenset(),
) -> frozenset[str]:
    names = {n for n in set(available) - CHILD_EXCLUDED_TOOLS
             if not tool_matches(n, definition_disallowed)}
    if definition_tools is not None:
        names = {n for n in names if tool_matches(n, definition_tools)}
    if permission == "plan":
        # Filtered out of the tool list entirely, so the MCP teaching block is not
        # appended either; _permission_correction is the defense in depth.
        names = {n for n in names if n not in _READ_ONLY_EXCLUDED and not n.startswith("mcp__")}
    if DISPATCH_TOOL in names:
        # A definition that may dispatch (Claude Code's Agent/Task map to dispatch_agents)
        # gets the whole group, so it can always wait — and so satisfy the report guard.
        names |= DISPATCH_GROUP & set(available)
    if not may_dispatch:
        names -= DISPATCH_GROUP
    return frozenset(names)


def follows_live_review(permission: Permission) -> bool:
    """Only an effective-`default` child shares the parent turn's ChatTurnControl, so a
    mid-dispatch "Review each edit" flip reaches it; acceptEdits/dontAsk auto-accept."""
    return permission == "default"
