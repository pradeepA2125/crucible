import pytest

from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_scripted_controller_step_returns_scripted_action():
    eng = ScriptedReasoningEngine(
        None, [], controller_step_responses=[{"type": "answer", "thought": "t", "answer": "hi"}]
    )
    out = await eng.create_controller_step(
        plan_context={"goal": "g", "workspace_path": "/w"},
        history=[],
        tool_definitions=[],
        phase="DECIDE",
    )
    assert out["type"] == "answer" and out["answer"] == "hi"
