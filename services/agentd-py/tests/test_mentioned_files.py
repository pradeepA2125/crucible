from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


class _CapturingEngine(ScriptedReasoningEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.captured_goals: list[str] = []

    async def create_controller_step(self, plan_context, history, tool_definitions, *, phase, on_thinking=None, on_retry=None):
        self.captured_goals.append(plan_context["goal"])
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, on_thinking=on_thinking)


@pytest.mark.asyncio
async def test_mentioned_file_content_folds_into_turn_goal_only(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    engine = _CapturingEngine(
        None, [], controller_step_responses=[
            {"type": "answer", "thought": "t", "answer": "ok"}])
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=engine,
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    await ctrl.handle_message(
        thread.thread_id, "what does this do", channel_id="c1",
        mentioned_files=[{"path": "src/a.py", "content": "x = 1"}])

    # The model saw the file content this turn.
    assert any("src/a.py" in g and "x = 1" in g for g in engine.captured_goals)

    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    user_msg = next(m for m in reloaded.messages if m.role == "user")
    # The persisted/display message stays the short original text — no file
    # content duplicated into chat storage.
    assert user_msg.content == "what does this do"
    assert user_msg.metadata.get("mentioned_files") == ["src/a.py"]


@pytest.mark.asyncio
async def test_no_mentioned_files_is_byte_identical_to_today(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    engine = _CapturingEngine(
        None, [], controller_step_responses=[
            {"type": "answer", "thought": "t", "answer": "ok"}])
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=engine,
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    await ctrl.handle_message(thread.thread_id, "hello", channel_id="c1")

    assert engine.captured_goals == ["hello"]
    reloaded = store.get_thread(thread.thread_id)
    user_msg = next(m for m in reloaded.messages if m.role == "user")
    assert user_msg.content == "hello"
    assert user_msg.metadata == {}
