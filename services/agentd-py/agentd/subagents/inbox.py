"""Live input for a running agent (spec §3.6)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InboxItem:
    kind: Literal["report", "note"]
    text: str
    wakes: bool         # an idle owner is re-activated for it (§3.6 leftovers)
    source_id: str = ""  # the agent the item came from, when there is one
