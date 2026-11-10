"""Helpers for short side calls made next to the agent's own turns (the Auto judge, the
voice front-man): a smaller model where one is verified for the provider, low effort, and
usage billed to the current owner."""

from __future__ import annotations

from typing import Any

from agentd.providers.reasoning_effort import ReasoningEffort
from agentd.providers.usage import METER, USAGE_OWNER

# Verified on the Claude plan login: answers a forced tool call in ~1.5 s (2026-11-10).
CLAUDE_SMALL_MODEL = "claude-haiku-5-5"


def small_plan_model(transport: Any) -> str | None:
    """The smaller model to prefer for side calls, or None to use the agent's own.
    Only providers whose small model is verified here get one."""
    from agentd.providers.claude_subscription import ClaudeSubscriptionTransport

    inner = getattr(transport, "wrapped", transport)
    return CLAUDE_SMALL_MODEL if isinstance(inner, ClaudeSubscriptionTransport) else None


async def low_effort(transport: Any, model: str) -> ReasoningEffort | None:
    """LOW when the model lists it as supported; otherwise leave the transport's own."""
    support = getattr(transport, "reasoning_effort_support", None)
    if not callable(support):
        return None
    try:
        result = await support(model)
    except Exception:  # noqa: BLE001 — capability lookup is best-effort
        return None
    supported: frozenset[ReasoningEffort] = getattr(result, "supported", frozenset())
    return ReasoningEffort.LOW if ReasoningEffort.LOW in supported else None


def record_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """on_usage callback: some transports (the Claude plan) report tokens only here."""
    METER.record(USAGE_OWNER.get(), prompt=prompt_tokens, completion=completion_tokens)
