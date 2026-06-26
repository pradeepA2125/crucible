"""Clarify-as-interactive-gate: the clarify action carries answer options, renders as a
durable Class-A gate (kind=clarify), and resolves via resolve_clarify (combined breadcrumb
+ loop re-entry). See docs/superpowers/plans/2026-06-26-clarify-interactive-gate.md."""

from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop, ControllerOutcome
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _controller(tmp_path):
    """A ChatController over a real sqlite chat store + scripted engine, no orchestrator
    (DECIDE-only is enough for the gate/resolve unit tests). Returns (ctrl, store, tid)."""
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    return ctrl, store, th.thread_id


def _loop(tmp_path, steps):
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)]
    )
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),
        task_subsystem_enabled=True)


@pytest.mark.asyncio
async def test_clarify_outcome_carries_question_and_options(tmp_path: Path):
    steps = [{
        "type": "clarify", "thought": "ambiguous target",
        "question": "Which pricing module?",
        "options": ["src/pricing.py", "billing/pricing.py"],
    }]
    out = await _loop(tmp_path, steps).run(
        {"goal": "fix pricing", "workspace_path": str(tmp_path)}, max_iters=4)
    assert out.kind == "clarify"
    assert out.text == "Which pricing module?"
    assert out.payload == {
        "question": "Which pricing module?",
        "options": ["src/pricing.py", "billing/pricing.py"],
    }


@pytest.mark.asyncio
async def test_clarify_without_options_yields_empty_list(tmp_path: Path):
    steps = [{"type": "clarify", "thought": "t", "question": "which file?"}]
    out = await _loop(tmp_path, steps).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    assert out.kind == "clarify" and out.text == "which file?"
    assert out.payload == {"question": "which file?", "options": []}


@pytest.mark.asyncio
async def test_present_clarify_sets_gate_not_chat_response(tmp_path: Path):
    ctrl, store, tid = _controller(tmp_path)
    outcome = ControllerOutcome(
        kind="clarify", text="Which module?",
        payload={"question": "Which module?", "options": ["a.py", "b.py"],
                 "resume_phase": None})
    await ctrl._present_clarify_choice(tid, f"chat:{tid}", outcome)
    gate = store.get_thread(tid).pending_controller_gate
    assert gate is not None and gate.kind == "clarify"
    assert gate.payload["question"] == "Which module?"
    assert gate.payload["options"] == ["a.py", "b.py"]
    # No chat bubble — the question lives in the card, not the transcript.
    assert not any(m.content == "Which module?" for m in store.get_thread(tid).messages)


@pytest.mark.asyncio
async def test_resolve_clarify_writes_combined_breadcrumb_and_clears_gate(tmp_path: Path):
    ctrl, store, tid = _controller(tmp_path)
    store.set_controller_gate(tid, PendingGate(
        kind="clarify",
        payload={"question": "Which module?", "options": ["a.py", "b.py"],
                 "resume_phase": None}))

    captured: dict[str, object] = {}

    async def _noop_loop(*_a, **kw):
        captured["seed_history"] = kw.get("seed_history")
        captured["phase"] = kw.get("phase")
        return ControllerOutcome(kind="answer", text="ok")

    ctrl._run_loop = _noop_loop  # type: ignore[assignment]
    await ctrl.resolve_clarify(tid, "a.py", channel_id=f"chat:{tid}", goal="fix pricing")

    msgs = store.get_thread(tid).messages
    crumb = next(m for m in msgs if m.metadata.get("breadcrumb"))
    assert "Which module?" in crumb.content and "a.py" in crumb.content
    assert store.get_thread(tid).pending_controller_gate is None  # cleared in place
    # The answer is seeded as the user's reply; DECIDE re-entry (resume_phase None).
    assert captured["phase"] is None
    assert any(
        m.get("role") == "user" and m.get("content") == "a.py"
        for m in (captured["seed_history"] or []))


@pytest.mark.asyncio
async def test_resolve_clarify_edit_resume_phase(tmp_path: Path):
    ctrl, store, tid = _controller(tmp_path)
    store.set_controller_gate(tid, PendingGate(
        kind="clarify",
        payload={"question": "range?", "options": [], "resume_phase": "EDIT"}))
    captured: dict[str, object] = {}

    async def _noop_loop(*_a, **kw):
        captured["phase"] = kw.get("phase")
        captured["edit_is_resume"] = kw.get("edit_is_resume")
        return ControllerOutcome(kind="answer", text="ok")

    ctrl._run_loop = _noop_loop  # type: ignore[assignment]
    await ctrl.resolve_clarify(tid, "[0,1]", channel_id=f"chat:{tid}", goal="g")
    assert captured["phase"] == "EDIT"
    assert captured["edit_is_resume"] is True


@pytest.mark.asyncio
async def test_resolve_clarify_idempotent_no_gate(tmp_path: Path):
    ctrl, store, tid = _controller(tmp_path)
    # No pending clarify gate → no-op (no breadcrumb, no raise).
    await ctrl.resolve_clarify(tid, "a.py", channel_id=f"chat:{tid}", goal="g")
    assert not store.get_thread(tid).messages


@pytest.mark.asyncio
async def test_resolve_clarify_empty_answer_noops(tmp_path: Path):
    ctrl, store, tid = _controller(tmp_path)
    store.set_controller_gate(tid, PendingGate(
        kind="clarify", payload={"question": "Q", "options": [], "resume_phase": None}))
    await ctrl.resolve_clarify(tid, "   ", channel_id=f"chat:{tid}", goal="g")
    # Gate stays (nothing resolved) — the card shouldn't submit blank, but defend.
    assert store.get_thread(tid).pending_controller_gate is not None
