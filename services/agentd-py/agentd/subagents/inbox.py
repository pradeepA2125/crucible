"""Live input for a running agent (spec §3.6)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InboxItem:
    kind: Literal["report", "note", "user"]   # "user": a message the user sent mid-turn (§5.3)
    text: str
    wakes: bool         # an idle owner is re-activated for it (§3.6 leftovers)
    source_id: str = ""  # the agent the item came from, or the queued message's id
    author: str = ""     # who wrote it, shown in the frame header (spec §3.10)
    notice_id: str = ""  # the agent_notices row it carries, for the main agent (§5.2)
