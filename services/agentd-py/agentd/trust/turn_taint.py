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
    # Untrusted output from earlier turns is still in the context this turn reads. It
    # makes Auto ask before sending anything out (Phase 3a), but does not stop the user
    # from asking for a memory in their own words this turn, which is what `seen` guards.
    in_history: bool = False

    def mark(self) -> None:
        self.seen = True

    def mark_history(self) -> None:
        self.in_history = True

    @property
    def context_tainted(self) -> bool:
        return self.seen or self.in_history

    def reset(self) -> None:
        self.seen = False
        self.in_history = False
