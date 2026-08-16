"""In-memory per-in-flight-turn control channel for the chat controller.

The chat-side sibling of orchestrator/task_control.py: the "Review each edit"
preference has to be mutable while a turn runs, because the composer checkbox is
live and the turn can emit many edits. Freezing it at turn start (the old
behavior) meant a flip only took effect on the NEXT user message.

Single-process asyncio, same as TaskControl: check+set with no `await` in between
is race-safe, so the loop reading this before each edit can never observe a torn
value.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatTurnControl:
    """Live preferences for one in-flight controller turn.

    Owned by ChatController._turn_controls (registered before the loop runs,
    released in the same finally that releases _active_loops); read by
    ControllerLoop before every edit dispatch.
    """

    auto_accept_edits: bool
