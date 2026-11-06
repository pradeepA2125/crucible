"""Context the span helpers read. Imports nothing from OpenTelemetry, so the call sites that
set these values (the Auto judge, the memory curator, the eval runner) cost nothing with
tracing off."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Literal

CallPurpose = Literal["agent", "auto_judge", "memory_curator", "memory_summary", "eval_judge"]

# Why a model call is made: the agent's own reasoning, or a call Crucible makes for itself
# (so its cost is never mistaken for the agent's).
CALL_PURPOSE: ContextVar[CallPurpose] = ContextVar("crucible_call_purpose", default="agent")
# Attributes the eval runner puts on every turn span of a case (crucible.eval_run_id, …).
EVAL_TAGS: ContextVar[Mapping[str, str]] = ContextVar(
    "crucible_eval_tags", default=MappingProxyType({}))

TurnKind = Literal["user", "notice", "continuation"]

# Span contexts of recent turns, by turn id, so a later trace can link to the turn that
# started its work (a continuation to its user turn, an agent to the turn that dispatched it).
# Opaque here: the span helpers store OpenTelemetry SpanContext objects.
_TURN_LINK_LIMIT = 256
_TURN_LINKS: OrderedDict[str, object] = OrderedDict()
_LAST_USER_TURN: dict[str, object] = {}


def remember_turn(turn_id: str, link: object, *, thread_id: str = "",
                  is_user_turn: bool = False) -> None:
    if turn_id:
        _TURN_LINKS[turn_id] = link
        _TURN_LINKS.move_to_end(turn_id)
        while len(_TURN_LINKS) > _TURN_LINK_LIMIT:
            _TURN_LINKS.popitem(last=False)
    if is_user_turn and thread_id:
        _LAST_USER_TURN[thread_id] = link


def turn_link(turn_id: str) -> object | None:
    return _TURN_LINKS.get(turn_id)


def last_user_turn_link(thread_id: str) -> object | None:
    return _LAST_USER_TURN.get(thread_id)
