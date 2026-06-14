"""DECIDE -> EDIT phase state machine for the chat controller (State pattern).

Mirrors verify_phase_sm's enforcement role: the allowed action `type`s are a pure
function of the phase, so the controller can filter the response schema per turn —
the model literally cannot emit `edit` before the user has chosen edit mode.
"""
from __future__ import annotations

from agentd.chat.controller_prompts import _PHASE_TYPES


class ControllerPhaseSM:
    def __init__(self) -> None:
        self._phase = "DECIDE"

    @property
    def phase(self) -> str:
        return self._phase

    def allowed_types(self) -> list[str]:
        return list(_PHASE_TYPES[self._phase])

    def enter_edit_mode(self) -> None:
        if self._phase != "DECIDE":
            raise ValueError(f"Cannot enter EDIT from {self._phase}")
        self._phase = "EDIT"
