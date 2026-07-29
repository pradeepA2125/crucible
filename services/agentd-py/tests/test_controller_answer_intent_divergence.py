"""Regression coverage for the smoking-gun bug found live (kafka-clone-4 dogfood, 2nd
run): right after a plan-writing edit landed, the model's own `thought` said "I should
use write_todos first to track progress, then start implementing" — but it emitted
type='answer' narrating intent ("Let me start by initializing the Go module") instead
of taking the action, ending the turn with nothing done. CONTROLLER_SYSTEM_PROMPT
already teaches this in prose (WRONG/RIGHT examples); this is a mechanical backstop:
`_answer_intent_divergence_correction` rejects the exact "forward-intent phrase + a
real tool name" shape and forces a retry, the same way `_empty_action_correction` and
the other schema-level guards already do.
"""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, _answer_intent_divergence_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

_TOOL_NAMES = frozenset({"write_todos", "read_file", "search_code", "run_command"})


def test_flags_the_exact_smoking_gun_shape() -> None:
    resp = {
        "type": "answer",
        "thought": (
            "The user approved the plan. Now I need to execute it. The plan has 4 "
            "tasks spanning multiple files. This is a big multi-part change, so I "
            "should use write_todos first to track progress, then start implementing."
        ),
        "answer": (
            "Plan written and saved. Now executing the plan. Let me start by "
            "initializing the Go module (Task 1)."
        ),
    }
    correction = _answer_intent_divergence_correction(resp, "answer", _TOOL_NAMES)
    assert correction is not None
    assert "write_todos" in correction


def test_ignores_atype_other_than_answer() -> None:
    resp = {"type": "tool_call", "thought": "I should use write_todos first", "tool": "write_todos"}
    assert _answer_intent_divergence_correction(resp, "tool_call", _TOOL_NAMES) is None


def test_ignores_ordinary_finished_answer_mentioning_a_tool_name() -> None:
    # A completed, self-contained answer that happens to mention a tool name in past
    # tense / explanatory prose (not forward-looking first-person intent) must NOT
    # be flagged — this is the false-positive guard the test suite exists to pin.
    resp = {
        "type": "answer",
        "thought": "explained the write_todos tool",
        "answer": "write_todos lets you track a multi-part change with a checklist.",
    }
    assert _answer_intent_divergence_correction(resp, "answer", _TOOL_NAMES) is None


def test_ignores_forward_intent_phrase_with_no_real_tool_name() -> None:
    # "Let me start by explaining..." with no tool name mentioned — nothing to
    # redirect to, so this must NOT be flagged (there's no actionable next step to name).
    resp = {
        "type": "answer",
        "thought": "let me start by explaining the architecture",
        "answer": "The architecture has three layers: ...",
    }
    assert _answer_intent_divergence_correction(resp, "answer", _TOOL_NAMES) is None


def test_ignores_real_tool_name_with_no_forward_intent_phrase() -> None:
    resp = {
        "type": "answer",
        "thought": "done",
        "answer": "I ran write_todos and marked every item done.",
    }
    assert _answer_intent_divergence_correction(resp, "answer", _TOOL_NAMES) is None


def _loop(tmp_path: Path, steps: list[dict[str, object]]) -> ControllerLoop:
    ledger = TodoLedger()
    reg = AggregatingToolRegistry([
        BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path),
        TodoToolSource(ledger),
    ])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(), todo_ledger=ledger)


@pytest.mark.asyncio
async def test_loop_rejects_the_divergent_answer_and_recovers(tmp_path: Path) -> None:
    steps = [
        {
            "type": "answer",
            "thought": (
                "I should use write_todos first to track progress, then start "
                "implementing."
            ),
            "answer": "Let me start by initializing the Go module (Task 1).",
        },
        {"type": "tool_call", "thought": "ok, actually doing it", "tool": "write_todos",
         "args": {"items": [{"title": "init module", "status": "pending"}]}},
        {"type": "answer", "thought": "done", "answer": "Initialized the module."},
    ]
    out = await _loop(tmp_path, steps).run(
        {"goal": "implement the plan", "workspace_path": str(tmp_path)}, max_iters=6)
    # The FIRST (divergent) answer must not have ended the turn as-is.
    assert out.text != "Let me start by initializing the Go module (Task 1)."
