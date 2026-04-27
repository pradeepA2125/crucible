# tests/test_delta_replan.py
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import PlanStep, TaskBudget, TaskUsage
from agentd.orchestrator.broadcaster import PatchEventBroadcaster
from agentd.tools.loop import PatchResult, PlanHandoff, ToolLoop
from agentd.tools.registry import ToolRegistry


class RevisionNeededEngine:
    """Engine that immediately emits revision_needed."""

    async def create_tool_step(self, step_context, history, tool_definitions):
        return {
            "type": "revision_needed",
            "thought": "Target file is wrong",
            "reason": "function not in planned file",
            "evidence": "grep found it in other.py",
            "affected_steps": ["s2"],
        }

    async def create_planning_step(self, *a, **kw): return {}
    async def create_plan(self, *a, **kw): return {}
    async def create_markdown_plan(self, *a, **kw): return ""
    async def critique_markdown_plan(self, *a, **kw): return {"verdict": "pass", "issues": []}
    async def critique_json_plan(self, *a, **kw): return {"verdict": "pass", "issues": []}
    async def create_patch(self, *a, **kw): return {}


@pytest.mark.asyncio
async def test_tool_loop_returns_plan_handoff_on_revision_needed(tmp_path: Path):
    step = PlanStep(
        id="s1",
        goal="add logging",
        targets=[{"path": "src/api.py", "intent": "existing"}],
        risk="low",
    )
    loop = ToolLoop(
        reasoning_engine=RevisionNeededEngine(),
        registry=ToolRegistry(shadow_root=tmp_path),
        broadcaster=PatchEventBroadcaster(),
        task_id="t1",
    )
    outcome = await loop.run(step, {}, TaskBudget(), TaskUsage())
    assert isinstance(outcome, PlanHandoff)
    assert outcome.step_id == "s1"
    assert outcome.reason == "function not in planned file"
    assert outcome.evidence == "grep found it in other.py"
    assert outcome.hinted_affected_steps == ["s2"]
