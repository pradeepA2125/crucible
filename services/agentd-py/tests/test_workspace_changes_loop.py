"""ToolOutput.workspace_changes (spec §6.4): a tool that promoted files on the agent's
behalf gets the same bookkeeping as the loop's own accepted edit."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.registry import ToolDefinition, ToolOutput
from agentd.tools.sources import AggregatingToolRegistry


class _ChangingSource:
    name = "fake"

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="fake_dispatch", description="d",
                               parameters={"type": "object", "properties": {}})]

    def owns(self, tool: str) -> bool:
        return tool == "fake_dispatch"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        return ToolOutput(output="children done", workspace_changes=["api/a.py"])


@pytest.mark.asyncio
async def test_workspace_changes_clear_dedup_mark_edited_and_refresh(tmp_path: Path) -> None:
    call = {"type": "tool_call", "thought": "t", "tool": "fake_dispatch", "args": {}}
    steps = [call, call, {"type": "submit_changes", "thought": "d", "summary": "ok"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([_ChangingSource()]), EventBroadcaster(),
        channel_id="c1", phase_sm=ControllerPhaseSM())
    deltas: list[list[str]] = []

    async def delta_cb(files: list[str]) -> str | None:
        deltas.append(files)
        return f"refreshed {files}"

    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6,
                         retrieval_delta_cb=delta_cb)
    assert out.kind == "submit_changes"
    contents = [str(m.get("content", "")) for m in out.history or []]
    assert not any("DUPLICATE BLOCKED" in c for c in contents)  # the repeat was allowed
    assert deltas == [["api/a.py"], ["api/a.py"]]
    assert contents.count("refreshed ['api/a.py']") == 2
    assert loop._edit_applied is True


@pytest.mark.asyncio
async def test_plain_tools_are_untouched(tmp_path: Path) -> None:
    assert ToolOutput(output="x").workspace_changes == []
