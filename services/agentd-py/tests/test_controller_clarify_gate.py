"""Clarify-as-interactive-gate: the clarify action carries answer options, renders as a
durable Class-A gate (kind=clarify), and resolves via resolve_clarify (combined breadcrumb
+ loop re-entry). See docs/superpowers/plans/2026-06-26-clarify-interactive-gate.md."""

from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


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
