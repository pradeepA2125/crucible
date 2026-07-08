from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class McpServerConfig(BaseModel):
    """One enabled server entry from .crucible/mcp.json. `env`/`headers` values may
    contain ${VAR} references — resolved at connect time (never stored resolved)."""
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False

    def fingerprint(self) -> str:
        """Change-detection key for reconcile(): any field change = new fingerprint."""
        return self.model_dump_json()


class McpServerStatus(BaseModel):
    """Queryable per-server connection state (spec: P4 UI serializes this)."""
    name: str
    state: Literal["connecting", "connected", "failed", "disconnected"]
    detail: str = ""
    tool_count: int = 0
