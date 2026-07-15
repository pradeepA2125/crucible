from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


class _RecordingEngine(ScriptedReasoningEngine):
    def __init__(self, responses):
        super().__init__(None, [], controller_step_responses=responses)
        self.seen_histories: list[list] = []

    async def create_controller_step(
        self, plan_context, history, tool_definitions, *, phase, on_thinking=None, on_retry=None):
        self.seen_histories.append(list(history))
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, on_thinking=on_thinking)


@pytest.mark.asyncio
async def test_clarify_then_resume_sees_prior_history(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(tmp_path), title="t")
    eng = _RecordingEngine([
        {"type": "clarify", "thought": "t", "question": "which file?"},   # turn 1
        {"type": "answer", "thought": "t", "answer": "ok, foo.py"},        # turn 2 (resume)
    ])
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=eng, thread_store=store,
                          orchestrator=None, broadcaster=EventBroadcaster(), retrieval_client=None)
    await ctrl.handle_message(th.thread_id, "change the thing", channel_id=f"chat:{th.thread_id}")
    await ctrl.handle_message(th.thread_id, "the foo one", channel_id=f"chat:{th.thread_id}")
    # Turn 2's first step must have seen turn 1's history (clarify resume = feedback resume).
    assert any(h for h in eng.seen_histories[1:]), "resume must seed prior history"
    assert any(m.content == "ok, foo.py" for m in store.get_thread(th.thread_id).messages)


@pytest.mark.asyncio
async def test_accepted_edit_appends_retrieval_delta_without_mutating_seed(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"), patch_engine=PatchEngine())

    async def delta_cb(touched):
        return f"changed: {touched}"

    reg = AggregatingToolRegistry([BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    loop = ControllerLoop(ScriptedReasoningEngine(None, [], controller_step_responses=[
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "s"},
    ]), reg, EventBroadcaster(), channel_id="c", phase_sm=sm, edit_session=sess)
    seed = {"neighbors": ["a.py"]}
    out = await loop.run(
        {"goal": "g", "workspace_path": str(real), "retrieval_seed": seed},
        max_iters=6, auto_accept_edits=True, retrieval_delta_cb=delta_cb)
    assert out.kind == "submit_changes"
    # Append-only delta entry exists in history...
    assert any(h.get("tool") == "retrieval_refresh" for h in (out.history or []))
    # ...and the seed object was never mutated (cache-prefix immutability, spec §6).
    assert seed == {"neighbors": ["a.py"]}
