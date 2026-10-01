"""Sub-agent permissions and tool sets (spec §5.3, §5.6)."""
from __future__ import annotations

from collections.abc import Iterable

from agentd.prompting.tagged import Permission

AGENT_BASE_TYPES: tuple[str, ...] = ("tool_call", "edit", "progress", "report")
DISPATCH_TOOL = "dispatch_agents"
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


def child_allowed_types(permission: Permission) -> tuple[str, ...]:
    if permission == "plan":
        return tuple(t for t in AGENT_BASE_TYPES if t != "edit")
    return AGENT_BASE_TYPES


def child_tool_names(
    available: Iterable[str], *, definition_tools: frozenset[str] | None,
    permission: Permission, may_dispatch: bool,
) -> frozenset[str]:
    names = set(available) - CHILD_EXCLUDED_TOOLS
    if definition_tools is not None:
        names &= definition_tools
    if permission == "plan":
        # Filtered out of the tool list entirely, so the MCP teaching block is not
        # appended either; _permission_correction is the defense in depth.
        names = {n for n in names if n not in _READ_ONLY_EXCLUDED and not n.startswith("mcp__")}
    if not may_dispatch:
        names.discard(DISPATCH_TOOL)
    return frozenset(names)


def follows_live_review(permission: Permission) -> bool:
    """Only an effective-`default` child shares the parent turn's ChatTurnControl, so a
    mid-dispatch "Review each edit" flip reaches it; acceptEdits/dontAsk auto-accept."""
    return permission == "default"
