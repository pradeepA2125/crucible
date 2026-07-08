# Chat Agent Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ChatAgent` backend — domain models, thread storage, intent classifier, Q&A path, and API endpoints — so the VS Code panel has a real backend to talk to.

**Architecture:** A new `agentd/chat/` package owns all chat concerns. `ChatAgent` owns a `ToolRegistry` (read-only) and runs a bounded explore phase on every message — ripgrep, read_file, list_directory — accumulating workspace context before doing anything else. It then calls `IntentClassifier` (a pure single-call decision function, no tools) with the gathered context + conversation history, and routes to Q&A, small-change, or large-change. The Q&A path reuses the already-gathered context directly in the answer prompt, avoiding any re-read. Thread history is persisted in SQLite alongside the task store.

**Tech Stack:** Python, FastAPI, Pydantic, SQLite, existing `ReasoningEngine` transport layer, existing `ToolRegistry`.

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `agentd/chat/__init__.py` | Create | Package marker |
| `agentd/chat/models.py` | Create | `ChatMessage`, `ChatThread`, `IntentType`, `IntentClassification`, `ChatEvent` |
| `agentd/chat/storage.py` | Create | `ChatThreadStore` — SQLite-backed thread persistence |
| `agentd/chat/classifier.py` | Create | `IntentClassifier` — pure single-call classifier; takes pre-gathered context + history |
| `agentd/chat/agent.py` | Create | `ChatAgent` — owns `ToolRegistry`, runs explore phase, routes messages, Q&A path |
| `agentd/api/routes.py` | Modify | Add `GET /v1/chat/threads`, `POST /v1/chat/threads`, `GET /v1/chat/threads/{id}`, `POST /v1/chat/threads/{id}/message` |
| `agentd/main.py` | Modify | Instantiate `ChatAgent`, pass to router |
| `tests/test_chat_models.py` | Create | Model validation tests |
| `tests/test_chat_storage.py` | Create | SQLite store tests |
| `tests/test_chat_classifier.py` | Create | Classifier routing tests (scripted LLM) |
| `tests/test_chat_agent.py` | Create | Q&A path end-to-end test |
| `tests/test_chat_routes.py` | Create | API endpoint tests |

---

## Task 1: Domain Models

**Files:**
- Create: `services/agentd-py/agentd/chat/models.py`
- Create: `services/agentd-py/agentd/chat/__init__.py`
- Create: `services/agentd-py/tests/test_chat_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_models.py
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_chat_models.py -v
```
Expected: `ModuleNotFoundError: No module named 'agentd.chat'`

- [ ] **Step 3: Create package and models**

```python
# agentd/chat/__init__.py
"""Chat interface package."""
```

```python
# agentd/chat/models.py
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IntentType(StrEnum):
    QA = "qa"
    SMALL_CHANGE = "small_change"
    LARGE_CHANGE = "large_change"


class IntentClassification(BaseModel):
    intent: IntentType
    rationale: str
    files_examined: list[str] = Field(default_factory=list)
    likely_targets: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["user", "agent"]
    content: str
    type: Literal["text", "plan_card", "diff_card", "diff_summary"] = "text"
    task_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatThread(BaseModel):
    thread_id: str
    workspace_path: str
    title: str = "New Chat"
    messages: list[ChatMessage] = Field(default_factory=list)
    touched_files: list[str] = Field(default_factory=list)


class ChatEvent(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_chat_models.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/__init__.py agentd/chat/models.py tests/test_chat_models.py
git commit -m "feat(chat): domain models — ChatMessage, ChatThread, IntentClassification"
```

---

## Task 2: Chat Thread Storage

**Files:**
- Create: `services/agentd-py/agentd/chat/storage.py`
- Create: `services/agentd-py/tests/test_chat_storage.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_storage.py
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_chat_storage.py -v
```
Expected: `ImportError: cannot import name 'ChatThreadStore'`

- [ ] **Step 3: Implement storage**

