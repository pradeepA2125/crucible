import os
from pathlib import Path

from agentd.mcp.rules import McpRuleStore

NATIVE_SERVER = "Exa"
NATIVE_TOOL = "web_search"


def is_web_search_enabled() -> bool:
    return os.getenv("CRUCIBLE_WEB_SEARCH_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on"}


class WebSearchRuleStore(McpRuleStore):
    def __init__(self, workspace_path: str | Path) -> None:
        self._path = Path(workspace_path) / ".crucible" / "approved-web-search.json"

FETCH_SERVER = "Web"
FETCH_TOOL = "web_fetch"


def is_web_fetch_enabled() -> bool:
    return os.getenv("CRUCIBLE_WEB_FETCH_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on"}


class WebFetchRuleStore(McpRuleStore):
    def __init__(self, workspace_path: str | Path) -> None:
        self._path = Path(workspace_path) / ".crucible" / "approved-web-fetch.json"
