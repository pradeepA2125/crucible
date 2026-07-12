"""Env resolution for exec sessions (mirrors controller_factory flag style)."""
from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}
YIELD_MIN_MS = 250
YIELD_MAX_MS = 30_000


def is_exec_sessions_enabled() -> bool:
    """Default OFF (ship dark); start-backend.sh opts in."""
    return os.getenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", "0").strip().lower() in _TRUTHY


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def max_session_count() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_MAX_COUNT", 16)


def buffer_bytes() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_BUFFER_BYTES", 1_048_576)


def default_yield_ms() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_DEFAULT_YIELD_MS", 2000)


def result_max_chars() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_RESULT_MAX_CHARS", 4000)


def clamp_yield_ms(raw: object) -> int:
    """Model-supplied yield → int clamped to [250, 30000]; garbage → default."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        val = default_yield_ms()
    else:
        try:
            val = int(raw)
        except ValueError:
            val = default_yield_ms()
    return max(YIELD_MIN_MS, min(YIELD_MAX_MS, val))
