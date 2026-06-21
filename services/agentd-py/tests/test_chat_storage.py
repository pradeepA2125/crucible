import pytest
from pathlib import Path
from agentd.chat.models import ChatMessage, ChatThread
from agentd.chat.storage import ChatThreadStore

@pytest.fixture
def store(tmp_path: Path) -> ChatThreadStore:
    return ChatThreadStore(tmp_path / "chat.db")

def test_create_thread_returns_empty_thread(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    assert thread.workspace_path == "/ws/project"
    assert thread.messages == []
    assert thread.title == "New Chat"

def test_multiple_threads_per_workspace(store: ChatThreadStore) -> None:
    t1 = store.create_thread("/ws/project", title="First chat")
    t2 = store.create_thread("/ws/project", title="Second chat")
    assert t1.thread_id != t2.thread_id
    threads = store.list_threads("/ws/project")
    assert len(threads) == 2

def test_list_threads_returns_newest_first(store: ChatThreadStore) -> None:
    store.create_thread("/ws/project", title="Old")
    store.create_thread("/ws/project", title="New")
    threads = store.list_threads("/ws/project")
    assert threads[0].title == "New"

def test_list_threads_isolates_by_workspace(store: ChatThreadStore) -> None:
    store.create_thread("/ws/alpha")
    store.create_thread("/ws/beta")
    assert len(store.list_threads("/ws/alpha")) == 1
    assert len(store.list_threads("/ws/beta")) == 1

def test_append_message_persists(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    msg = ChatMessage(role="user", content="hello")
    store.append_message(thread.thread_id, msg)

    reloaded = store.get_thread(thread.thread_id)
    assert len(reloaded.messages) == 1
    assert reloaded.messages[0].content == "hello"

def test_update_touched_files(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    store.add_touched_file(thread.thread_id, "src/foo.py")
    store.add_touched_file(thread.thread_id, "src/bar.py")

    reloaded = store.get_thread(thread.thread_id)
    assert "src/foo.py" in reloaded.touched_files
    assert "src/bar.py" in reloaded.touched_files

def test_update_title(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    store.update_title(thread.thread_id, "Add auth layer")
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded.title == "Add auth layer"

def test_active_task_id_defaults_to_none(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    assert thread.active_task_id is None
    assert store.get_thread(thread.thread_id).active_task_id is None

def test_set_active_task_persists(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    store.set_active_task(thread.thread_id, "task-abc")
    assert store.get_thread(thread.thread_id).active_task_id == "task-abc"
    # And it survives in the list view (used by the UI to follow task-id churn).
    listed = store.list_threads("/ws/project")[0]
    assert listed.active_task_id == "task-abc"

def test_set_active_task_overwrites_on_resume(store: ChatThreadStore) -> None:
    thread = store.create_thread("/ws/project")
    store.set_active_task(thread.thread_id, "task-parent")
    store.set_active_task(thread.thread_id, "task-child")  # resume churns the id
    assert store.get_thread(thread.thread_id).active_task_id == "task-child"


def test_upsert_inflight_pills_creates_then_updates(store: ChatThreadStore) -> None:
    # Mid-turn pill persistence (finding 5): the first upsert appends a pills-only
    # agent message tagged with the turn id; subsequent upserts for the SAME turn
    # update that message in place (no duplicate), so getChatThread reconstructs the
    # in-flight pills on a switch-away/reopen before the turn completes.
    thread = store.create_thread("/ws/project")
    tid = thread.thread_id

    store.upsert_inflight_pills(tid, "turn-1", [{"id": "c0", "tool": "read_file"}])
    msgs = store.get_thread(tid).messages
    pill_msgs = [m for m in msgs if (m.metadata or {}).get("inflight_turn_id") == "turn-1"]
    assert len(pill_msgs) == 1
    assert [p["id"] for p in pill_msgs[0].metadata["tool_events"]] == ["c0"]

    # Same turn → update in place (still ONE message), pills grow.
    store.upsert_inflight_pills(
        tid, "turn-1", [{"id": "c0", "tool": "read_file"}, {"id": "c1", "tool": "search_code"}])
    msgs = store.get_thread(tid).messages
    pill_msgs = [m for m in msgs if (m.metadata or {}).get("inflight_turn_id") == "turn-1"]
    assert len(pill_msgs) == 1
    assert [p["id"] for p in pill_msgs[0].metadata["tool_events"]] == ["c0", "c1"]

    # A different turn → a NEW message.
    store.upsert_inflight_pills(tid, "turn-2", [{"id": "c0", "tool": "read_file"}])
    msgs = store.get_thread(tid).messages
    assert len([m for m in msgs if (m.metadata or {}).get("inflight_turn_id")]) == 2


def test_clear_inflight_markers_drops_marker_keeps_pills(store: ChatThreadStore) -> None:
    # A prior turn left an in-flight pills message (orphaned before finalize). Clearing
    # markers at the next turn's start drops the marker but KEEPS the pills (finding 5).
    thread = store.create_thread("/ws/project")
    tid = thread.thread_id
    store.upsert_inflight_pills(tid, "old-turn", [{"id": 0, "tool": "read_file"}])
    store.clear_inflight_markers(tid)
    msgs = store.get_thread(tid).messages
    # Marker gone everywhere…
    assert all(not (m.metadata or {}).get("inflight_turn_id") for m in msgs)
    # …but the pills survive as a normal agent message.
    pill_msgs = [m for m in msgs if (m.metadata or {}).get("tool_events")]
    assert len(pill_msgs) == 1
    assert pill_msgs[0].metadata["tool_events"][0]["id"] == 0
