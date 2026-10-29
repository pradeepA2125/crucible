"""Agents learn that Auto approval is on (spec §6.1): the main agent through the payload
tail (its cached system prompt never changes), children through their tagged prompts."""
from __future__ import annotations

import pytest

from agentd.chat.controller_prompts import AUTO_MODE_NOTE, build_controller_step_payload
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext, render_prompt
from agentd.subagents.context import AgentContext
from agentd.tools.registry import _RUN_COMMAND_DESCRIPTION
from tests.loop_harness import run_child_loop


def _payload(mode: str) -> dict[str, object]:
    ctx = {"goal": "g", "iteration": 1, "max_iters": 10, "approval_mode": mode}
    return build_controller_step_payload(ctx, [], [], phase="ACTIVE")


def test_payload_has_auto_note_only_in_auto():
    assert "approval_mode_note" not in _payload("ask")
    assert "approval_mode_note" not in _payload("allow_all")
    assert _payload("auto")["approval_mode_note"] == AUTO_MODE_NOTE


def test_auto_note_sits_in_the_dynamic_tail():
    keys = list(_payload("auto"))
    assert keys.index("approval_mode_note") > keys.index("goal")
    assert keys.index("approval_mode_note") < keys.index("instruction")


def _child(permission: str) -> RenderContext:
    agent = AgentContext(agent_id="a", name="general-purpose", label="x", depth=1,
                         parent_agent_id=None, permission=permission,
                         allowed_types=("tool_calls", "progress", "report"), persona="",
                         max_iters=10)
    return RenderContext.for_agent(agent, tools=frozenset({"run_command"}), shell_policy="auto")


@pytest.mark.parametrize("permission", ["default", "acceptEdits", "dontAsk"])
def test_child_run_command_description_mentions_auto(permission):
    rendered = render_prompt(_RUN_COMMAND_DESCRIPTION, _child(permission))
    assert "Auto" in rendered


class _RecordingEngine(ScriptedReasoningEngine):
    def __init__(self, script: list[dict[str, object]]) -> None:
        super().__init__(None, [], controller_step_responses=script)
        self.modes: list[object] = []

    async def create_controller_step(self, plan_context, *args, **kwargs):
        self.modes.append(plan_context.get("approval_mode"))
        return await super().create_controller_step(plan_context, *args, **kwargs)


@pytest.mark.asyncio
async def test_loop_rereads_approval_mode_every_iteration(tmp_path):
    engine = _RecordingEngine([
        {"type": "progress", "thought": "t", "message": "working"},
        {"type": "report", "thought": "t", "status": "completed", "summary": "done"},
    ])
    modes = iter(["ask", "auto"])
    await run_child_loop(tmp_path, [], engine=engine, approval_mode=lambda: next(modes))
    assert engine.modes == ["ask", "auto"]


def test_main_never_sees_auto_tag_text():
    rendered = render_prompt(_RUN_COMMAND_DESCRIPTION, RenderContext.main())
    assert "Auto" not in rendered
