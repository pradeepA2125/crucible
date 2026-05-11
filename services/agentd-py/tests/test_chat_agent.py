import pytest
from pathlib import Path
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore


class ScriptedTransport:
    """
    generate_json is called for both the explore loop and the classifier.
    Distinguish via schema_name: "explore_step" vs "intent_classification".
    """
    def __init__(self, text_response: str = "It handles login.") -> None:
        self._text = text_response

    async def generate_text(self, *, model, system_instructions, user_payload) -> str:
        return self._text

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload) -> dict:
        if schema_name == "explore_step":
            return {"action": "done"}  # skip exploration in tests
        return {"intent": "qa", "rationale": "scripted", "likely_targets": []}


@pytest.fixture
def store(tmp_path: Path) -> ChatThreadStore:
    return ChatThreadStore(tmp_path / "chat.db")


@pytest.mark.asyncio
async def test_qa_streams_response(tmp_path: Path, store: ChatThreadStore) -> None:
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=ScriptedTransport("It handles login."),
        model="test-model",
        thread_store=store,
        orchestrator=None,
    )
    thread = store.create_thread(str(tmp_path))
    events = []
    async for event in agent.handle_message(thread.thread_id, "What does auth do?"):
        events.append(event)

    types = [e.type for e in events]
    assert "chat_agent_thinking" in types   # user sees activity immediately
    assert "intent_classified" in types
    assert "chat_response" in types
    assert "chat_done" in types
    assert any("login" in e.payload.get("chunk", "")
               for e in events if e.type == "chat_response")


@pytest.mark.asyncio
async def test_explore_tool_calls_yield_events(tmp_path: Path, store: ChatThreadStore) -> None:
    """Each tool call during explore must emit an explore_tool_call event."""
    class OneToolTransport:
        async def generate_text(self, **_) -> str:
            return "answer"

        async def generate_json(self, *, model, schema_name, schema,
                                system_instructions, user_payload) -> dict:
            if schema_name == "explore_step":
                if not user_payload.get("tool_results"):
                    return {"action": "tool_call", "tool": "search_code",
                            "args": {"pattern": "auth"}}
                return {"action": "done"}
            return {"intent": "qa", "rationale": "ok", "likely_targets": []}

    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=OneToolTransport(),
        model="test-model",
        thread_store=store,
        orchestrator=None,
    )
    thread = store.create_thread(str(tmp_path))
    events = []
    async for event in agent.handle_message(thread.thread_id, "What does auth do?"):
        events.append(event)

    tool_events = [e for e in events if e.type == "explore_tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0].payload["tool"] == "search_code"


@pytest.mark.asyncio
async def test_explore_context_passed_to_classifier(tmp_path: Path, store: ChatThreadStore) -> None:
    classifier_payloads: list[dict] = []

    class CapturingTransport:
        async def generate_text(self, **_) -> str:
            return "answer"

        async def generate_json(self, *, model, schema_name, schema,
                                system_instructions, user_payload) -> dict:
            if schema_name == "explore_step":
                if not user_payload.get("tool_results"):
                    return {"action": "tool_call", "tool": "search_code",
                            "args": {"pattern": "auth"}}
                return {"action": "done"}
            classifier_payloads.append(user_payload)
            return {"intent": "qa", "rationale": "ok", "likely_targets": []}

    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=CapturingTransport(),
        model="test-model",
        thread_store=store,
        orchestrator=None,
    )
    thread = store.create_thread(str(tmp_path))
    async for _ in agent.handle_message(thread.thread_id, "What does auth do?"):
        pass

    assert len(classifier_payloads) == 1
    assert classifier_payloads[0]["explore_context"]  # search result injected


@pytest.mark.asyncio
async def test_qa_persists_both_messages(tmp_path: Path, store: ChatThreadStore) -> None:
    agent = ChatAgent(
        workspace_path=str(tmp_path),
        transport=ScriptedTransport("Answer."),
        model="test-model",
        thread_store=store,
        orchestrator=None,
    )
    thread = store.create_thread(str(tmp_path))
    async for _ in agent.handle_message(thread.thread_id, "Explain this"):
        pass

    reloaded = store.get_thread(thread.thread_id)
    assert len(reloaded.messages) == 2
    assert reloaded.messages[0].role == "user"
    assert reloaded.messages[1].role == "agent"
