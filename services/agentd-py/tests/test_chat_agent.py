import pytest
from pathlib import Path
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster


class ScriptedTransport:
    """
    generate_json is called for both the explore loop and the classifier.
    Distinguish via schema_name: "explore_step" vs "intent_classification".
    """
    def __init__(self, text_response: str = "It handles login.") -> None:
        self._text = text_response

    async def generate_text(self, *, model, system_instructions, user_payload, on_thinking=None) -> str:
        return self._text

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload, on_thinking=None) -> dict:
        if schema_name == "explore_step":
            return {"action": "done"}  # skip exploration in tests
        return {"intent": "qa", "rationale": "scripted", "likely_targets": []}


@pytest.fixture
def store(tmp_path: Path) -> ChatThreadStore:
    return ChatThreadStore(tmp_path / "chat.db")


def _make_agent(tmp_path, transport, broadcaster, orchestrator=None):
    return ChatAgent(
        workspace_path=str(tmp_path),
        transport=transport,
        model="test-model",
        thread_store=ChatThreadStore(tmp_path / "chat.db"),
        orchestrator=orchestrator,
        broadcaster=broadcaster,
    )


def _drain(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_qa_broadcasts_response(tmp_path: Path, store: ChatThreadStore) -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe("ch-1")
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=ScriptedTransport("It handles login."),
        model="test-model",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    await agent.handle_message(thread.thread_id, "What does auth do?", channel_id="ch-1")

    events = _drain(queue)
    types = [e["type"] for e in events]
    assert "chat_agent_thinking" in types
    assert "intent_classified" in types
    assert "chat_response" in types
    assert "chat_done" in types
    assert any("login" in e["payload"].get("chunk", "")
               for e in events if e["type"] == "chat_response")


@pytest.mark.asyncio
async def test_explore_tool_calls_broadcast_events(tmp_path: Path, store: ChatThreadStore) -> None:
    """Each tool call during explore must emit an explore_tool_call event."""
    class OneToolTransport:
        async def generate_text(self, **_) -> str:
            return "answer"

        async def generate_json(self, *, model, schema_name, schema,
                                system_instructions, user_payload, on_thinking=None) -> dict:
            if schema_name == "explore_step":
                if not user_payload.get("tool_results"):
                    return {"thought": "Looking for auth patterns", "action": "tool_call",
                            "tool": "search_code", "args": {"pattern": "auth"}}
                return {"thought": "Have enough context", "action": "done"}
            return {"intent": "qa", "rationale": "ok", "likely_targets": []}

    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe("ch-2")
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=OneToolTransport(),
        model="test-model",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    await agent.handle_message(thread.thread_id, "What does auth do?", channel_id="ch-2")

    events = _drain(queue)
    tool_events = [e for e in events if e["type"] == "explore_tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0]["payload"]["tool"] == "search_code"
    assert tool_events[0]["payload"]["thought"] == "Looking for auth patterns"


@pytest.mark.asyncio
async def test_explore_context_passed_to_classifier(tmp_path: Path, store: ChatThreadStore) -> None:
    classifier_payloads: list[dict] = []

    class CapturingTransport:
        async def generate_text(self, **_) -> str:
            return "answer"

        async def generate_json(self, *, model, schema_name, schema,
                                system_instructions, user_payload, on_thinking=None) -> dict:
            if schema_name == "explore_step":
                if not user_payload.get("tool_results"):
                    return {"thought": "Looking for context", "action": "tool_call",
                            "tool": "search_code", "args": {"pattern": "auth"}}
                return {"action": "done"}
            classifier_payloads.append(user_payload)
            return {"intent": "qa", "rationale": "ok", "likely_targets": []}

    broadcaster = EventBroadcaster()
    broadcaster.subscribe("ch-3")
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=CapturingTransport(),
        model="test-model",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    await agent.handle_message(thread.thread_id, "What does auth do?", channel_id="ch-3")

    assert len(classifier_payloads) == 1
    assert classifier_payloads[0]["explore_context"]  # search result injected


@pytest.mark.asyncio
async def test_qa_persists_both_messages(tmp_path: Path, store: ChatThreadStore) -> None:
    broadcaster = EventBroadcaster()
    broadcaster.subscribe("ch-4")
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=ScriptedTransport("Answer."),
        model="test-model",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )
    thread = store.create_thread(str(tmp_path))
    await agent.handle_message(thread.thread_id, "Explain this", channel_id="ch-4")

    reloaded = store.get_thread(thread.thread_id)
    assert len(reloaded.messages) == 2
    assert reloaded.messages[0].role == "user"
    assert reloaded.messages[1].role == "agent"
