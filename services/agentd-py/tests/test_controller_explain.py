"""Finding 4: picking "Just explain" must DESCRIBE the approach, not re-open the mode
gate. EXPLAIN phase forbids propose_mode, so a re-proposal is rejected+corrected and the
turn falls to an answer; resolve_mode("explain") ends with an answer and no new gate.
"""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.models import PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

_PROPOSE = {
    "type": "propose_mode", "thought": "t", "plan_sketch": "add clamp()",
    "recommended": "edit", "reason": "small", "options": [
        {"mode": "edit", "label": "Edit", "description": "x"},
        {"mode": "explain", "label": "Explain", "description": "x"}]}


@pytest.mark.asyncio
async def test_explain_phase_rejects_propose_mode_then_answers(tmp_path: Path):
    sm = ControllerPhaseSM()
    sm.enter_explain_mode()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    steps = [
        _PROPOSE,  # forbidden in EXPLAIN → rejected + corrected
        {"type": "answer", "thought": "t", "answer": "The approach: add a clamp helper."},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and "approach" in out.text.lower()


@pytest.mark.asyncio
async def test_resolve_mode_explain_uses_plan_sketch_as_goal(tmp_path: Path):
    # The re-entry must use the agreed plan_sketch as the goal — not the vague last
    # message ("understood, let's do this") — matching the create_task branch.
    from agentd.chat.controller_loop import ControllerOutcome

    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    tid = thread.thread_id
    store.set_controller_gate(tid, PendingGate(kind="mode", payload={
        "plan_sketch": "Add clamp(x, lo, hi) to src/mathutil.py",
        "options": [{"mode": "explain", "label": "Explain", "description": "x"}]}))
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    captured: dict = {}

    async def fake_run_loop(thread_id, channel_id, goal, *, seed_history, step_review,
                            phase=None, turn_id=None):
        captured["goal"] = goal
        return ControllerOutcome(kind="answer", text="ok")

    ctrl._run_loop = fake_run_loop  # type: ignore[method-assign]
    await ctrl.resolve_mode(
        tid, "explain", channel_id=f"chat:{tid}", goal="understood, let's do this")
    assert captured["goal"] == "Add clamp(x, lo, hi) to src/mathutil.py"


@pytest.mark.asyncio
async def test_resolve_mode_explain_answers_without_reraising_gate(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    tid = thread.thread_id
    # A pending mode gate (as if a prior turn proposed). Even if the model tries to
    # re-propose, EXPLAIN blocks it → the turn answers.
    store.set_controller_gate(tid, PendingGate(kind="mode", payload={"options": [
        {"mode": "edit", "label": "Edit", "description": "x"},
        {"mode": "explain", "label": "Explain", "description": "x"}]}))
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                _PROPOSE,
                {"type": "answer", "thought": "t", "answer": "Here is the plan in detail."}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.resolve_mode(tid, "explain", channel_id=f"chat:{tid}", goal="add clamp")

    reloaded = store.get_thread(tid)
    # The mode gate is cleared (not re-raised).
    assert reloaded.pending_controller_gate is None
    # The turn produced an answer in the transcript.
    assert any(m.role == "agent" and "plan" in m.content.lower() for m in reloaded.messages)
