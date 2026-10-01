"""ControllerLoop as a sub-agent (spec §5.1, §6.5)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.todo_ledger import TodoItem, TodoLedger
from agentd.memory.models import TurnPreparation
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.subagents.context import AgentContext
from agentd.subagents.permissions import child_allowed_types
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


class _Recording(ScriptedReasoningEngine):
    def __init__(self, steps: list[dict[str, object]]) -> None:
        super().__init__(None, [], controller_step_responses=steps)
        self.calls: list[dict[str, object]] = []

    async def create_controller_step(
        self, plan_context, history, tool_definitions, *, phase, on_thinking=None,
        on_retry=None, on_progress=None, on_salvage=None, on_usage=None,
        unconstrained=False, allowed_types=None, render_ctx=None, persona=None,
    ):
        self.calls.append({"allowed_types": list(allowed_types or []), "persona": persona,
                           "readonly": plan_context.get("agent_readonly"),
                           "iteration": plan_context.get("iteration")})
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, render_ctx=render_ctx)


class _SpyHarness:
    def __init__(self) -> None:
        self.consolidate: list[object] = []

    async def prepare_turn(self, history, run_id, query="", observed=None, **kwargs):
        self.consolidate.append(kwargs.get("consolidate", "absent"))
        return TurnPreparation(history=history)


def _agent(permission: str = "default") -> AgentContext:
    return AgentContext(
        agent_id="agent-1", name="general-purpose", label="impl", depth=1,
        parent_agent_id=None, permission=permission,
        allowed_types=child_allowed_types(permission), persona="Be terse.", max_iters=5)


def _loop(tmp_path: Path, steps: list[dict[str, object]], agent: AgentContext | None,
          *, ledger: TodoLedger | None = None, harness: object | None = None,
          ) -> tuple[ControllerLoop, _Recording]:
    engine = _Recording(steps)
    registry = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    kwargs: dict[str, object] = {}
    if harness is not None:
        kwargs["memory_harness"] = harness
    if agent is not None:
        kwargs["render_ctx"] = RenderContext.for_agent(
            agent, tools=frozenset(d.name for d in registry.definitions()), shell_policy="ask")
        kwargs["agent"] = agent
    loop = ControllerLoop(
        engine, registry, EventBroadcaster(), channel_id="chat:t:agent:agent-1",
        phase_sm=ControllerPhaseSM(start="AGENT" if agent else "ACTIVE"), todo_ledger=ledger,
        **kwargs)
    return loop, engine


REPORT = {"type": "report", "thought": "t", "summary": "Changed api/a.py. Tests pass."}
LIST = {"type": "tool_call", "thought": "t", "tool": "list_directory", "args": {"path": "."}}
SEARCH = {"type": "tool_call", "thought": "t", "tool": "search_code", "args": {"pattern": "x"}}


@pytest.mark.asyncio
async def test_agent_types_persona_and_readonly_flag(tmp_path: Path) -> None:
    loop, engine = _loop(tmp_path, [REPORT], _agent())
    await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert engine.calls[0] == {"allowed_types": ["tool_call", "edit", "progress", "report"],
                               "persona": "Be terse.", "readonly": False, "iteration": 0}


@pytest.mark.asyncio
async def test_a_read_only_agent_has_no_edit(tmp_path: Path) -> None:
    loop, engine = _loop(tmp_path, [REPORT], _agent("plan"))
    await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert engine.calls[0]["allowed_types"] == ["tool_call", "progress", "report"]
    assert engine.calls[0]["readonly"] is True


@pytest.mark.asyncio
async def test_the_final_iteration_narrows_to_report(tmp_path: Path) -> None:
    loop, engine = _loop(tmp_path, [LIST, SEARCH, REPORT], _agent())
    await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=2)
    assert [c["allowed_types"] for c in engine.calls] == [
        ["tool_call", "edit", "progress", "report"],
        ["tool_call", "edit", "progress", "report"],
        ["report"]]


@pytest.mark.asyncio
async def test_children_never_consolidate_and_the_parent_call_is_unchanged(
    tmp_path: Path,
) -> None:
    child_harness, parent_harness = _SpyHarness(), _SpyHarness()
    child, _ = _loop(tmp_path, [REPORT], _agent(), harness=child_harness)
    await child.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    parent, _ = _loop(tmp_path, [{"type": "submit_changes", "thought": "d", "summary": "ok"}],
                      None, harness=parent_harness)
    await parent.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert child_harness.consolidate == [False]
    assert parent_harness.consolidate == ["absent"]


@pytest.mark.asyncio
async def test_a_voluntary_report_is_completed_and_verbatim(tmp_path: Path) -> None:
    long = "x" * 50_000
    loop, _ = _loop(tmp_path, [{"type": "report", "thought": "t", "summary": long}], _agent())
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=5)
    assert out.kind == "report" and out.payload == {"status": "completed"}
    assert out.text == long


@pytest.mark.asyncio
async def test_open_todos_block_a_report_until_the_final_iteration(tmp_path: Path) -> None:
    ledger = TodoLedger(items=[TodoItem(title="write the limiter"),
                               TodoItem(title="docs", status="done")])
    loop, _ = _loop(tmp_path, [REPORT, LIST, REPORT], _agent(), ledger=ledger)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=2)
    contents = [str(m.get("content", "")) for m in out.history or []]
    assert any(c.startswith("report BLOCKED — 1 todo item(s) still open: write the limiter")
               for c in contents)
    assert out.kind == "report" and out.payload == {"status": "partial"}
    assert out.text == ("Changed api/a.py. Tests pass.\n\nUnfinished:\n"
                        "- write the limiter (pending)")


@pytest.mark.asyncio
async def test_the_fallback_report_is_synthesized_from_the_record(tmp_path: Path) -> None:
    ledger = TodoLedger(items=[TodoItem(title="write the limiter", status="in_progress")])
    empty = {"type": "report", "thought": "t", "summary": ""}
    loop, _ = _loop(tmp_path, [LIST, empty, empty, empty, empty, empty], _agent(),
                    ledger=ledger)
    with pytest.raises(Exception, match="consecutive malformed"):
        await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=20)
    report = loop.fallback_report("the model stopped responding", ["api/a.py"])
    assert report == (
        "Status: failed — the model stopped responding\n\n"
        "Files changed: api/a.py\n\n"
        "Unfinished:\n- write the limiter (in_progress)\n\n"
        'Last tool calls:\n- list_directory {"path": "."}')
