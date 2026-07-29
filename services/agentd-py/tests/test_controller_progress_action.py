"""Coverage for the non-terminal `progress` action in the chat controller loop.

The failure this closes (reproduced live, kafka-clone-4 dogfood): the model wanted to
NARRATE ("I should use write_todos first... then start implementing") and the only
non-terminal outlet it had was `answer`, which ENDS the turn — so a human had to nudge
it back to work. `progress` gives narration its own non-terminal action.

A free non-terminal action is also a new attractor risk for a weak model (spam notes
instead of acting), so the dispatch is fenced by two MECHANICAL guardrails routed
through the SAME `_MAX_MALFORMED` correction chain every other guard here uses — no
new retry primitive:
  - `_progress_repeat_correction`  — two notes in a row with no real action between
  - `_progress_dedup_correction`   — an exact repeat of a note already posted this turn
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agentd.chat.controller_loop import (
    _PROGRESS_NOTE_MAX_CHARS,
    ControllerLoop,
    _answer_intent_divergence_correction,
    _empty_action_correction,
    _normalize_progress_note,
    _progress_dedup_correction,
    _progress_repeat_correction,
)
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

# ---- pure correction-unit tests (no loop) ----


def test_repeat_correction_fires_only_after_progress() -> None:
    resp = {"type": "progress", "note": "still working"}
    assert _progress_repeat_correction(resp, "progress", True) is not None
    assert _progress_repeat_correction(resp, "progress", False) is None
    assert _progress_repeat_correction({"type": "answer"}, "answer", True) is None


def test_dedup_correction_fires_on_exact_repeat() -> None:
    resp = {"type": "progress", "note": "creating crc.go"}
    seen = {"creating crc.go"}
    assert _progress_dedup_correction(resp, "progress", seen) is not None
    assert _progress_dedup_correction({"type": "progress", "note": "new"}, "progress", seen) is None
    assert _progress_dedup_correction(resp, "answer", seen) is None


def test_dedup_correction_ignores_an_empty_note() -> None:
    # An empty note is _empty_action_correction's job (it fires earlier in the chain);
    # dedup must never treat "" as a seen value or every blank note after the first
    # would be reported as a duplicate instead of as empty.
    assert _progress_dedup_correction({"type": "progress", "note": "  "}, "progress", set()) is None


def test_dedup_catches_an_over_cap_note_repeated_verbatim() -> None:
    # Regression guard: dispatch stores the note CAPPED, so dedup must compare the
    # capped form too. Checking the raw value against a capped store let any note
    # longer than the cap evade dedup entirely (it could never match what was stored).
    long_note = "x" * (_PROGRESS_NOTE_MAX_CHARS + 250)
    resp = {"type": "progress", "note": long_note}
    seen = {_normalize_progress_note(resp)}
    assert _progress_dedup_correction(resp, "progress", seen) is not None


def test_normalize_progress_note_strips_and_caps() -> None:
    assert _normalize_progress_note({"type": "progress", "note": "  hi  "}) == "hi"
    capped = _normalize_progress_note({"note": "y" * (_PROGRESS_NOTE_MAX_CHARS + 10)})
    assert len(capped) == _PROGRESS_NOTE_MAX_CHARS
    assert _normalize_progress_note({}) == ""


def test_empty_note_correction() -> None:
    assert _empty_action_correction({"type": "progress", "note": "  "}, "progress") is not None
    assert _empty_action_correction({"type": "progress", "note": "ok"}, "progress") is None


def test_divergence_correction_names_progress() -> None:
    msg = _answer_intent_divergence_correction(
        {"type": "answer", "thought": "I should use write_todos next",
         "answer": "Let me start by calling write_todos."},
        "answer", frozenset({"write_todos"}),
    )
    assert msg is not None
    assert "progress" in msg


# ---- integration through ControllerLoop.run() ----


def build_loop(
    tmp_path: Path,
    responses: list[dict[str, object]],
    *,
    progress_note_cb: Callable[[str], Awaitable[None]] | None = None,
    phase: str = "ACTIVE",
) -> ControllerLoop:
    """A real ControllerLoop over a scripted engine that returns `responses` in order
    (mirrors the `_loop` helper in test_controller_answer_intent_divergence.py)."""
    ledger = TodoLedger()
    registry = AggregatingToolRegistry([
        BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path),
        TodoToolSource(ledger),
    ])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=responses),
        registry,
        EventBroadcaster(),
        channel_id="c",
        phase_sm=ControllerPhaseSM(phase),
        todo_ledger=ledger,
        progress_note_cb=progress_note_cb,
    )


def _note_collector() -> tuple[list[str], Callable[[str], Awaitable[None]]]:
    notes: list[str] = []

    async def cb(note: str) -> None:
        notes.append(note)

    return notes, cb


@pytest.mark.asyncio
async def test_progress_persists_and_continues(tmp_path: Path) -> None:
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "Working on task 1."},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    # The note did NOT end the turn — the loop ran on to the terminal answer.
    assert outcome.kind == "answer"
    assert outcome.text == "Done."
    assert notes == ["Working on task 1."]


@pytest.mark.asyncio
async def test_progress_repeat_is_corrected(tmp_path: Path) -> None:
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "first"},
            {"type": "progress", "thought": "t", "note": "second"},  # rejected: two in a row
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"
    assert notes == ["first"]  # the second never dispatched


@pytest.mark.asyncio
async def test_progress_repeat_flag_survives_a_rejection(tmp_path: Path) -> None:
    # progress → (rejected junk) → progress: the model still took no real action after
    # its note, so the adjacency flag must NOT be cleared by the rejection.
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "first"},
            {"type": "answer", "thought": "t", "answer": ""},  # rejected: empty answer
            {"type": "progress", "thought": "t", "note": "second"},  # still adjacent
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"
    assert notes == ["first"]


@pytest.mark.asyncio
async def test_progress_dedup_is_corrected(tmp_path: Path) -> None:
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "same"},
            {"type": "tool_call", "thought": "t", "tool": "read_file", "args": {"path": "a"}},
            {"type": "progress", "thought": "t", "note": "same"},  # exact repeat → rejected
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"
    assert notes == ["same"]  # dispatched once; the repeat corrected


@pytest.mark.asyncio
async def test_progress_after_a_real_action_is_allowed(tmp_path: Path) -> None:
    # The negative control for the repeat guard: a DIFFERENT note after a real action
    # must go through — the guardrails must not make `progress` effectively one-shot.
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "starting"},
            {"type": "tool_call", "thought": "t", "tool": "read_file", "args": {"path": "a"}},
            {"type": "progress", "thought": "t", "note": "now the second half"},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"
    assert notes == ["starting", "now the second half"]


@pytest.mark.asyncio
async def test_progress_note_is_capped_at_dispatch(tmp_path: Path) -> None:
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "z" * (_PROGRESS_NOTE_MAX_CHARS + 400)},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert [len(n) for n in notes] == [_PROGRESS_NOTE_MAX_CHARS]


@pytest.mark.asyncio
async def test_progress_does_not_reset_the_malformed_streak(tmp_path: Path) -> None:
    # A note is not evidence of progress: it must not launder a malformed streak.
    # Two malformed, then a note, then malformed until the cap → the turn still bails.
    junk = {"type": "answer", "thought": "t", "answer": ""}  # rejected: empty answer
    loop = build_loop(
        tmp_path,
        [
            junk,
            junk,
            {"type": "progress", "thought": "t", "note": "still here"},
            junk,
            junk,
            {"type": "answer", "thought": "t", "answer": "unreachable"},
        ],
    )
    with pytest.raises(Exception, match="consecutive malformed"):
        await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=12)


@pytest.mark.asyncio
async def test_empty_note_is_corrected_not_dispatched(tmp_path: Path) -> None:
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "   "},  # rejected: empty note
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"
    assert notes == []


@pytest.mark.asyncio
async def test_progress_without_cb_still_continues(tmp_path: Path) -> None:
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "no cb wired"},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=None,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"


@pytest.mark.asyncio
async def test_progress_cb_failure_never_kills_the_turn(tmp_path: Path) -> None:
    async def cb(note: str) -> None:
        raise RuntimeError("transcript write failed")

    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "narrating"},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
        progress_note_cb=cb,
    )
    outcome = await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "answer"


@pytest.mark.asyncio
async def test_progress_broadcasts_a_live_chat_progress_event(tmp_path: Path) -> None:
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "live half"},
            {"type": "answer", "thought": "t", "answer": "Done."},
        ],
    )
    events: list[dict[str, object]] = []
    original = loop._broadcaster.broadcast

    def spy(channel_id: str, event: dict[str, object]) -> None:
        events.append(event)
        original(channel_id, event)

    loop._broadcaster.broadcast = spy  # type: ignore[method-assign]
    await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    progress_events = [e for e in events if e.get("type") == "chat_progress"]
    assert progress_events == [{"type": "chat_progress", "payload": {"note": "live half"}}]


@pytest.mark.asyncio
async def test_progress_allowed_in_plan_phase(tmp_path: Path) -> None:
    notes, cb = _note_collector()
    loop = build_loop(
        tmp_path,
        [
            {"type": "progress", "thought": "t", "note": "exploring"},
            {"type": "answer", "thought": "t", "answer": "Here's the approach."},
        ],
        progress_note_cb=cb,
        phase="PLAN",
    )
    await loop.run({"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
    assert notes == ["exploring"]
