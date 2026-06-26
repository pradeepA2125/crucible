"""propose_mode must carry valid mode vocabulary (smoke-found, 2026-06-15): qwen3
emitted recommended="create" + options=[{"type":"create"}], which made the mode
gate unusable. The loop must reject an invalid propose_mode (correction-retry, like
the phase SM rejects bad action types) and only surface a well-formed one.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, ControllerLoopExhausted
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def _loop(tmp_path: Path, responses: list[dict]) -> ControllerLoop:
    eng = ScriptedReasoningEngine(None, [], controller_step_responses=responses)
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(
        eng, reg, EventBroadcaster(), channel_id="c1", phase_sm=ControllerPhaseSM(),
        # These exercise the full mode vocabulary (incl. create_task); offered-set
        # flag-gating is covered by test_controller_mode_gating.py.
        task_subsystem_enabled=True)


@pytest.mark.asyncio
async def test_invalid_modes_are_corrected_then_valid_surfaces(tmp_path: Path):
    invalid = {
        "type": "propose_mode", "thought": "t",
        "plan_sketch": "add clamp", "recommended": "create",
        "options": [{"type": "create", "description": "make the file"}],
    }
    valid = {
        "type": "propose_mode", "thought": "t",
        "plan_sketch": "add clamp", "recommended": "edit", "reason": "small",
        "options": [
            {"mode": "edit", "label": "Edit inline now", "description": "edit directly"},
            {"mode": "create_task", "label": "Plan it as a task", "description": "plan it"},
        ],
    }
    outcome = await _loop(tmp_path, [invalid, valid]).run(
        {"goal": "add clamp", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "propose_mode"
    assert outcome.payload is not None
    assert outcome.payload["recommended"] == "edit"
    modes = [o["mode"] for o in outcome.payload["options"]]
    assert modes == ["edit", "create_task"]


@pytest.mark.asyncio
async def test_valid_options_missing_recommended_is_normalized_not_rejected(tmp_path: Path):
    """Real qwen3 case: it emits perfect options but omits `recommended`. That's a
    missing hint, not a malformed gate — surface it, defaulting recommended to the
    first option (do NOT exhaust the turn over a missing highlight)."""
    resp = {
        "type": "propose_mode", "thought": "t", "plan_sketch": "add clamp",
        # no "recommended" key at all
        "options": [
            {"mode": "edit", "label": "Edit inline now", "description": "edit directly"},
            {"mode": "create_task", "label": "Plan it as a task", "description": "plan it"},
        ],
    }
    outcome = await _loop(tmp_path, [resp]).run(
        {"goal": "add clamp", "workspace_path": str(tmp_path)}, max_iters=8)
    assert outcome.kind == "propose_mode"
    assert outcome.payload is not None
    assert outcome.payload["recommended"] == "edit"  # defaulted to first option


@pytest.mark.asyncio
async def test_persistent_invalid_modes_exhaust(tmp_path: Path):
    invalid = {
        "type": "propose_mode", "thought": "t", "plan_sketch": "x",
        "recommended": "create", "options": [{"type": "create"}],
    }
    with pytest.raises(ControllerLoopExhausted):
        await _loop(tmp_path, [invalid]).run(
            {"goal": "x", "workspace_path": str(tmp_path)}, max_iters=8)
