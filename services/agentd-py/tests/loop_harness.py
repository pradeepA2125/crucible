"""A child ControllerLoop over a scripted engine, for loop-level tests."""
from pathlib import Path
from typing import Any

from agentd.chat.controller_loop import ControllerLoop, ControllerOutcome
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.subagents.context import AgentContext
from agentd.tools.sources import AggregatingToolRegistry


def child_context(label: str = "kid") -> AgentContext:
    return AgentContext(
        agent_id="agent-kid", name="general-purpose", label=label, depth=1,
        parent_agent_id=None, permission="default",
        allowed_types=("tool_call", "edit", "progress", "report"), persona="", max_iters=8)


async def run_child_loop(
    tmp_path: Path, script: list[dict[str, object]], *, max_iters: int = 8,
    engine: ScriptedReasoningEngine | None = None, **run_kwargs: Any,
) -> ControllerOutcome:
    engine = engine or ScriptedReasoningEngine(None, [], controller_step_responses=script)
    loop = ControllerLoop(
        engine, AggregatingToolRegistry([]), EventBroadcaster(), channel_id="chat:t:agent:k",
        phase_sm=ControllerPhaseSM(start="AGENT"), agent=child_context())
    return await loop.run(
        {"goal": "task", "workspace_path": str(tmp_path), "run_id": "t:k"},
        max_iters=max_iters, **run_kwargs)
