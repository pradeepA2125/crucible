"""Shared live-token-progress accumulator.

Four transports (gemini, groq, turboquant, watsonx) each have a streaming loop that
already separates reasoning from output but reported no counts. Rather than paste
the same char-accumulate/throttle/final-exact-tick into each, they share this.
"""
import pytest

from agentd.providers.token_progress import ProgressTicker


def _rec():
    seen: list[tuple[int, int, int | None, bool]] = []

    def on_progress(r, c, *, input_n=None, exact=False):
        seen.append((r, c, input_n, exact))

    return seen, on_progress


def test_ticker_is_a_no_op_without_a_callback():
    t = ProgressTicker(None)
    t.thinking("abc")
    t.output("def")
    t.finish()  # must not raise


def test_first_tick_is_immediate_then_the_rest_are_throttled():
    """The throttle limits the RATE, it does not delay the start.

    The first delta should move the counter at once — that is the moment prefill
    ends and generation begins, and it is the transition that otherwise reads as a
    hang. openai_compatible and ollama both behave this way; a shared helper that
    swallowed the first tick would make those providers feel different from these.
    """
    seen, cb = _rec()
    t = ProgressTicker(cb, interval_sec=1000.0)
    t.thinking("x" * 400)
    assert len(seen) == 1, "the first tick must not wait for the interval"
    t.output("y" * 800)
    assert len(seen) == 1, "subsequent ticks must be throttled"
    t.finish()
    assert len(seen) == 2, "the closing tick must always fire"


def test_estimates_are_marked_inexact_and_provider_counts_exact():
    seen, cb = _rec()
    t = ProgressTicker(cb, interval_sec=0.0)  # every delta ticks
    t.thinking("a" * 40)
    assert seen[-1][3] is False, "a chars/4 estimate must not claim to be exact"
    t.finish(output_tokens=88, input_tokens=1234)
    r, c, input_n, exact = seen[-1]
    assert (c, input_n, exact) == (88, 1234, True)


def test_finish_falls_back_to_estimates_when_the_provider_reports_nothing():
    seen, cb = _rec()
    t = ProgressTicker(cb, interval_sec=1000.0)
    t.output("z" * 40)
    t.finish()
    r, c, input_n, exact = seen[-1]
    assert c == 10 and exact is False and input_n is None


def test_input_size_rides_every_tick_so_prefill_is_visible_immediately():
    # Prefill produces no deltas at all; the prompt size is the only number that
    # exists during the longest part of a call, and it must not wait for the end.
    seen, cb = _rec()
    t = ProgressTicker(cb, interval_sec=0.0, input_n=372_000)
    t.start()
    assert seen[0] == (0, 0, 372_000, False)
