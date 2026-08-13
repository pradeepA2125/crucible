"""Provider-neutral reasoning-effort ladder.

Deliberately knows nothing about any provider: each transport declares its own
EffortSupport and owns its wire mapping, so a new provider is one file rather
than an edit to a shared table. See
docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ReasoningEffort(StrEnum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


# Cheapest first. Index position IS the ordering clamping walks, so this tuple is
# the single definition of "below" and "above" for the whole feature.
LADDER: tuple[ReasoningEffort, ...] = (
    ReasoningEffort.OFF,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
    ReasoningEffort.MAX,
)


def parse_effort(raw: str | None) -> ReasoningEffort | None:
    """A user/env string to a rung, or None for absent-or-unrecognized.

    Unrecognized input is None (= "send nothing, provider default") rather than an
    exception: this parses env vars and request bodies, and a typo must not take
    the backend down.
    """
    if not raw:
        return None
    try:
        return ReasoningEffort(raw.strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True)
class EffortSupport:
    """What a (backend, model) pair can actually express.

    Tri-state on purpose. A rung listed in neither collection is UNKNOWN — we have
    no evidence either way, which is a different thing from knowing it is
    unsupported. A pasted openai_compatible endpoint is entirely unknown, and the
    UI must be able to say "unverified" rather than claim support it cannot vouch
    for. Same distinction the context-window verdict already draws between
    "couldn't tell" and "downgraded".
    """

    supported: frozenset[ReasoningEffort] = frozenset()
    unsupported: Mapping[ReasoningEffort, str] = field(default_factory=dict)

    def state(self, level: ReasoningEffort) -> str:
        if level in self.supported:
            return "supported"
        if level in self.unsupported:
            return "unsupported"
        return "unknown"

    def resolve(self, level: ReasoningEffort) -> tuple[ReasoningEffort, str | None]:
        """(effective rung, human note when it differs from what was asked).

        Downward-biased: never silently spend MORE thinking than requested — that
        is the failure mode this whole feature exists to fix. Upward is the last
        resort and exists for exactly one real case (Groq 400s on "none", so OFF
        has to become LOW).

        Clamp targets are rungs that are not KNOWN-bad, i.e. supported or unknown.
        Restricting targets to `supported` alone would strand the common
        openai_compatible shape, where MAX is known-unsupported and every other
        rung is merely unverified.
        """
        if self.state(level) != "unsupported":
            return level, None
        reason = self.unsupported[level]
        index = LADDER.index(level)
        for candidate in reversed(LADDER[:index]):
            if self.state(candidate) != "unsupported":
                return candidate, f"{level} unavailable here ({reason}); using {candidate}."
        for candidate in LADDER[index + 1 :]:
            if self.state(candidate) != "unsupported":
                return candidate, f"{level} unavailable here ({reason}); using {candidate}."
        # Degenerate: every rung is known-bad. Keep what was asked and let the
        # request fail loudly rather than inventing a rung that is equally wrong.
        return level, f"{level} unavailable here ({reason}); no alternative rung is available."
