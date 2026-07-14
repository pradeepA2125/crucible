import pytest

from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.reasoning.engine import DefaultReasoningEngine


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


@pytest.mark.asyncio
async def test_scripted_controller_step_accepts_on_retry_without_raising():
    eng = ScriptedReasoningEngine(
        None, [], controller_step_responses=[{"type": "answer", "thought": "t", "answer": "hi"}]
    )
    out = await eng.create_controller_step(
        plan_context={"goal": "g", "workspace_path": "/w"},
        history=[],
        tool_definitions=[],
        phase="DECIDE",
        on_retry=lambda a, m, r, msg: None,
    )
    assert out["type"] == "answer"


@pytest.mark.asyncio
async def test_create_controller_step_forwards_on_retry_to_transport() -> None:
    calls: list[tuple[int, int, str, str]] = []

    class _FakeTransport:
        supports_oneof_grammar = False

        async def generate_json(self, **kwargs):
            on_retry = kwargs.get("on_retry")
            if callable(on_retry):
                on_retry(1, 3, "network_error", "⏳ retrying…")
            return {"type": "answer", "thought": "t", "answer": "hi"}

    engine = DefaultReasoningEngine(model="m", transport=_FakeTransport())

    def _on_retry(attempt, max_attempts, reason, message):
        calls.append((attempt, max_attempts, reason, message))

    await engine.create_controller_step(
        plan_context={}, history=[], tool_definitions=[],
        phase="DECIDE", on_retry=_on_retry,
    )

    assert calls == [(1, 3, "network_error", "⏳ retrying…")]
