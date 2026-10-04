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
    # Effective constraints (spec §3.12): no_ask = command/MCP calls are policy-denied;
    # edit_review = shared (the live review control) | auto | required (always gated).
    no_ask: bool = False
    edit_review: str = "shared"
    capped: bool = False

    def __post_init__(self) -> None:
        # One fact, two fields: a dontAsk agent is no-ask however the context was built
        # (a hand-built context without the flag would otherwise wait on a card forever).
        if self.permission == "dontAsk" and not self.no_ask:
            object.__setattr__(self, "no_ask", True)
