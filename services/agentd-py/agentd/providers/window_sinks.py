"""Pure helper behind main.py's window-sink wiring (ProviderRuntime's window_sinks
list) — kept separate from main.py so it is unit-testable without constructing the
whole app, and separate from the chat package so providers/ doesn't grow a
dependency on it.

The task loop's harness (_task_memory_harness) is always present but matters
least in a default deployment: CRUCIBLE_TASK_SUBSYSTEM is default-OFF, so the
chat controller's own harness — reached only via a string-keyed getattr, since
the legacy ChatAgent has no such attribute at all — is the compactor that
actually runs. A rename of ChatController._memory_harness would otherwise make
that getattr silently yield None with no exception, no type error, and no test
failure; this module exists so that discovery has a covering test.
"""
from __future__ import annotations


def collect_window_sinks(task_harness: object, chat_handler: object | None) -> list[object]:
    """The list of objects a declared context-window change must be applied to.

    `chat_handler` is duck-typed (legacy ChatAgent has no `_memory_harness`;
    the reactive ChatController does) — absence is normal, not an error.
    When memory is disabled both harnesses are the same NO_OP_HARNESS instance;
    identity-collapse keeps that a single sink rather than double-applying.
    """
    sinks: list[object] = [task_harness]
    chat_harness = getattr(chat_handler, "_memory_harness", None)
    if chat_harness is not None and chat_harness is not task_harness:
        sinks.append(chat_harness)
    return sinks