```python
# agentd/chat/storage.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentd.chat.models import ChatMessage, ChatThread


class ChatThreadStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                thread_id TEXT PRIMARY KEY,
                workspace_path TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                messages_json TEXT NOT NULL DEFAULT '[]',
                touched_files_json TEXT NOT NULL DEFAULT '[]'
            );
        """)
        self._conn.commit()

    def create_thread(self, workspace_path: str, title: str = "New Chat") -> ChatThread:
        thread_id = f"chat-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO chat_threads (thread_id, workspace_path, title, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, workspace_path, title, created_at),
        )
        self._conn.commit()
        return ChatThread(thread_id=thread_id, workspace_path=workspace_path, title=title)

    def list_threads(self, workspace_path: str) -> list[ChatThread]:
        rows = self._conn.execute(
            "SELECT * FROM chat_threads WHERE workspace_path = ? ORDER BY created_at DESC",
            (workspace_path,),
        ).fetchall()
        return [
            ChatThread(
                thread_id=row["thread_id"],
                workspace_path=row["workspace_path"],
                title=row["title"],
                messages=[ChatMessage.model_validate(m) for m in json.loads(row["messages_json"])],
                touched_files=json.loads(row["touched_files_json"]),
            )
            for row in rows
        ]

    def get_thread(self, thread_id: str) -> ChatThread | None:
        row = self._conn.execute(
            "SELECT * FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        return ChatThread(
            thread_id=row["thread_id"],
            workspace_path=row["workspace_path"],
            title=row["title"],
            messages=[ChatMessage.model_validate(m) for m in json.loads(row["messages_json"])],
            touched_files=json.loads(row["touched_files_json"]),
        )

    def append_message(self, thread_id: str, message: ChatMessage) -> None:
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        messages = json.loads(row["messages_json"])
        messages.append(message.model_dump(mode="json"))
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(messages), thread_id),
        )
        self._conn.commit()

    def update_title(self, thread_id: str, title: str) -> None:
        self._conn.execute(
            "UPDATE chat_threads SET title = ? WHERE thread_id = ?", (title, thread_id)
        )
        self._conn.commit()

    def add_touched_file(self, thread_id: str, file_path: str) -> None:
        row = self._conn.execute(
            "SELECT touched_files_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        files: list[str] = json.loads(row["touched_files_json"])
        if file_path not in files:
            files.append(file_path)
        self._conn.execute(
            "UPDATE chat_threads SET touched_files_json = ? WHERE thread_id = ?",
            (json.dumps(files), thread_id),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_chat_storage.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/storage.py tests/test_chat_storage.py
git commit -m "feat(chat): SQLite chat thread storage"
```

---

## Task 3: Intent Classifier

`IntentClassifier` is a **pure decision function** — one structured LLM call, no tools, no loop.
`ChatAgent` runs the explore phase first (Task 4) and passes the accumulated workspace context
plus conversation history into `classify()`. The classifier just looks at the evidence and decides.

