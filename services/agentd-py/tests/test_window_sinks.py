"""collect_window_sinks is the pure helper behind main.py's window-sink wiring.

Extracted specifically so the discovery of the chat controller's memory harness
(reached via a string-keyed getattr, since the providers package must not import
the chat package) is unit-testable in isolation from constructing the whole app.
"""
from __future__ import annotations

from agentd.providers.window_sinks import collect_window_sinks


class _Harness:
    """Stand-in for MemoryHarness / NO_OP_HARNESS — identity is what matters here."""


class _ChatHandlerWithHarness:
    def __init__(self, harness: object) -> None:
        self._memory_harness = harness


class _ChatHandlerWithoutHarness:
    """Legacy ChatAgent shape: no _memory_harness attribute at all."""


def test_includes_both_sinks_when_chat_handler_has_a_distinct_harness() -> None:
    task_harness = _Harness()
    chat_harness = _Harness()
    chat_handler = _ChatHandlerWithHarness(chat_harness)

    sinks = collect_window_sinks(task_harness, chat_handler)

    assert sinks == [task_harness, chat_harness]


def test_omits_chat_sink_when_handler_has_no_memory_harness_attribute() -> None:
    task_harness = _Harness()
    chat_handler = _ChatHandlerWithoutHarness()

    sinks = collect_window_sinks(task_harness, chat_handler)

    assert sinks == [task_harness]


def test_omits_chat_sink_when_chat_handler_is_none() -> None:
    task_harness = _Harness()

    sinks = collect_window_sinks(task_harness, None)

    assert sinks == [task_harness]


def test_collapses_to_one_sink_when_both_are_the_same_object() -> None:
    # e.g. memory disabled: both task and chat harnesses are the same NO_OP_HARNESS.
    shared_harness = _Harness()
    chat_handler = _ChatHandlerWithHarness(shared_harness)

    sinks = collect_window_sinks(shared_harness, chat_handler)

    assert sinks == [shared_harness]
