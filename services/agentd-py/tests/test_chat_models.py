from datetime import datetime, timezone
from agentd.chat.models import (
    ChatMessage, ChatThread, IntentType, IntentClassification, ChatEvent
)

def test_chat_message_defaults():
    msg = ChatMessage(role="user", content="hello")
    assert msg.type == "text"
    assert msg.task_id is None
    assert isinstance(msg.timestamp, datetime)

def test_chat_thread_append():
    thread = ChatThread(thread_id="t1", workspace_path="/ws")
    thread.messages.append(ChatMessage(role="user", content="hi"))
    assert len(thread.messages) == 1

def test_intent_classification_fields():
    ic = IntentClassification(intent=IntentType.QA, rationale="just a question")
    assert ic.intent == IntentType.QA
    assert ic.files_examined == []

def test_chat_event_types():
    e = ChatEvent(type="chat_response", payload={"chunk": "hello"})
    assert e.type == "chat_response"
