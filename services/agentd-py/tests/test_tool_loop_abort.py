"""ToolLoop checks the abort event between ReAct iterations and raises TaskAborted."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.domain.models import PlanStep, PlanTarget, PlanTargetIntent, TaskBudget, TaskUsage
from agentd.orchestrator.broadcaster import PatchEventBroadcaster
from agentd.orchestrator.task_control import TaskAborted
from agentd.tools.loop import ToolLoop
from agentd.tools.registry import ToolOutput


class _ExplodingReasoning:
    """If the loop ever calls the model, the abort short-circuit failed."""

    async def create_tool_step(self, *a, **k):
        raise AssertionError("model must not be called once abort is set")

    async def create_planning_step(self, *a, **k):
        raise AssertionError("unused")


class _NoopRegistry:
    def definitions(self, phase: str = "explore"):
        return []

    async def execute(self, name: str, args: dict):
        return ToolOutput(output="(stub)", is_error=False)

    def use_shadow_for_reads(self) -> None:
        pass


@pytest.mark.asyncio
async def test_tool_loop_raises_when_abort_set_between_iterations(tmp_path: Path):
    ev = asyncio.Event()
    ev.set()
    loop = ToolLoop(
        reasoning_engine=_ExplodingReasoning(),
        registry=_NoopRegistry(),
        broadcaster=PatchEventBroadcaster(),
        task_id="t1",
        patch_engine=object(),
        shadow_path=tmp_path,
        abort=ev,
    )
    step = PlanStep(
        id="s1", goal="noop", risk="low",
        targets=[PlanTarget(path="x.py", intent=PlanTargetIntent.NEW)],
    )
    with pytest.raises(TaskAborted):
        await loop.run(
            step=step,
            patch_request_context={"allowed_files": ["x.py"]},
            budget=TaskBudget(max_tool_calls_per_step=4, max_verify_calls_per_step=2),
            usage=TaskUsage(),
        )
