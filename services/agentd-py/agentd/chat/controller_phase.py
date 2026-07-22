"""ACTIVE/PLAN phase state machine for the chat controller (State pattern).

Mirrors verify_phase_sm's enforcement role: the allowed action `type`s are a pure
function of the phase, so the controller can filter the response schema per turn —
the model literally cannot emit `edit`/`submit_changes` before the user has chosen
Plan Mode's "Implement this plan", and cannot emit `propose_mode` once it's already
in ACTIVE (the default acting phase).

Every phase entry is a FRESH instance constructed with `start=` — there is no
post-construction transition method. The two legal `start=` values and their only
non-default construction sites (see the design doc's C1/C5 entries):
  - "ACTIVE": the default for a plain fresh turn; also `resolve_mode`'s "implement"
    dispatch; also `resolve_clarify`'s ACTIVE-resume (a clarify raised mid-ACTIVE).
  - "PLAN": only `resolve_clarify`'s PLAN-resume (a clarify raised mid-PLAN) — PLAN
    is never transitioned into from a live ACTIVE turn otherwise, only ever
    constructed fresh as a turn's toggle-derived starting phase.
"""
from __future__ import annotations

from agentd.chat.controller_prompts import _PHASE_TYPES

_VALID_STARTS = ("PLAN", "ACTIVE")


class ControllerPhaseSM:
    def __init__(self, start: str = "ACTIVE") -> None:
        if start not in _VALID_STARTS:
            raise ValueError(f"invalid starting phase: {start!r} (must be one of {_VALID_STARTS})")
        self._phase = start

    @property
    def phase(self) -> str:
        return self._phase

    def allowed_types(self) -> list[str]:
        return list(_PHASE_TYPES[self._phase])
