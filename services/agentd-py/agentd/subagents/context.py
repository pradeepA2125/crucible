"""AgentContext: the one object that carries a sub-agent's configuration (spec §3)."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agentd.prompting.tagged import Permission


def new_agent_id() -> str:
    return f"agent-{uuid4().hex[:12]}"


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    name: str            # the definition name ("explore", "general-purpose", …)
    label: str           # unique within its dispatch; what the user and siblings see
    depth: int           # 1 = dispatched by the main agent
    parent_agent_id: str | None  # None when the main agent dispatched it
    permission: Permission       # EFFECTIVE permission (read-only-ness inherited, §5.6)
    allowed_types: tuple[str, ...]  # the AGENT base type set for this permission
    persona: str         # the definition body ("" = none)
    max_iters: int
