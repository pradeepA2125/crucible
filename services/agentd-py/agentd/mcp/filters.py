"""Per-server enabled_tools / disabled_tools filtering (pure)."""
from __future__ import annotations


def visible_tool_names(
    names: list[str], enabled_tools: list[str] | None, disabled_tools: list[str],
) -> list[str]:
    """The tools the agent may see, in the server's order."""
    allowed = set(names) if enabled_tools is None else set(enabled_tools)
    blocked = set(disabled_tools)
    return [n for n in names if n in allowed and n not in blocked]


def unknown_filter_names(
    names: list[str], enabled_tools: list[str] | None, disabled_tools: list[str],
) -> list[str]:
    """Filter entries the server does not offer (renamed or removed tools)."""
    offered = set(names)
    return sorted({n for n in [*(enabled_tools or []), *disabled_tools] if n not in offered})
