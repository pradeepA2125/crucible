import json
import random

import pytest

from agentd.providers.context_probe import (
    PROBE_CHARS_PER_TOKEN,
    build_probe,
    estimated_prompt_tokens,
    new_passphrase,
    recalled,
)


def test_passphrase_is_three_reproducible_words():
    phrase = new_passphrase(random.Random(1))
    assert phrase.count("-") == 2
    assert phrase.replace("-", "").isalpha()
    assert new_passphrase(random.Random(1)) == phrase  # seeded => deterministic


def test_passphrases_differ_across_runs():
    """A fixed passphrase would let a cached or echoed response fake a pass."""
    seen = {new_passphrase() for _ in range(50)}
    assert len(seen) > 40


def test_probe_puts_the_passphrase_at_the_very_front_of_the_filler():
    """The FRONT of the prompt is what falls off when the window is overrun, so
    that is the only position that proves anything."""
    _system, payload = build_probe(4096, "velvet-harbor-quasar")
    document = str(payload["document"])
    assert document.index("velvet-harbor-quasar") < 200
    assert "velvet-harbor-quasar" not in document[len(document) // 2:]


def test_probe_is_sized_to_the_declared_window():
    _system, payload = build_probe(50_000, "a-b-c")
    estimated = estimated_prompt_tokens(_system, payload)
    assert 0.85 * 50_000 <= estimated <= 50_000


@pytest.mark.parametrize("window", [4_096, 8_192, 32_768, 128_000])
def test_small_windows_are_filled_as_completely_as_large_ones(window):
    """Headroom is proportional, so the fill ratio must not collapse at the bottom
    of the range. A flat reserve left a declared 4,096 window only 52% full, and
    under-filling is the FALSE-PASS direction: the probe would confirm a window it
    never actually tested."""
    system, payload = build_probe(window, "a-b-c")
    ratio = estimated_prompt_tokens(system, payload) / window
    assert 0.85 <= ratio <= 1.0, f"window {window} filled to {ratio:.3f}"


def test_probe_json_serializes():
    """The payload is sent as JSON by every transport; a non-serializable value
    would fail at the boundary, far from here."""
    _system, payload = build_probe(4096, "a-b-c")
    assert json.loads(json.dumps(payload))["document"]


def test_recall_is_case_and_whitespace_insensitive():
    assert recalled("The passphrase is  Velvet-Harbor-Quasar.", "velvet-harbor-quasar")
    assert recalled("velvet harbor quasar", "velvet-harbor-quasar")


def test_no_recall_when_the_answer_is_empty_or_wrong():
    """NVIDIA NIM answered an over-long prompt with completion_tokens:1 and empty
    content, HTTP 200 — an empty answer is a FAIL, not an inconclusive result."""
    assert not recalled("", "velvet-harbor-quasar")
    assert not recalled("   ", "velvet-harbor-quasar")
    assert not recalled("I don't see a passphrase.", "velvet-harbor-quasar")


def test_partial_recall_is_not_recall():
    assert not recalled("velvet harbor", "velvet-harbor-quasar")


def test_chars_per_token_sits_inside_the_measured_range():
    assert 3.71 <= PROBE_CHARS_PER_TOKEN <= 4.40
