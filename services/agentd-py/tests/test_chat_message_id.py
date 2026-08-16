"""ChatMessage.id — the stable anchor chat rewind hangs checkpoints on."""
from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore


def test_legacy_message_dict_parses_with_null_id():
    """A message persisted before this feature has no 'id' key. It must parse to
    None and stay None across repeated validation — a default_factory would mint a
    fresh random id per read, producing rewind anchors that 404 differently each poll."""
    legacy = {"role": "user", "content": "hi", "type": "text",
              "timestamp": "2026-08-16T00:00:00+00:00", "metadata": {}}
    first = ChatMessage.model_validate(legacy)
    second = ChatMessage.model_validate(legacy)
    assert first.id is None
    assert second.id is None


def test_append_message_stamps_and_returns_id(tmp_path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    returned = store.append_message(thread.thread_id, ChatMessage(role="user", content="hi"))
    reloaded = store.get_thread(thread.thread_id)
    assert returned is not None
    assert reloaded.messages[0].id == returned
