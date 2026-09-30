import pytest

from agentd.chat import controller_loop as cl
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.reasoning.react_common import MALFORMED_CORRECTION, accepts_kwarg, malformed_correction
from agentd.tools.sources import AggregatingToolRegistry

MAIN = RenderContext.main()
CHILD = RenderContext(audience="child", permission="default",
                      base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                      agent_id="a1", agent_label="impl")
READONLY = RenderContext(audience="child", permission="plan",
                         base_types=frozenset({"tool_call", "progress", "report"}),
                         agent_id="a2", agent_label="survey")
_BANNED = ("submit_changes", "propose_mode", "Plan Mode", "'answer'")


def test_malformed_correction_main_is_unchanged_and_child_names_allowed_types() -> None:
    assert malformed_correction(MAIN, ["tool_call", "answer"]) == MALFORMED_CORRECTION
    out = malformed_correction(CHILD, ["tool_call", "edit", "progress", "report"])
    assert out.endswith("Allowed types right now: tool_call, edit, progress, report.")


@pytest.mark.parametrize("ctx", [CHILD, READONLY])
def test_child_corrections_never_name_parent_terminals(ctx: RenderContext) -> None:
    texts = [
        cl._reserved_tool_name_correction({"tool": "edit"}, "tool_call", ctx) or "",
        cl._progress_repeat_correction({"note": "x"}, "progress", True, ctx) or "",
        cl._progress_dedup_correction({"note": "x"}, "progress", {"x"}, ctx) or "",
        cl._empty_edit_redirect(ctx),
    ]
    for text in texts:
        assert text and not any(b in text for b in _BANNED), text


def test_readonly_corrections_never_offer_edit() -> None:
    repeat = cl._progress_repeat_correction({"note": "x"}, "progress", True, READONLY) or ""
    reserved = cl._reserved_tool_name_correction({"tool": "edit"}, "tool_call", READONLY) or ""
    assert "'edit'" not in repeat
    assert "You are read-only" in reserved


def test_accepts_kwarg_reads_the_signature() -> None:
    def explicit(a, *, b): ...
    def open_kwargs(a, **kw): ...
    assert accepts_kwarg(explicit, "b") and not accepts_kwarg(explicit, "c")
    assert accepts_kwarg(open_kwargs, "anything")


@pytest.mark.asyncio
async def test_loop_uses_the_child_render_context_for_corrections() -> None:
    steps = [{"type": "progress", "thought": "t", "note": "n1"},
             {"type": "progress", "thought": "t", "note": "n2"},
             {"type": "answer", "thought": "t", "answer": "done"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([]), EventBroadcaster(), channel_id="c",
        phase_sm=ControllerPhaseSM(), render_ctx=CHILD)
    out = await loop.run({"goal": "g", "workspace_path": "/w"}, max_iters=6)
    corrections = [m["content"] for m in out.history or [] if m.get("role") == "tool_result"]
    assert any("(or 'report' if you are done)" in c for c in corrections)
