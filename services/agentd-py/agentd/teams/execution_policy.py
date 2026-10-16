"""Tools that cannot mutate a workspace during team investigation."""

from __future__ import annotations

READ_TOOLS = frozenset(
    {
        "read_file",
        "read_image",
        "list_directory",
        "search_code",
        "search_semantic",
        "query_graph",
        "read_env_profile",
        "find_binary",
        "recall",
        "read_skill",
        "write_todos",
        "web_search",
        "web_fetch",
        "dispatch_agents",
        "wait_agents",
        "message_agent",
        "stop_agent",
    }
)


def investigation_tools(names: frozenset[str], team_tools: frozenset[str]) -> frozenset[str]:
    return names & (READ_TOOLS | team_tools)
