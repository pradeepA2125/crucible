"""What captured text may contain, and how long it may be (observability spec §6.1)."""
from __future__ import annotations

from agentd.telemetry.config import TEXT_CAP


def cap_text(text: str, cap: int = TEXT_CAP) -> str:
    if len(text) <= cap:
        return text
    return f"{text[:cap]}… [truncated {len(text) - cap} chars]"
