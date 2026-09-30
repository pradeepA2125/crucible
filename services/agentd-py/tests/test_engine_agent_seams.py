import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.reasoning.engine import DefaultReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry

CHILD = RenderContext(audience="child", permission="default",
                      base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                      agent_id="a1", agent_label="impl")


class _CapturingTransport:
    supports_oneof_grammar = False

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"type": "answer", "thought": "t", "answer": "hi"}


@pytest.mark.asyncio
async def test_engine_uses_allowed_types_and_renders_for_the_child() -> None:
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    await engine.create_controller_step(
        plan_context={"goal": "g", "workspace_path": "/w"}, history=[], tool_definitions=[],
        phase="AGENT", allowed_types=["report"], render_ctx=CHILD, persona="Review code.")
    call = transport.calls[0]
    assert call["schema"]["properties"]["type"]["enum"] == ["report"]
    assert call["system_instructions"].startswith("You are a sub-agent")
    assert "AGENT INSTRUCTIONS" in call["system_instructions"]


@pytest.mark.asyncio
async def test_main_active_with_task_subsystem_now_offers_propose_mode() -> None:
    # Latent bug (spec §4.1): _allowed_action_types appended propose_mode but the schema never did.
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    types = ["tool_call", "answer", "clarify", "edit", "submit_changes", "progress", "propose_mode"]
    await engine.create_controller_step(
        plan_context={"goal": "g", "workspace_path": "/w"}, history=[], tool_definitions=[],
        phase="ACTIVE", allowed_types=types)
    assert "propose_mode" in transport.calls[0]["schema"]["properties"]["type"]["enum"]


def test_with_model_shares_the_transport() -> None:
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    other = engine.with_model("m2")
    assert other is not engine and other._transport is transport and other._model == "m2"
    scripted = ScriptedReasoningEngine(None, [])
    assert scripted.with_model("x") is scripted


@pytest.mark.asyncio
async def test_scripted_agent_scripts_are_per_label() -> None:
    eng = ScriptedReasoningEngine(
        None, [], controller_step_responses=[{"type": "answer", "thought": "t", "answer": "main"}],
        agent_scripts={"impl": [{"type": "report", "thought": "t", "summary": "one"},
                                {"type": "report", "thought": "t", "summary": "two"}]})
    args = dict(plan_context={}, history=[], tool_definitions=[], phase="AGENT")
    assert (await eng.create_controller_step(**args, render_ctx=CHILD))["summary"] == "one"
    assert (await eng.create_controller_step(**args, render_ctx=CHILD))["summary"] == "two"
    assert (await eng.create_controller_step(**args))["answer"] == "main"


@pytest.mark.asyncio
async def test_loop_passes_new_kwargs_only_to_engines_that_declare_them() -> None:
    seen: dict[str, object] = {}

    class _Declares:
        async def create_controller_step(self, plan_context, history, tool_definitions, *, phase,
                                         allowed_types=None, render_ctx=None, **_kw):
            seen["allowed_types"], seen["render_ctx"] = allowed_types, render_ctx
            return {"type": "answer", "thought": "t", "answer": "ok"}

    class _Fixed:  # like the existing test fakes: no new params, no **kwargs
        async def create_controller_step(self, plan_context, history, tool_definitions, *, phase,
                                         on_thinking=None, on_retry=None, on_progress=None,
                                         on_salvage=None, on_usage=None, unconstrained=False):
            return {"type": "answer", "thought": "t", "answer": "ok"}

    for engine in (_Declares(), _Fixed()):
        loop = ControllerLoop(engine, AggregatingToolRegistry([]), EventBroadcaster(),
                              channel_id="c", phase_sm=ControllerPhaseSM())
        out = await loop.run({"goal": "g", "workspace_path": "/w"}, max_iters=3)
        assert out.kind == "answer"
    assert seen["allowed_types"] == [
        "tool_call", "answer", "clarify", "edit", "submit_changes", "progress"]
    assert seen["render_ctx"] == RenderContext.main()
