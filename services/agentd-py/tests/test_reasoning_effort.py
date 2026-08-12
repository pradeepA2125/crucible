import pytest

from agentd.providers.reasoning_effort import (
    LADDER,
    EffortSupport,
    ReasoningEffort,
    parse_effort,
)


def test_ladder_is_cheapest_first():
    assert LADDER == (
        ReasoningEffort.OFF,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.MAX,
    )


def test_state_is_tri_valued():
    support = EffortSupport(
        supported=frozenset({ReasoningEffort.LOW}),
        unsupported={ReasoningEffort.OFF: "no off here"},
    )
    assert support.state(ReasoningEffort.LOW) == "supported"
    assert support.state(ReasoningEffort.OFF) == "unsupported"
    # In neither set: we do not know, which is NOT the same as unsupported.
    assert support.state(ReasoningEffort.MAX) == "unknown"


def test_supported_rung_resolves_to_itself_with_no_note():
    support = EffortSupport(supported=frozenset({ReasoningEffort.HIGH}))
    assert support.resolve(ReasoningEffort.HIGH) == (ReasoningEffort.HIGH, None)


def test_unknown_rung_is_sent_as_is_never_clamped():
    # An unverifiable endpoint must not be silently downgraded — we send and learn.
    support = EffortSupport()
    assert support.resolve(ReasoningEffort.MAX) == (ReasoningEffort.MAX, None)


def test_unsupported_rung_clamps_downward():
    support = EffortSupport(
        supported=frozenset({ReasoningEffort.OFF, ReasoningEffort.LOW, ReasoningEffort.HIGH}),
        unsupported={ReasoningEffort.MAX: "tops out at high"},
    )
    effective, note = support.resolve(ReasoningEffort.MAX)
    assert effective == ReasoningEffort.HIGH
    assert note is not None and "tops out at high" in note


def test_clamp_target_may_be_unknown_not_only_supported():
    # openai_compatible's real shape: MAX explicitly unsupported, everything else
    # unverified. Clamping only to *supported* rungs would find nothing and leave
    # MAX in place, sending a value we know is wrong.
    support = EffortSupport(unsupported={ReasoningEffort.MAX: "tops out at high"})
    effective, note = support.resolve(ReasoningEffort.MAX)
    assert effective == ReasoningEffort.HIGH
    assert note is not None


def test_clamps_upward_only_when_nothing_below_is_available():
    # Groq: rejects "none", so OFF has to go UP to LOW. The one upward case.
    support = EffortSupport(
        supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH}),
        unsupported={ReasoningEffort.OFF: 'Groq rejects "none"'},
    )
    effective, note = support.resolve(ReasoningEffort.OFF)
    assert effective == ReasoningEffort.LOW
    assert note is not None and "rejects" in note


def test_degenerate_all_unsupported_keeps_the_request_and_still_notes_it():
    support = EffortSupport(unsupported={level: "nope" for level in LADDER})
    effective, note = support.resolve(ReasoningEffort.HIGH)
    assert effective == ReasoningEffort.HIGH
    assert note is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high", ReasoningEffort.HIGH),
        ("HIGH", ReasoningEffort.HIGH),
        ("  off ", ReasoningEffort.OFF),
        (None, None),
        ("", None),
        ("banana", None),
    ],
)
def test_parse_effort(raw, expected):
    assert parse_effort(raw) == expected
