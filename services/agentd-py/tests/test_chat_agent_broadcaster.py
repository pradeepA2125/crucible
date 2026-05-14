"""Tests for ChatAgent broadcaster-based coroutine and _draft_plan_markdown."""
from __future__ import annotations
import pytest
from pathlib import Path
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster


def _drain(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


class _DoneExploreTransport:
    """Skips explore, classifies to the given intent."""
    def __init__(self, intent: str, text: str = "result") -> None:
        self._intent = intent
        self._text = text

    async def generate_text(self, **_) -> str:
        return self._text

    async def generate_json(self, *, schema_name, **_) -> dict:
        if schema_name == "explore_step":
            return {"action": "done"}
        return {"intent": self._intent, "rationale": "scripted", "likely_targets": []}


class _InlineOrchestrator:
    """Stub orchestrator that records run_inline_change calls."""
    def __init__(self) -> None:
        self.inline_calls: list[dict] = []
        self.task_calls: list[dict] = []

    async def run_inline_change(self, *, thread_id, goal, workspace_path,
                                plan_markdown, explore_context, channel_id, store):
        self.inline_calls.append({
            "thread_id": thread_id, "goal": goal, "plan_markdown": plan_markdown,
            "channel_id": channel_id,
        })

    async def create_task_from_chat(self, *, thread_id, goal, workspace_path,
                                    explore_context, store) -> str:
        self.task_calls.append({"thread_id": thread_id, "goal": goal})
        return "task-from-chat-1"


@pytest.mark.asyncio
async def test_handle_message_is_coroutine_not_generator(tmp_path: Path) -> None:
    """handle_message must return a coroutine, not an async generator."""
    import inspect
    broadcaster = EventBroadcaster()
    store = ChatThreadStore(tmp_path / "chat.db")
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=_DoneExploreTransport("qa"),
        model="test-model",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    coro = agent.handle_message(thread.thread_id, "hi", "ch-x")
    assert inspect.iscoroutine(coro), "handle_message must return a coroutine"
    await coro


@pytest.mark.asyncio
async def test_small_change_calls_run_inline_change(tmp_path: Path) -> None:
    broadcaster = EventBroadcaster()
    broadcaster.subscribe("ch-s1")
    store = ChatThreadStore(tmp_path / "chat.db")
    orch = _InlineOrchestrator()
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=_DoneExploreTransport("small_change", text="- rename foo to bar"),
        model="test-model",
        thread_store=store,
        orchestrator=orch,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    await agent.handle_message(thread.thread_id, "rename foo to bar", channel_id="ch-s1")

    assert len(orch.inline_calls) == 1
    call = orch.inline_calls[0]
    assert call["goal"] == "rename foo to bar"
    assert call["channel_id"] == "ch-s1"
    assert call["plan_markdown"]  # must be non-empty


@pytest.mark.asyncio
async def test_large_change_broadcasts_task_card(tmp_path: Path) -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe("ch-l1")
    store = ChatThreadStore(tmp_path / "chat.db")
    orch = _InlineOrchestrator()
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=_DoneExploreTransport("large_change"),
        model="test-model",
        thread_store=store,
        orchestrator=orch,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    await agent.handle_message(thread.thread_id, "refactor entire module", channel_id="ch-l1")

    assert len(orch.task_calls) == 1
    events = _drain(queue)
    task_card_events = [e for e in events if e["type"] == "task_card"]
    assert len(task_card_events) == 1
    assert task_card_events[0]["payload"]["task_id"] == "task-from-chat-1"


@pytest.mark.asyncio
async def test_draft_plan_markdown_fallback_on_error(tmp_path: Path) -> None:
    """If generate_text fails, _draft_plan_markdown returns goal as bullet point."""
    class FailTransport:
        async def generate_text(self, **_):
            raise RuntimeError("LLM unavailable")

        async def generate_json(self, **_):
            return {"action": "done"}

    broadcaster = EventBroadcaster()
    store = ChatThreadStore(tmp_path / "chat.db")
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=FailTransport(),
        model="test-model",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )
    result = await agent._draft_plan_markdown("fix the bug", [])
    assert "fix the bug" in result
