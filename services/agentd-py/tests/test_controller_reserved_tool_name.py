"""Fix: a tool_call whose 'tool' is actually a top-level response type name
(edit/propose_mode/answer/clarify/submit_changes) is rejected with a targeted
correction instead of silently dispatched to the tool registry.

Root cause (found live 2026-07-17, alpha-forge workspace): a weak model that had
just correctly used {"type":"tool_call","tool":"write_todos",...} generalized the
same shape onto 'edit' — {"type":"tool_call","tool":"edit","args":{"patch_ops":[...]}}.
'edit' is never a registered tool (it's a top-level response TYPE gated by
ControllerPhaseSM), so the registry returned a generic "Error: unknown tool 'edit'"
with no actionable signal. The model looped on the identical illegal call for over
an hour, each attempt costing a full ~13.5k-token regeneration, because this path
did not count toward consecutive_malformed and had no dispatch guard.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, ControllerLoopExhausted
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _loop(tmp_path, steps, sm):
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)]
    )
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=sm)


@pytest.mark.asyncio
async def test_tool_call_edit_is_rejected_not_dispatched(tmp_path: Path):
    # EDIT phase — even here, 'edit' as a tool_call TOOL is illegal; it is only ever
    # valid as a top-level {"type":"edit",...} object.
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    steps = [
        {"type": "tool_call", "thought": "write the plan",
         "tool": "edit", "args": {"patch_ops": [
             {"op": "create_file", "file": "plan.md", "content": "x", "reason": "r"}]}},
        {"type": "submit_changes", "thought": "done", "summary": "recovered"},
    ]
    out = await _loop(tmp_path, steps, sm).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "submit_changes"
    assert not (tmp_path / "plan.md").exists(), (
        "tool_call tool='edit' must NOT write a file — it is not a real tool")


@pytest.mark.asyncio
async def test_tool_call_propose_mode_is_rejected_in_decide(tmp_path: Path):
    steps = [
        {"type": "tool_call", "thought": "propose a change",
         "tool": "propose_mode", "args": {}},
        {"type": "answer", "thought": "ok", "answer": "done"},
    ]
    out = await _loop(tmp_path, steps, ControllerPhaseSM()).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and out.text == "done"


@pytest.mark.asyncio
async def test_repeated_tool_call_edit_exhausts_instead_of_looping_forever(tmp_path: Path):
    # Before the fix this never counted as malformed and could grind for max_iters (500,
    # each attempt a full regeneration) instead of failing fast and visibly.
    bad = {"type": "tool_call", "thought": "retry", "tool": "edit",
           "args": {"patch_ops": [{"op": "create_file", "file": "p.md",
                                    "content": "x", "reason": "r"}]}}
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    with pytest.raises(ControllerLoopExhausted):
        await _loop(tmp_path, [bad, bad, bad, bad], sm).run(
            {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=50)
