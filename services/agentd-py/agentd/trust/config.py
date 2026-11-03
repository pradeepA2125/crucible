"""The prompt-injection defense switch (spec §3)."""
from __future__ import annotations

import os

_OFF = {"0", "false", "no", "off"}


def is_injection_defense_enabled() -> bool:
    """Default ON. Kill switch: CRUCIBLE_INJECTION_DEFENSE=0 (or false/no/off), which restores
    the behavior from before Phase 1 exactly."""
    return os.getenv("CRUCIBLE_INJECTION_DEFENSE", "1").strip().lower() not in _OFF
