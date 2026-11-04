"""Whether this turn has read untrusted output (prompt-injection Phase 1, spec §4.4).

One per turn: the loop marks it when an untrusted tool result arrives, and memory refuses a
`remember` that claims the user asked for it from then on, since the claim could come from
text the model just read.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TurnTaint:
    seen: bool = False

    def mark(self) -> None:
        self.seen = True

    def reset(self) -> None:
        self.seen = False
