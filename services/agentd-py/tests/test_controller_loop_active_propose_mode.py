"""I4: when the task subsystem flag is ON, propose_mode must be reachable from
ACTIVE too (restricted to create_task/resume — never "implement", since ACTIVE is
already the implementing phase). When OFF (default), ACTIVE's action set is
unchanged — propose_mode stays PLAN-only."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, _propose_mode_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _loop(tmp_path, steps, task_subsystem_enabled):
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),  # ACTIVE
        task_subsystem_enabled=task_subsystem_enabled)


@pytest.mark.asyncio
async def test_propose_mode_reachable_from_active_when_task_subsystem_on(tmp_path: Path):
    steps = [
        {"type": "propose_mode", "thought": "big feature", "plan_sketch": "…",
         "reason": "large", "recommended": "create_task",
         "options": [{"mode": "create_task", "label": "Plan as task", "description": "…"}]},
    ]
    out = await _loop(tmp_path, steps, task_subsystem_enabled=True).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    assert out.kind == "propose_mode"


@pytest.mark.asyncio
async def test_propose_mode_rejected_from_active_when_task_subsystem_off(tmp_path: Path):
    steps = [
        {"type": "propose_mode", "thought": "big feature", "plan_sketch": "…",
         "reason": "large", "recommended": "create_task",
         "options": [{"mode": "create_task", "label": "Plan as task", "description": "…"}]},
        {"type": "answer", "thought": "ok", "answer": "fine, doing it inline"},
    ]
    out = await _loop(tmp_path, steps, task_subsystem_enabled=False).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer"  # propose_mode was rejected, corrected, model recovered


def test_active_propose_mode_allowed_modes_excludes_implement():
    resp = {"recommended": "implement", "options": [
        {"mode": "implement", "label": "x", "description": "y"}]}
    # ACTIVE's task-subsystem-on allowed modes must exclude "implement" — ACTIVE is
    # already the implementing phase, so proposing to re-enter it is meaningless.
    assert _propose_mode_correction(resp, frozenset({"create_task", "resume"})) is not None
