from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop, ControllerLoopExhausted
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
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM())


@pytest.mark.asyncio
async def test_malformed_then_recovers(tmp_path: Path):
    steps = [
        {"thought": "oops"},  # no type → malformed
        {"type": "answer", "thought": "ok", "answer": "recovered"},
    ]
    out = await _loop(tmp_path, steps).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and out.text == "recovered"


@pytest.mark.asyncio
async def test_consecutive_malformed_raises_after_cap(tmp_path: Path):
    # Scripted engine repeats the last response → malformed forever.
    steps = [{"thought": "still no type"}]
    with pytest.raises(ControllerLoopExhausted):
        await _loop(tmp_path, steps).run(
            {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=10)


@pytest.mark.asyncio
async def test_empty_answer_is_rejected_and_retried(tmp_path: Path):
    # The flat schema lets a weak model emit {"type":"answer"} with no answer body (it dumps
    # its text into the discarded 'thought'). That must NOT return an empty turn — treat it as
    # malformed, correct, and retry.
    steps = [
        {"type": "answer", "thought": "the whole response went here, answer omitted"},
        {"type": "answer", "thought": "ok", "answer": "the real answer"},
    ]
    out = await _loop(tmp_path, steps).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and out.text == "the real answer"


@pytest.mark.asyncio
async def test_empty_tool_call_is_rejected_and_retried(tmp_path: Path):
    # A tool_call with no 'tool' (or empty args) is useless — reject + retry, don't execute "".
    steps = [
        {"type": "tool_call", "thought": "explore", "tool": "", "args": {}},
        {"type": "answer", "thought": "ok", "answer": "done"},
    ]
    out = await _loop(tmp_path, steps).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer" and out.text == "done"


@pytest.mark.asyncio
async def test_empty_clarify_is_rejected_and_retried(tmp_path: Path):
    steps = [
        {"type": "clarify", "thought": "blocked"},  # no question
        {"type": "clarify", "thought": "ok", "question": "Which module?"},
    ]
    out = await _loop(tmp_path, steps).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "clarify" and out.text == "Which module?"
