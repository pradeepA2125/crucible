"""Pure token accounting for the compaction trigger.

No store, no network, no event loop — this is where the arithmetic that decides
when history gets evicted is actually pinned down.

estimate_tokens is `max(1, len(text) // 3)`, so the fixtures below use content
lengths that are exact multiples of 3 and the expected values are computed, not
guessed.
"""
from agentd.memory.compactor import fixed_overhead, input_tokens
from agentd.memory.models import ObservedPrompt

# 30 chars -> 10 estimated tokens; 60 chars -> 20. History estimate = 30.
HISTORY = [
    {"role": "user", "content": "a" * 30},
    {"role": "assistant", "content": "b" * 60},
]


class TestInputTokens:
    def test_falls_back_to_the_estimate_with_no_observation(self):
        # Every provider but one reports no usage at all; they keep today's behaviour.
        assert input_tokens(HISTORY, None) == 30

    def test_uses_the_exact_count_when_it_covers_the_whole_history(self):
        # Nothing appended since the measurement, so nothing is estimated.
        observed = ObservedPrompt(tokens=500, message_count=2)
        assert input_tokens(HISTORY, observed) == 500

    def test_adds_an_estimate_for_messages_appended_since(self):
        # Measured the first message only; the second arrived afterwards.
        observed = ObservedPrompt(tokens=500, message_count=1)
        assert input_tokens(HISTORY, observed) == 520      # 500 + 60//3

    def test_discards_an_observation_that_outruns_the_history(self):
        # Compaction rewrote history, so the pinned message_count is meaningless.
        observed = ObservedPrompt(tokens=500, message_count=5)
        assert input_tokens(HISTORY, observed) == 30

    def test_a_zero_count_observation_estimates_the_whole_history(self):
        # The call measured system+schema only. Coherent, not a special case.
        observed = ObservedPrompt(tokens=400, message_count=0)
        assert input_tokens(HISTORY, observed) == 430


class TestFixedOverhead:
    def test_is_zero_without_an_observation(self):
        assert fixed_overhead(HISTORY, None) == 0

    def test_is_the_measured_total_minus_the_measured_history(self):
        # What the system prompt and response schema occupy.
        observed = ObservedPrompt(tokens=500, message_count=2)
        assert fixed_overhead(HISTORY, observed) == 470

    def test_only_subtracts_the_slice_that_was_measured(self):
        observed = ObservedPrompt(tokens=500, message_count=1)
        assert fixed_overhead(HISTORY, observed) == 490    # 500 - 30//3

    def test_never_goes_negative(self):
        # The estimate can exceed the real count; overhead is a floor, not a signal.
        observed = ObservedPrompt(tokens=5, message_count=2)
        assert fixed_overhead(HISTORY, observed) == 0

    def test_is_zero_for_a_stale_observation(self):
        observed = ObservedPrompt(tokens=500, message_count=5)
        assert fixed_overhead(HISTORY, observed) == 0
