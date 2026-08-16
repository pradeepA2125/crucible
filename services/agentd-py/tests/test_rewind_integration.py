"""End-to-end: a real PatchEngine writing real files on a real tmp_path workspace.

Deliberately NOT InMemoryTaskStore — it hands back the same object reference and would
mask exactly the state-divergence bugs this wiring can produce.
"""
import pytest

from agentd.chat.edit_session import TurnEditSession
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.patch.engine import PatchEngine
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_capture_runs_before_the_patch_lands(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("before\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), "t")
    rewind = RewindStore(store, ws)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    session = TurnEditSession(
        turn_id=thread.thread_id, real_path=ws,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
        checkpoint_cb=lambda paths: rewind.capture(thread.thread_id, paths),
    )
    await session.apply([{"op": "search_replace", "file": "a.py",
                          "search": "before", "replace": "after", "reason": "r"}])
    await session.accept()
    await session.close()

    assert (ws / "a.py").read_text() == "after\n"
    outcome = rewind.restore(thread.thread_id, msg_id)
    assert (ws / "a.py").read_text() == "before\n"
    assert outcome.restored_files == ["a.py"]


@pytest.mark.asyncio
async def test_handle_message_opens_a_checkpoint_anchored_on_the_user_message(tmp_path):
    """The wiring everything else rests on: a real turn must leave a rewind point
    anchored on the user message it started from, holding pre-turn controller state."""
    from agentd.chat.controller import ChatController
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine

    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_history(thread.thread_id, [{"role": "user", "content": "before"}])
    rewind = RewindStore(store, tmp_path)
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "answer", "thought": "t", "answer": "hello"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None, rewind_store=rewind)

    await ctrl.handle_message(thread.thread_id, "hi", channel_id="c1")

    reloaded = store.get_thread(thread.thread_id)
    user_msg = next(m for m in reloaded.messages if m.role == "user")
    checkpoints = store.list_checkpoints(thread.thread_id)
    assert len(checkpoints) == 1
    assert checkpoints[0].anchor_message_id == user_msg.id
    # Pre-turn history, not the post-turn value the loop wrote.
    assert checkpoints[0].controller_history_json == '[{"role": "user", "content": "before"}]'


@pytest.mark.asyncio
async def test_session_without_checkpoint_cb_still_works(tmp_path):
    """The cb is optional — the orphan-recovery and test paths build sessions without it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("before\n")
    session = TurnEditSession(
        turn_id="t", real_path=ws,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
    )
    entries = await session.apply([{"op": "search_replace", "file": "a.py",
                                    "search": "before", "replace": "after", "reason": "r"}])
    await session.close()
    assert [e.path for e in entries] == ["a.py"]
