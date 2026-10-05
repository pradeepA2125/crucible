"""Report fields are validated in the loop (spec v2 §8.3); deadlines force the final
iteration (§8.3, §3.11)."""
from __future__ import annotations

import pytest

from agentd.chat.controller_loop import ControllerLoop, ReportVerdict
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.subagents.context import AgentContext
from agentd.tools.sources import AggregatingToolRegistry

REPORT = {"type": "report", "thought": "t", "summary": "s", "status": "completed"}
TOOL = {"type": "tool_call", "thought": "look", "tool": "list_directory", "args": {}}


class _Engine:
    def __init__(self, script: list[dict[str, object]]) -> None:
        self.script = script
        self.types: list[list[str]] = []

    async def create_controller_step(self, plan_context, history, tool_definitions,
                                     allowed_types=None, **_k):  # type: ignore[no-untyped-def]
        self.types.append(list(allowed_types or []))
        return self.script[min(len(self.types) - 1, len(self.script) - 1)]


def _loop(engine: _Engine) -> ControllerLoop:
    ctx = AgentContext(agent_id="agent-r", name="general-purpose", label="alice", depth=1,
                       parent_agent_id=None, permission="default",
                       allowed_types=("tool_call", "progress", "report"), persona="",
                       max_iters=10)
    return ControllerLoop(engine, AggregatingToolRegistry([]), EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM(start="AGENT"), agent=ctx)


@pytest.mark.asyncio
async def test_report_check_redirect_then_accept() -> None:
    calls: list[bool] = []

    def check(resp, final):  # type: ignore[no-untyped-def]
        calls.append(final)
        return ReportVerdict(message="state P1 first") if len(calls) == 1 else ReportVerdict()

    engine = _Engine([REPORT])
    outcome = await _loop(engine).run({"goal": "g"}, max_iters=10, report_check=check)
    assert outcome.kind == "report"
    assert calls == [False, False]
    assert "state P1 first" in str(outcome.history)


@pytest.mark.asyncio
async def test_report_check_identical_resubmission_exhausts_into_acceptance() -> None:
    finals: list[bool] = []

    def check(resp, final):  # type: ignore[no-untyped-def]
        finals.append(final)
        if final:
            return ReportVerdict()
        return ReportVerdict(message="bad id P9", malformed=len(finals) > 1)

    engine = _Engine([REPORT])
    outcome = await _loop(engine).run({"goal": "g"}, max_iters=20, report_check=check)
    assert outcome.kind == "report"
    assert finals[-1] is True                     # accepted with invalid entries dropped
    assert len(finals) <= 6                       # bounded by _MAX_MALFORMED, not max_iters


@pytest.mark.asyncio
async def test_force_final_narrows_and_accepts_partial() -> None:
    engine = _Engine([TOOL, REPORT])
    loop = _loop(engine)
    loop.force_final()
    seen: list[bool] = []
    outcome = await loop.run({"goal": "g"}, max_iters=10,
                             report_check=lambda r, f: (seen.append(f), ReportVerdict())[1])
    assert engine.types[0] == ["report"]
    assert outcome.payload == {"status": "partial"}
    assert seen == [True]
