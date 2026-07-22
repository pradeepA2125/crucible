"""C1 regression: ACTIVE is now the default phase for every plain turn. Before this
fix, TurnEditSession was only built when phase=="EDIT" (entered via resolve_mode),
so a plain "continue" message landing in ACTIVE by default would crash on its first
`edit` dispatch — assert self._edit is not None. The lazy factory must build it
on first use instead."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_active_default_turn_can_edit_via_lazy_factory(tmp_path: Path):
    wm = ShadowWorkspaceManager(tmp_path / "shadows")
    patch_engine = PatchEngine()
    factory_calls = []

    def factory() -> TurnEditSession:
        factory_calls.append(1)
        return TurnEditSession(
            turn_id="t1", real_path=tmp_path, workspace_manager=wm, patch_engine=patch_engine)

    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    steps = [
        {"type": "edit", "thought": "add file", "patch_ops": [
            {"op": "create_file", "file": "hello.txt", "content": "hi\n", "reason": "test"}]},
        {"type": "submit_changes", "thought": "done", "summary": "added hello.txt"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),  # default = ACTIVE
        edit_session_factory=factory)
    out = await loop.run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6, auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert (tmp_path / "hello.txt").read_text() == "hi\n"
    assert factory_calls == [1]  # built exactly once, on first use


@pytest.mark.asyncio
async def test_active_pure_qa_turn_never_builds_the_factory(tmp_path: Path):
    calls = []

    def factory() -> TurnEditSession:
        calls.append(1)
        raise AssertionError("factory must not be called for a pure Q&A turn")

    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    steps = [{"type": "answer", "thought": "ok", "answer": "hello"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),
        edit_session_factory=factory)
    out = await loop.run({"goal": "g", "workspace_path": str(tmp_path)}, max_iters=6)
    assert out.kind == "answer"
    assert calls == []
