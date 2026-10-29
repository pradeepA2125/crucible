"""Provider-owned reasoning-effort controls and a generic compatibility fallback.

Transports build per-model controls and own their wire mappings. Catalog parsing
retains native options; the original ladder is only an unknown-capability fallback.
See
docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum


class ReasoningEffort(StrEnum):
    OFF = "off"
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
    ULTRA = "ultra"


# Legacy fallback ordering. Native controls use their own available options and
# NATIVE_LADDER only to resolve selections carried across a model/provider change.
LADDER: tuple[ReasoningEffort, ...] = (
    ReasoningEffort.OFF,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
    ReasoningEffort.MAX,
)

# A per-call effort that wins over the transport's own setting for the calls made while
# it is set (the Auto judge runs at low effort without changing the agent's effort). A
# transport that sees the override rejected drops it for that call only.
EFFORT_OVERRIDE: ContextVar[ReasoningEffort | None] = ContextVar(
    "crucible_effort_override", default=None)


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

    Legacy declarations are tri-state by omission. An explicit control is a
    complete list of expressible choices; absent choices are unsupported. Unknown
    choices in a fallback remain selectable and explicitly unverified. A pasted
    openai_compatible endpoint is entirely unknown, and the
    UI must be able to say "unverified" rather than claim support it cannot vouch
    for. Same distinction the context-window verdict already draws between
    "couldn't tell" and "downgraded".
    """

    supported: frozenset[ReasoningEffort] = frozenset()
    unsupported: Mapping[ReasoningEffort, str] = field(default_factory=dict)
    control: EffortControl | None = None

    def payload(self) -> dict:
        result = {
            "supported": sorted(level.value for level in self.supported),
            "unsupported": {level.value: why for level, why in self.unsupported.items()},
        }
        if self.control is not None:
            result["control"] = self.control.payload()
        return result

    def state(self, level: ReasoningEffort) -> str:
        if level in self.supported:
            return "supported"
        if level in self.unsupported:
            return "unsupported"
        if self.control is not None and not any(row.value == level for row in self.control.options):
            return "unsupported"
        return "unknown"

    def resolve(self, level: ReasoningEffort) -> tuple[ReasoningEffort | None, str | None]:
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
        if self.control is not None:
            available = [row.value for row in self.control.options]
            if level in available:
                return level, None
            if not available:
                return None, f"{level} unavailable here; using provider default."
            index = NATIVE_LADDER.index(level)
            lower = [value for value in available if NATIVE_LADDER.index(value) <= index]
            effective = (
                max(lower, key=NATIVE_LADDER.index)
                if lower
                else min(available, key=NATIVE_LADDER.index)
            )
            reason = self.unsupported.get(level)
            detail = f" ({reason})" if reason else ""
            return effective, f"{level} unavailable here{detail}; using {effective}."
        if self.state(level) != "unsupported":
            return level, None
        reason = self.unsupported[level]
        ladder = LADDER if level in LADDER else NATIVE_LADDER
        index = ladder.index(level)
        for candidate in reversed(ladder[:index]):
            if self.state(candidate) != "unsupported":
                return candidate, f"{level} unavailable here ({reason}); using {candidate}."
        for candidate in ladder[index + 1 :]:
            if self.state(candidate) != "unsupported":
                return candidate, f"{level} unavailable here ({reason}); using {candidate}."
        # Degenerate: every rung is known-bad. Keep what was asked and let the
        # request fail loudly rather than inventing a rung that is equally wrong.
        return level, f"{level} unavailable here ({reason}); no alternative rung is available."


# Only the fallback uses the original five-rung ladder. Native catalog controls
# preserve provider values and order; this ordering is for cross-model clamping.
NATIVE_LADDER = (
    ReasoningEffort.OFF,
    ReasoningEffort.NONE,
    ReasoningEffort.MINIMAL,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
    ReasoningEffort.XHIGH,
    ReasoningEffort.MAX,
    ReasoningEffort.ULTRA,
)


def effort_label(value: ReasoningEffort) -> str:
    return "Extra high" if value == ReasoningEffort.XHIGH else value.value.capitalize()


@dataclass(frozen=True)
class EffortOption:
    value: ReasoningEffort
    label: str
    description: str = ""
    state: str = "supported"


@dataclass(frozen=True)
class EffortControl:
    options: tuple[EffortOption, ...]
    source: str
    default: ReasoningEffort | None = None
    reason: str | None = None

    def payload(self) -> dict:
        return {
            "options": [
                {
                    "value": row.value.value,
                    "label": row.label,
                    "description": row.description,
                    "state": row.state,
                }
                for row in self.options
            ],
            "source": self.source,
            "default": self.default.value if self.default else None,
            "reason": self.reason,
        }


class EffortControlBuilder:
    """Providers assemble controls; the composer only renders the descriptor."""

    def __init__(self, source: str):
        self.source = source
        self.options: list[EffortOption] = []
        self.default: ReasoningEffort | None = None
        self.reason: str | None = None

    def option(
        self,
        value: ReasoningEffort,
        *,
        description: str = "",
        label: str | None = None,
        state: str = "supported",
    ) -> EffortControlBuilder:
        if not any(row.value == value for row in self.options):
            self.options.append(
                EffortOption(value, label or effort_label(value), description, state)
            )
        return self

    def with_default(self, value: ReasoningEffort | None) -> EffortControlBuilder:
        self.default = value
        return self

    def unavailable(self, reason: str) -> EffortControlBuilder:
        self.reason = reason
        return self

    def build(self) -> EffortSupport:
        default = self.default if any(row.value == self.default for row in self.options) else None
        control = EffortControl(tuple(self.options), self.source, default, self.reason)
        return EffortSupport(
            supported=frozenset(row.value for row in self.options if row.state == "supported"),
            control=control,
        )


def control_for_transport(support: EffortSupport, *, writable: bool) -> EffortSupport:
    """Bridge legacy transport declarations, with fallback only for unknowns."""
    if not writable:
        return (
            EffortControlBuilder("transport")
            .unavailable("This provider does not expose effort control.")
            .build()
        )
    if support.control is not None:
        return support
    builder = EffortControlBuilder("transport" if support.supported else "fallback")
    for value in LADDER:
        if support.state(value) != "unsupported":
            builder.option(value, state=support.state(value))
    if not builder.options:
        builder.unavailable("This model does not expose effort control.")
    control = builder.build().control
    return EffortSupport(support.supported, support.unsupported, control)


def catalog_effort_support(model: dict) -> EffortSupport | None:
    """OpenAI model catalog: absent/malformed differs from an explicit empty list."""
    rows = model.get("supported_reasoning_levels")
    if not isinstance(rows, list):
        return None
    builder = EffortControlBuilder("catalog")
    for row in rows:
        if not isinstance(row, dict):
            return None
        value = parse_effort(row.get("effort") if isinstance(row.get("effort"), str) else None)
        if value is None:
            # Never turn an unknown future native value into a claimed capability.
            continue
        description = row.get("description")
        builder.option(value, description=description if isinstance(description, str) else "")
    if rows and not builder.options:
        return None
    default = model.get("default_reasoning_level")
    builder.with_default(parse_effort(default if isinstance(default, str) else None))
    if not rows:
        builder.unavailable("This model does not expose effort control.")
    return builder.build()