**Files:**
- Create: `services/agentd-py/agentd/chat/classifier.py`
- Create: `services/agentd-py/tests/test_chat_classifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_classifier.py
import pytest
from agentd.chat.classifier import IntentClassifier
from agentd.chat.models import IntentType


class ScriptedTransport:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload) -> dict:
        return self._response


@pytest.mark.asyncio
async def test_plan_prefix_forces_large_change() -> None:
    classifier = IntentClassifier(transport=ScriptedTransport({}), model="test-model")
    result = await classifier.classify("/plan add a caching layer", context=[], history=[])
    assert result.intent == IntentType.LARGE_CHANGE


@pytest.mark.asyncio
async def test_classifier_returns_qa() -> None:
    classifier = IntentClassifier(
        transport=ScriptedTransport(
            {"intent": "qa", "rationale": "pure question", "likely_targets": []}
        ),
        model="test-model",
    )
    result = await classifier.classify("What does auth do?", context=[], history=[])
    assert result.intent == IntentType.QA


@pytest.mark.asyncio
async def test_classifier_returns_small_change() -> None:
    classifier = IntentClassifier(
        transport=ScriptedTransport(
            {"intent": "small_change", "rationale": "one file", "likely_targets": ["auth.py"]}
        ),
        model="test-model",
    )
    context = [{"tool": "search_code", "result": "auth.py:5: def authenticate"}]
    result = await classifier.classify("fix authenticate", context=context, history=[])
    assert result.intent == IntentType.SMALL_CHANGE
    assert result.likely_targets == ["auth.py"]


@pytest.mark.asyncio
async def test_classifier_receives_context_and_history_in_payload() -> None:
    received: list[dict] = []

    class CapturingTransport:
        async def generate_json(self, *, model, schema_name, schema,
                                system_instructions, user_payload) -> dict:
            received.append(user_payload)
            return {"intent": "small_change", "rationale": "ok", "likely_targets": []}

    classifier = IntentClassifier(transport=CapturingTransport(), model="test-model")
    context = [{"tool": "read_file", "result": "TIMEOUT = 10"}]
    history = [{"role": "user", "content": "look at config.py"}]
    await classifier.classify("fix that", context=context, history=history)
    assert received[0]["explore_context"] == context
    assert received[0]["conversation_history"] == history


@pytest.mark.asyncio
async def test_classifier_defaults_to_large_change_on_error() -> None:
    class FailingTransport:
        async def generate_json(self, **_) -> dict:
            raise RuntimeError("LLM down")

    classifier = IntentClassifier(transport=FailingTransport(), model="test-model")
    result = await classifier.classify("do something", context=[], history=[])
    assert result.intent == IntentType.LARGE_CHANGE
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_chat_classifier.py -v
```
Expected: `ImportError: cannot import name 'IntentClassifier'`

- [ ] **Step 3: Implement classifier**

```python
# agentd/chat/classifier.py
from __future__ import annotations

import logging
from typing import Any

from agentd.chat.models import IntentClassification, IntentType

logger = logging.getLogger(__name__)

_CLASSIFY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["qa", "small_change", "large_change"]},
        "rationale": {"type": "string"},
        "likely_targets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "rationale", "likely_targets"],
}

_SYSTEM_PROMPT = """\
You are classifying a user's chat message to decide the execution path:
  qa           — question or discussion, no file changes needed
  small_change — 1-2 files, localised edit, no interface or schema changes
  large_change — 3+ files, interface/schema changes, new files, or ambiguous scope

You receive:
  conversation_history — recent messages; use to resolve "fix that", "also update tests", etc.
  explore_context      — files already read and search results gathered from the workspace

Count distinct files in explore_context to judge scope. Be conservative — prefer large_change
when scope is unclear.
"""


class IntentClassifier:
    def __init__(self, *, transport: Any, model: str) -> None:
        self._transport = transport
        self._model = model

    async def classify(
        self,
        message: str,
        context: list[dict[str, str]],
        history: list[dict[str, str]],
    ) -> IntentClassification:
        if message.strip().startswith("/plan"):
            return IntentClassification(
                intent=IntentType.LARGE_CHANGE,
                rationale="/plan prefix — forced large_change routing",
            )
        try:
            result = await self._transport.generate_json(
                model=self._model,
                schema_name="intent_classification",
                schema=_CLASSIFY_SCHEMA,
                system_instructions=_SYSTEM_PROMPT,
                user_payload={
                    "message": message,
                    "conversation_history": history[-10:],
                    "explore_context": context,
                },
            )
            return IntentClassification(
                intent=IntentType(result["intent"]),
                rationale=result.get("rationale", ""),
                likely_targets=result.get("likely_targets", []),
            )
        except Exception:
            logger.exception("Intent classification failed — defaulting to large_change")
            return IntentClassification(
                intent=IntentType.LARGE_CHANGE,
                rationale="classification error — safe default",
            )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_chat_classifier.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/classifier.py tests/test_chat_classifier.py
git commit -m "feat(chat): IntentClassifier — pure single-call classifier, context+history from ChatAgent"
```

---

## Task 4: ChatAgent — Explore Phase + Q&A Path

