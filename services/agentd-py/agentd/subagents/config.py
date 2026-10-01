"""Sub-agent limits (spec §12). Read per call, so a test's monkeypatch takes effect."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[subagent] %s=%r is not an integer — using %d", name, raw, default)
        return default
    if value < minimum:
        logger.warning("[subagent] %s=%d is below %d — using %d", name, value, minimum, minimum)
        return minimum
    return value


def subagent_max_depth() -> int:
    """Max nesting below the parent turn: children are depth 1, theirs depth 2, …"""
    return _int_env("CRUCIBLE_SUBAGENT_MAX_DEPTH", 2, 1)


def subagent_max_concurrent() -> int:
    """Process-wide running-agent cap (protects the provider's rate limit, spec §6.2)."""
    return _int_env("CRUCIBLE_SUBAGENT_MAX_CONCURRENT", 8, 1)


def subagent_max_iters() -> int:
    """A child's loop budget when its definition sets no maxTurns (spec §6.5)."""
    return _int_env("CRUCIBLE_SUBAGENT_MAX_ITERS", 100, 1)
