"""The retrieval seed is computed off the event loop: it is seconds of CPU and filesystem
work, and on the loop it froze every other chat, SSE stream and /live poll."""
import threading
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.retrieval.artifact_client import RetrievalContext


class _RecordingRetrieval:
    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def load_context(self, workspace_path: str, goal: str) -> tuple[RetrievalContext, list[object]]:
        self.threads.append(threading.current_thread())
        return RetrievalContext.empty(), []


@pytest.mark.asyncio
async def test_the_seed_is_computed_on_a_worker_thread_and_pinned(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    thread_id = store.create_thread(str(tmp_path), title="t").thread_id
    retrieval = _RecordingRetrieval()
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        {"type": "answer", "thought": "t", "answer": "hi"}])
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=engine,
                          thread_store=store, orchestrator=None,
                          broadcaster=EventBroadcaster(), retrieval_client=retrieval)

    await ctrl.handle_message(thread_id, "hello", channel_id=f"chat:{thread_id}")

    assert len(retrieval.threads) == 1
    assert retrieval.threads[0] is not threading.main_thread()
    assert store.get_thread(thread_id).controller_retrieval_seed is not None  # type: ignore[union-attr]