`ChatAgent` owns the `ToolRegistry` and runs a bounded explore loop before doing anything else.
The loop is **inlined in `_handle`** (not a separate method) so it can `yield` progress events at
each step — `chat_agent_thinking` at start, `explore_tool_call` per tool invocation. Without these
the user sees nothing for several seconds and thinks the UI is frozen. The accumulated context
flows to `IntentClassifier` (single call, no tools) and into the Q&A answer prompt — no re-reads.

**Files:**
- Create: `services/agentd-py/agentd/chat/agent.py`
- Create: `services/agentd-py/tests/test_chat_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_agent.py
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_chat_agent.py -v
```
Expected: `ImportError: cannot import name 'ChatAgent'`

- [ ] **Step 3: Implement ChatAgent**

```python
# agentd/chat/agent.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, AsyncIterator

from agentd.chat.classifier import IntentClassifier
from agentd.chat.models import ChatEvent, ChatMessage, IntentType
from agentd.chat.storage import ChatThreadStore
from agentd.planning.registry import PlanningToolRegistry

logger = logging.getLogger(__name__)

_EXPLORE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "done"]},
        "tool": {"type": "string",
                 "enum": ["search_code", "list_directory", "read_file", "search_semantic"]},
        "args": {"type": "object"},
    },
    "required": ["action"],
}

_EXPLORE_PROMPT = """\
You are exploring a codebase to gather context before classifying a user request.
Use tools to find relevant files, symbols, and usages mentioned in the message and history.
When you have enough evidence to judge scope, emit action=done.

Tools: search_code (ripgrep), list_directory, read_file, search_semantic.
Cap: you will be stopped after a fixed number of calls regardless.
Never modify files.
"""

_QA_PROMPT = """\
You are an expert code assistant. Answer the user's question about the codebase.
Use the workspace context below — files and search results already gathered.
Be concise and specific. Name files and functions explicitly.
"""


class ChatAgent:
    def __init__(
        self,
        *,
        workspace_path: str,
        transport: Any,
        model: str,
        thread_store: ChatThreadStore,
        orchestrator: Any | None,
        max_explore_calls: int = 5,
    ) -> None:
        self._workspace_path = workspace_path
        self._transport = transport
        self._model = model
        self._store = thread_store
        self._orchestrator = orchestrator
        self._max_explore_calls = max_explore_calls
        self._registry = PlanningToolRegistry(real_path=Path(workspace_path))
        self._classifier = IntentClassifier(transport=transport, model=model)

    async def handle_message(self, thread_id: str, message: str) -> AsyncIterator[ChatEvent]:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id!r} not found")

        user_msg = ChatMessage(role="user", content=message)
        self._store.append_message(thread_id, user_msg)

        history = [{"role": m.role, "content": m.content} for m in thread.messages]

        # Explore phase — inlined so we can yield progress events at each step.
        # Without these the user sees nothing for several seconds and thinks the UI is frozen.
        context: list[dict[str, str]] = []
        files_examined: list[str] = []

        yield ChatEvent(type="chat_agent_thinking", payload={"message": "Exploring workspace…"})

        for _ in range(self._max_explore_calls):
            try:
                step = await self._transport.generate_json(
                    model=self._model,
                    schema_name="explore_step",
                    schema=_EXPLORE_SCHEMA,
                    system_instructions=_EXPLORE_PROMPT,
                    user_payload={
                        "message": message,
                        "conversation_history": history[-10:],
                        "workspace_path": self._workspace_path,
                        "tool_results": context,
                    },
                )
            except Exception:
                logger.exception("Explore step failed — stopping early")
                break

            if step.get("action") == "done":
                break

            tool_name = step.get("tool", "")
            args = step.get("args") or {}

            yield ChatEvent(type="explore_tool_call",
                            payload={"tool": tool_name, "args": args})

            try:
                tool_output = await self._registry.execute(tool_name, args)
                context.append({"tool": tool_name, "result": tool_output.output, "is_error": tool_output.is_error})
            except Exception as exc:
                context.append({"tool": tool_name, "result": str(exc), "is_error": True})
            if tool_name in ("read_file", "list_directory"):
                path = args.get("path", "")
                if path and path not in files_examined:
                    files_examined.append(path)

        classification = await self._classifier.classify(
            message, context=context, history=history
        )
        yield ChatEvent(
            type="intent_classified",
            payload={
                "intent": classification.intent,
                "rationale": classification.rationale,
                "likely_targets": classification.likely_targets,
                "files_examined": files_examined,
            },
        )

        if classification.intent == IntentType.QA:
            async for event in self._handle_qa(thread_id, message, context, history):
                yield event
        else:
            # small_change and large_change wired in Plan 2
            yield ChatEvent(
                type="chat_response",
                payload={"chunk": f"[{classification.intent} routing — not yet wired]"},
            )
            yield ChatEvent(type="chat_done", payload={})

    async def _handle_qa(
        self,
        thread_id: str,
        message: str,
        context: list[dict[str, str]],
        history: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]:
        try:
            response_text = await self._transport.generate_text(
                model=self._model,
                system_instructions=_QA_PROMPT,
                user_payload={
                    "workspace_path": self._workspace_path,
                    "conversation_history": history[-10:],
                    "workspace_context": context,  # already gathered — no re-read
                    "question": message,
                },
            )
        except Exception:
            logger.exception("Q&A LLM call failed")
            response_text = "Sorry, I couldn't answer that. Please try again."

        self._store.append_message(
            thread_id, ChatMessage(role="agent", content=response_text)
        )
        yield ChatEvent(type="chat_response", payload={"chunk": response_text})
        yield ChatEvent(type="chat_done", payload={})
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_chat_agent.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/agent.py tests/test_chat_agent.py
git commit -m "feat(chat): ChatAgent — explore phase with progress events, classify, Q&A with gathered context"
```

