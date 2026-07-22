"""C2: `answer` is now reachable from ACTIVE (it wasn't from the old EDIT phase).
It must be blocked ONLY when this turn itself applied an edit AND the ledger still
has pending/in-progress items — never on raw stale ledger presence alone (that
would incorrectly block ordinary Q&A against an unrelated leftover list)."""
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.todo_ledger import TodoLedger
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


def _loop(tmp_path, steps, ledger):
    wm = ShadowWorkspaceManager(tmp_path / "shadows")
    patch_engine = PatchEngine()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    return ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps), reg,
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(), todo_ledger=ledger,
        edit_session_factory=lambda: TurnEditSession(
            turn_id="t1", real_path=tmp_path, workspace_manager=wm, patch_engine=patch_engine))


@pytest.mark.asyncio
async def test_answer_blocked_after_this_turn_edit_with_pending_items(tmp_path: Path):
    ledger = TodoLedger.from_json('[{"title":"do X","status":"pending"}]')
    steps = [
        {"type": "edit", "thought": "start", "patch_ops": [
            {"op": "create_file", "file": "a.txt", "content": "x\n", "reason": "t"}]},
        {"type": "answer", "thought": "done enough", "answer": "I made a.txt"},
        {"type": "submit_changes", "thought": "ok", "summary": "done"},
    ]
    out = await _loop(tmp_path, steps, ledger).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=8, auto_accept_edits=True)
    # The first `answer` must have been rejected (redirected) — the loop keeps going
    # and eventually reaches submit_changes only after the ledger is dealt with, or
    # exhausts trying. At minimum, assert the loop did NOT terminate on that `answer`.
    assert out.kind != "answer" or out.text != "I made a.txt"


@pytest.mark.asyncio
async def test_answer_allowed_for_pure_qa_despite_unrelated_stale_ledger(tmp_path: Path):
    # A non-empty ledger exists (left over from an unrelated earlier turn), but THIS
    # turn never edits — answer must NOT be blocked.
    ledger = TodoLedger.from_json(
        '[{"title":"unrelated leftover item","status":"pending"}]')
    steps = [{"type": "answer", "thought": "just answering", "answer": "The answer is 42."}]
    out = await _loop(tmp_path, steps, ledger).run(
        {"goal": "g", "workspace_path": str(tmp_path)}, max_iters=4)
    assert out.kind == "answer"
    assert out.text == "The answer is 42."