---

## Task 5: API Routes

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`
- Modify: `services/agentd-py/agentd/main.py`
- Create: `services/agentd-py/tests/test_chat_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_routes.py
import json
import pytest
from httpx import AsyncClient, ASGITransport
from agentd.main import build_app

@pytest.mark.asyncio
async def test_list_threads_empty(tmp_path):
    app = build_app(workspace_path=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/chat/threads", params={"workspace": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["threads"] == []

@pytest.mark.asyncio
async def test_create_thread_and_get_it(tmp_path):
    app = build_app(workspace_path=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post(
            "/v1/chat/threads",
            json={"workspace": str(tmp_path), "title": "My chat"},
        )
        assert create_resp.status_code == 200
        thread_id = create_resp.json()["thread_id"]

        get_resp = await client.get(f"/v1/chat/threads/{thread_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["thread_id"] == thread_id

@pytest.mark.asyncio
async def test_post_message_streams_events(tmp_path):
    app = build_app(workspace_path=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        thread_id = (
            await client.post("/v1/chat/threads", json={"workspace": str(tmp_path)})
        ).json()["thread_id"]

        async with client.stream(
            "POST", f"/v1/chat/threads/{thread_id}/message",
            json={"message": "What is this project?"},
        ) as resp:
            assert resp.status_code == 200
            lines = []
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    lines.append(json.loads(line[5:].strip()))

    types = [e["type"] for e in lines]
    assert "intent_classified" in types
    assert "chat_done" in types
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_chat_routes.py -v
```
Expected: 404 on `/v1/chat/threads`

- [ ] **Step 3: Add routes to `routes.py`**

In `agentd/api/routes.py`, add inside `build_router()`:

```python
# At top of file, add imports:
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore

# In build_router(), add chat_agent parameter:
def build_router(
    store: TaskStore,
    orchestrator: AgentOrchestrator,
    workspace_manager: ShadowWorkspaceManager,
    chat_agent: ChatAgent,
) -> APIRouter:
    ...

    @router.get("/v1/chat/threads")
    async def list_chat_threads(workspace: str) -> dict:
        threads = chat_agent._store.list_threads(workspace)
        return {"threads": [t.model_dump(exclude={"messages"}) for t in threads]}

    @router.post("/v1/chat/threads")
    async def create_chat_thread(request: dict) -> dict:
        workspace = request.get("workspace", "")
        title = request.get("title", "New Chat")
        thread = chat_agent._store.create_thread(workspace, title=title)
        return thread.model_dump(exclude={"messages"})

    @router.get("/v1/chat/threads/{thread_id}")
    async def get_chat_thread(thread_id: str) -> dict:
        thread = chat_agent._store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return thread.model_dump()

    @router.post("/v1/chat/threads/{thread_id}/message")
    async def post_chat_message(thread_id: str, request: dict) -> StreamingResponse:
        message = request.get("message", "")

        async def event_stream():
            async for event in chat_agent.handle_message(thread_id, message):
                yield f"data: {event.model_dump_json()}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Wire `ChatAgent` in `main.py`**

In `agentd/main.py`, after instantiating `orchestrator`, add the chat wiring and a `build_app` factory
(the factory is needed by tests so they can construct isolated app instances with a tmp workspace):

```python
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore

chat_db_path = Path(os.getenv("CRUCIBLE_CHAT_DB_PATH", ".agentd/chat.sqlite3")).resolve()
chat_db_path.parent.mkdir(parents=True, exist_ok=True)
chat_thread_store = ChatThreadStore(chat_db_path)

chat_agent = ChatAgent(
    workspace_path=workspace_path,
    transport=transport,   # existing provider transport
    model=model,           # existing model string
    thread_store=chat_thread_store,
    orchestrator=orchestrator,
)

router = build_router(store, orchestrator, workspace_manager, chat_agent)
```

Add `build_app` factory at module level in `main.py` for use by integration tests:

```python
def build_app(workspace_path: str) -> FastAPI:
    """Construct a self-contained FastAPI app for a given workspace path.
    Used by integration tests to spin up isolated instances with tmp_path.
    """
    from agentd.chat.agent import ChatAgent
    from agentd.chat.storage import ChatThreadStore
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.storage.in_memory import InMemoryTaskStore
    from agentd.workspace.shadow import ShadowWorkspaceManager

    _store = InMemoryTaskStore()
    _ws_manager = ShadowWorkspaceManager(Path(workspace_path) / ".agentd" / "shadows")
    _chat_store = ChatThreadStore(Path(workspace_path) / "chat.db")

    class _NullTransport:
        async def generate_text(self, **_) -> str:
            return "test response"
        async def generate_json(self, *, schema_name, **_) -> dict:
            if schema_name == "explore_step":
                return {"action": "done"}
            return {"intent": "qa", "rationale": "test", "likely_targets": []}

    _chat_agent = ChatAgent(
        workspace_path=workspace_path,
        transport=_NullTransport(),
        model="test-model",
        thread_store=_chat_store,
        orchestrator=None,
    )
    _orchestrator = AgentOrchestrator(store=_store, workspace_manager=_ws_manager)
    _router = build_router(_store, _orchestrator, _ws_manager, _chat_agent)
    _app = FastAPI()
    _app.include_router(_router)
    return _app
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_chat_routes.py tests/test_chat_agent.py tests/test_chat_storage.py tests/test_chat_classifier.py tests/test_chat_models.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add agentd/api/routes.py agentd/main.py tests/test_chat_routes.py
git commit -m "feat(chat): API routes — GET/POST /v1/chat/threads, GET /v1/chat/threads/{id}, POST /v1/chat/threads/{id}/message"
```

---

## Verification

- [ ] Start backend and confirm chat endpoints respond

```bash
bash scripts/stress/start-backend.sh --backend gemini --workspace "$PWD/workspaces/shadow-forge-stress"

# List threads (empty at start)
curl -s "http://localhost:8000/v1/chat/threads?workspace=/path/to/ws" | python3 -m json.tool

# Create a thread
THREAD_ID=$(curl -s -X POST http://localhost:8000/v1/chat/threads \
  -H "Content-Type: application/json" \
  -d '{"workspace": "/path/to/ws", "title": "Test chat"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])")

# Send a message and stream events
curl -sN -X POST "http://localhost:8000/v1/chat/threads/$THREAD_ID/message" \
  -H "Content-Type: application/json" \
  -d '{"message": "What does this project do?"}' \
  --no-buffer
```

Expected: SSE stream with `intent_classified`, `chat_response`, `chat_done` events.
