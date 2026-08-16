# Chat Rewind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user rewind a chat thread to an earlier user message — restoring the files the agent touched, truncating the transcript and controller history, and retiring the memories those turns wrote.

**Architecture:** A `RewindStore` captures each agent-touched file's pre-edit content at the `TurnEditSession.apply()` seam (copy-on-first-write, real workspace is the clean before-state), keyed to a per-turn checkpoint row anchored on the user message that started the turn. Restoring folds every checkpoint from the target forward with first-seen-wins per path, then restores four verbatim controller state blobs plus the memory compaction anchor. Truncation is permanent.

**Tech Stack:** Python 3.13 / FastAPI / Pydantic / sqlite3 / pytest-asyncio; TypeScript / Zod / React / vitest.

**Spec:** `docs/superpowers/specs/2026-08-16-chat-rewind-design.md`

## Global Constraints

- `ChatMessage.id` is `str | None = None` — **never** a `default_factory`. A factory mints a fresh id on every `model_validate` of a legacy dict, producing anchors that 404 differently each poll.
- Every contract shape change lands in **three** places in one commit: `agentd/chat/models.py`, `apps/editor-client/src/contracts/task-contracts.ts`, `apps/vscode-extension/webview-ui/src/types.ts`.
- Memory work is **best-effort**: wrapped in `try/except`, logged with `exc_info`, never fails a rewind.
- File capture is **degrade-never-raise**: no checkpoint for the thread → silent no-op.
- Restore collects per-file failures and continues; it never aborts partway.
- `pytest` here already sets `addopts = "-q"` in `pyproject.toml` — **never pass `-q`** (it stacks to `-qq` and suppresses the summary). Never pipe pytest output; the pipe's exit code masks pytest's.
- After changing `apps/editor-client`, run `npm run -w @crucible/editor-client build` before typechecking `crucible-vscode-extension` — the extension types off compiled `dist/index.d.ts`.
- Env defaults: `CRUCIBLE_REWIND_RETENTION_TURNS` = `50`, `CRUCIBLE_REWIND_MAX_FILE_BYTES` = `10000000`.

---

### Task 1: `ChatMessage.id` and its three contract mirrors

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py:29-35`
- Modify: `services/agentd-py/agentd/chat/storage.py:216-226`
- Modify: `apps/editor-client/src/contracts/task-contracts.ts:235-243`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts:11-22`
- Test: `services/agentd-py/tests/test_chat_message_id.py`
- Test: `apps/editor-client/test/schemas.test.ts`

**Interfaces:**
- Produces: `ChatMessage.id: str | None`; `ChatThreadStore.append_message(thread_id, message) -> str | None` (now **returns** the stamped id — Task 5 uses it as the rewind anchor); Zod `ChatMessageSchema` field `id: z.string().nullable().optional()`; webview `ChatMsg.id?: string | null`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_chat_message_id.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_chat_message_id.py -v`
Expected: FAIL — `AttributeError`/`ValidationError` on the unknown `id` attribute.

- [ ] **Step 3: Add the field and stamp it**

In `agentd/chat/models.py`, add to `ChatMessage` (after `role`/`content`):

```python
    # Stable per-message anchor for chat rewind. Deliberately nullable with NO
    # default_factory: model_validate runs against raw dicts out of messages_json,
    # so a factory would mint a fresh random id on every read of every message
    # persisted before this feature existed. Nullable means those carry None and
    # simply do not offer a rewind anchor.
    id: str | None = None
```

In `agentd/chat/storage.py`, change `append_message` to stamp and return:

```python
    def append_message(self, thread_id: str, message: ChatMessage) -> str | None:
        if message.id is None:
            message = message.model_copy(update={"id": uuid.uuid4().hex})
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
        return message.id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_chat_message_id.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Mirror into the two TypeScript contracts**

In `apps/editor-client/src/contracts/task-contracts.ts`, add to `ChatMessageSchema` (after `role`):

```typescript
  id: z.string().nullable().optional(),
```

In `apps/vscode-extension/webview-ui/src/types.ts`, add to `ChatMsg` (after `role`):

```typescript
  /** Stable rewind anchor. Absent/null on messages persisted before rewind shipped. */
  id?: string | null;
```

- [ ] **Step 6: Add the editor-client schema test**

Append to `apps/editor-client/test/schemas.test.ts`:

```typescript
import { ChatMessageSchema } from "../src/contracts/task-contracts";

it("accepts a chat message with no id (pre-rewind message)", () => {
  const parsed = ChatMessageSchema.parse({
    role: "user", content: "hi", timestamp: "2026-08-16T00:00:00Z",
  });
  expect(parsed.id).toBeUndefined();
});

it("preserves a chat message id when present", () => {
  const parsed = ChatMessageSchema.parse({
    role: "user", content: "hi", timestamp: "2026-08-16T00:00:00Z", id: "abc123",
  });
  expect(parsed.id).toBe("abc123");
});
```

- [ ] **Step 7: Run the full affected suites**

Run: `cd services/agentd-py && pytest tests/test_chat_message_id.py tests/test_chat_storage.py`
Run: `npm run -w @crucible/editor-client test`
Expected: PASS. `append_message`'s return type changed but no caller reads it yet, so nothing else breaks.

- [ ] **Step 8: Commit**

```bash
git add services/agentd-py/agentd/chat/models.py services/agentd-py/agentd/chat/storage.py \
        services/agentd-py/tests/test_chat_message_id.py \
        apps/editor-client/src/contracts/task-contracts.ts apps/editor-client/test/schemas.test.ts \
        apps/vscode-extension/webview-ui/src/types.ts
git commit -m "feat(chat): give chat messages a stable id for rewind anchors"
```

---

### Task 2: Checkpoint rows and copy-on-first-write capture

**Files:**
- Create: `services/agentd-py/agentd/chat/rewind.py`
- Modify: `services/agentd-py/agentd/chat/models.py` (add `Checkpoint`)
- Modify: `services/agentd-py/agentd/chat/storage.py` (`_migrate` + checkpoint CRUD)
- Test: `services/agentd-py/tests/test_rewind_capture.py`

**Interfaces:**
- Consumes: `ChatThreadStore`, `ChatThread` (Task 1).
- Produces:
  - `Checkpoint` (pydantic) fields: `thread_id, seq, anchor_message_id, turn_id, created_at, files: list[CapturedFile], controller_history_json, controller_seed_json, controller_todo_json, controller_active_skill_json, memory_anchor_md`
  - `CapturedFile` (pydantic) fields: `path: str, existed: bool, oversize: bool = False`
  - `ChatThreadStore.next_checkpoint_seq(thread_id) -> int`, `.insert_checkpoint(cp: Checkpoint) -> None`, `.list_checkpoints(thread_id) -> list[Checkpoint]`, `.get_checkpoint_by_anchor(thread_id, anchor_message_id) -> Checkpoint | None`, `.set_checkpoint_files(thread_id, seq, files: list[CapturedFile]) -> None`, `.delete_checkpoints_from(thread_id, seq) -> None`, `.delete_checkpoints_before(thread_id, seq) -> None`
  - `RewindStore(store: ChatThreadStore, workspace_path: Path, *, max_file_bytes: int | None = None, retention_turns: int | None = None)`
  - `RewindStore.open_checkpoint(thread_id, anchor_message_id, turn_id, *, thread: ChatThread, memory_anchor_md: str | None) -> int`
  - `RewindStore.capture(thread_id: str, paths: list[str]) -> None`

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_rewind_capture.py
import json

from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore


def _fixture(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("original a\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), "t")
    return ws, store, thread


def test_capture_copies_pre_edit_content_once(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    rewind.capture(thread.thread_id, ["src/a.py"])
    (ws / "src" / "a.py").write_text("edited once\n")
    # A second capture of the same path must NOT overwrite the original snapshot.
    rewind.capture(thread.thread_id, ["src/a.py"])

    snap = ws / ".crucible" / "state" / "rewind" / thread.thread_id / "0" / "files" / "src" / "a.py"
    assert snap.read_text() == "original a\n"


def test_capture_records_created_file_as_absent(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    rewind.capture(thread.thread_id, ["src/new.py"])

    cp = store.get_checkpoint_by_anchor(thread.thread_id, msg_id)
    entry = next(f for f in cp.files if f.path == "src/new.py")
    assert entry.existed is False


def test_capture_skips_oversize_file(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    (ws / "big.bin").write_text("x" * 100)
    rewind = RewindStore(store, ws, max_file_bytes=10)
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))
    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md=None)

    rewind.capture(thread.thread_id, ["big.bin"])

    cp = store.get_checkpoint_by_anchor(thread.thread_id, msg_id)
    entry = next(f for f in cp.files if f.path == "big.bin")
    assert entry.oversize is True
    assert not (ws / ".crucible" / "state" / "rewind" / thread.thread_id / "0" / "files" / "big.bin").exists()


def test_capture_without_open_checkpoint_is_silent_noop(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    rewind.capture(thread.thread_id, ["src/a.py"])  # must not raise
    assert store.list_checkpoints(thread.thread_id) == []


def test_open_checkpoint_snapshots_controller_blobs(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    store.set_controller_history(thread.thread_id, [{"role": "user", "content": "earlier"}])
    store.set_controller_todos(thread.thread_id, '[{"title": "t"}]')
    msg_id = store.append_message(thread.thread_id, ChatMessage(role="user", content="go"))

    rewind.open_checkpoint(thread.thread_id, msg_id, "turn1",
                           thread=store.get_thread(thread.thread_id), memory_anchor_md="anchor v1")

    cp = store.get_checkpoint_by_anchor(thread.thread_id, msg_id)
    assert json.loads(cp.controller_history_json) == [{"role": "user", "content": "earlier"}]
    assert cp.controller_todo_json == '[{"title": "t"}]'
    assert cp.memory_anchor_md == "anchor v1"


def test_second_checkpoint_gets_next_seq(tmp_path):
    ws, store, thread = _fixture(tmp_path)
    rewind = RewindStore(store, ws)
    t = store.get_thread(thread.thread_id)
    first = rewind.open_checkpoint(thread.thread_id, "m1", "turn1", thread=t, memory_anchor_md=None)
    second = rewind.open_checkpoint(thread.thread_id, "m2", "turn2", thread=t, memory_anchor_md=None)
    assert (first, second) == (0, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_rewind_capture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.chat.rewind'`

- [ ] **Step 3: Add the models**

In `agentd/chat/models.py`, append:

```python
class CapturedFile(BaseModel):
    """One path's pre-edit state inside a rewind checkpoint. `existed=False` means the
    turn created it (nothing was copied; restoring deletes it). `oversize=True` means it
    was too large to snapshot, so it is reported as not-restored rather than restored wrong."""
    path: str
    existed: bool
    oversize: bool = False


class Checkpoint(BaseModel):
    """A rewind point: the state of a thread just before one turn started.

    The four controller_* blobs are snapshotted WHOLE rather than truncated at rewind
    time. controller_history_json is a flat list of assistant-action / tool-result pairs
    with no alignment to transcript messages, so "truncate the history to match the
    transcript" has no correct implementation; restoring a verbatim earlier copy
    sidesteps alignment and picks up todos, active skill and the pinned seed for free."""
    thread_id: str
    seq: int
    anchor_message_id: str
    turn_id: str
    created_at: datetime
    files: list[CapturedFile] = Field(default_factory=list)
    controller_history_json: str | None = None
    controller_seed_json: str | None = None
    controller_todo_json: str | None = None
    controller_active_skill_json: str | None = None
    memory_anchor_md: str | None = None
```

- [ ] **Step 4: Add the table and CRUD**

In `agentd/chat/storage.py`, import `Checkpoint, CapturedFile` from `agentd.chat.models`, and add to the end of `_migrate` (before `self._conn.commit()`):

```python
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_checkpoints (
                thread_id                     TEXT NOT NULL,
                seq                           INTEGER NOT NULL,
                anchor_message_id             TEXT NOT NULL,
                turn_id                       TEXT NOT NULL,
                created_at                    TEXT NOT NULL,
                files_json                    TEXT NOT NULL DEFAULT '[]',
                controller_history_json       TEXT,
                controller_seed_json          TEXT,
                controller_todo_json          TEXT,
                controller_active_skill_json  TEXT,
                memory_anchor_md              TEXT,
                PRIMARY KEY (thread_id, seq)
            );
        """)
```

Then add these methods to `ChatThreadStore`:

```python
    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
        return Checkpoint(
            thread_id=row["thread_id"], seq=row["seq"],
            anchor_message_id=row["anchor_message_id"], turn_id=row["turn_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            files=[CapturedFile.model_validate(f) for f in json.loads(row["files_json"])],
            controller_history_json=row["controller_history_json"],
            controller_seed_json=row["controller_seed_json"],
            controller_todo_json=row["controller_todo_json"],
            controller_active_skill_json=row["controller_active_skill_json"],
            memory_anchor_md=row["memory_anchor_md"],
        )

    def next_checkpoint_seq(self, thread_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(seq) AS m FROM chat_checkpoints WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return 0 if row["m"] is None else row["m"] + 1

    def insert_checkpoint(self, cp: Checkpoint) -> None:
        self._conn.execute(
            "INSERT INTO chat_checkpoints (thread_id, seq, anchor_message_id, turn_id, "
            "created_at, files_json, controller_history_json, controller_seed_json, "
            "controller_todo_json, controller_active_skill_json, memory_anchor_md) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cp.thread_id, cp.seq, cp.anchor_message_id, cp.turn_id,
             cp.created_at.isoformat(),
             json.dumps([f.model_dump(mode="json") for f in cp.files]),
             cp.controller_history_json, cp.controller_seed_json, cp.controller_todo_json,
             cp.controller_active_skill_json, cp.memory_anchor_md),
        )
        self._conn.commit()

    def list_checkpoints(self, thread_id: str) -> list[Checkpoint]:
        rows = self._conn.execute(
            "SELECT * FROM chat_checkpoints WHERE thread_id = ? ORDER BY seq", (thread_id,)
        ).fetchall()
        return [self._checkpoint_from_row(r) for r in rows]

    def get_checkpoint_by_anchor(
        self, thread_id: str, anchor_message_id: str
    ) -> Checkpoint | None:
        row = self._conn.execute(
            "SELECT * FROM chat_checkpoints WHERE thread_id = ? AND anchor_message_id = ?",
            (thread_id, anchor_message_id),
        ).fetchone()
        return self._checkpoint_from_row(row) if row else None

    def set_checkpoint_files(
        self, thread_id: str, seq: int, files: list[CapturedFile]
    ) -> None:
        self._conn.execute(
            "UPDATE chat_checkpoints SET files_json = ? WHERE thread_id = ? AND seq = ?",
            (json.dumps([f.model_dump(mode="json") for f in files]), thread_id, seq),
        )
        self._conn.commit()

    def delete_checkpoints_from(self, thread_id: str, seq: int) -> None:
        self._conn.execute(
            "DELETE FROM chat_checkpoints WHERE thread_id = ? AND seq >= ?", (thread_id, seq)
        )
        self._conn.commit()

    def delete_checkpoints_before(self, thread_id: str, seq: int) -> None:
        self._conn.execute(
            "DELETE FROM chat_checkpoints WHERE thread_id = ? AND seq < ?", (thread_id, seq)
        )
        self._conn.commit()
```

- [ ] **Step 5: Write `rewind.py` (capture half)**

```python
# services/agentd-py/agentd/chat/rewind.py
"""Chat rewind — per-turn checkpoints of the files the agent touched.

Chat controller edits are instant-promoted (TurnEditSession.accept -> promote_files)
and the turn shadow is rmtree'd at turn end, so nothing retains the pre-edit content.
This module captures it at the one moment it is still on disk: the start of
TurnEditSession.apply(), where the real workspace is the clean before-state (the
`shadow == real` invariant edit_session.py's own docstring asserts).

Capture is copy-on-first-write per checkpoint: the FIRST time a turn touches a path,
its current real content is copied aside. Later edits to the same path in the same
turn are no-ops, so the snapshot always holds the pre-turn state.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from agentd.chat.models import CapturedFile, ChatThread, Checkpoint
from agentd.chat.storage import ChatThreadStore

logger = logging.getLogger(__name__)

_RETENTION_ENV = "CRUCIBLE_REWIND_RETENTION_TURNS"
_MAX_BYTES_ENV = "CRUCIBLE_REWIND_MAX_FILE_BYTES"
_DEFAULT_RETENTION = 50
_DEFAULT_MAX_BYTES = 10_000_000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class RewindStore:
    def __init__(
        self,
        store: ChatThreadStore,
        workspace_path: Path,
        *,
        max_file_bytes: int | None = None,
        retention_turns: int | None = None,
    ) -> None:
        self._store = store
        self._root = Path(workspace_path)
        self._max_bytes = max_file_bytes or _env_int(_MAX_BYTES_ENV, _DEFAULT_MAX_BYTES)
        self._retention = retention_turns or _env_int(_RETENTION_ENV, _DEFAULT_RETENTION)

    def _files_dir(self, thread_id: str, seq: int) -> Path:
        return self._root / ".crucible" / "state" / "rewind" / thread_id / str(seq) / "files"

    def open_checkpoint(
        self,
        thread_id: str,
        anchor_message_id: str,
        turn_id: str,
        *,
        thread: ChatThread,
        memory_anchor_md: str | None,
    ) -> int:
        """Start a checkpoint for a turn, snapshotting pre-turn controller state.

        `thread` MUST be the object read at the top of handle_message, i.e. BEFORE
        _run_loop mutates history/todos/skill — those are the values a rewind restores.
        """
        import json as _json

        seq = self._store.next_checkpoint_seq(thread_id)
        cp = Checkpoint(
            thread_id=thread_id, seq=seq, anchor_message_id=anchor_message_id,
            turn_id=turn_id, created_at=datetime.now(UTC), files=[],
            controller_history_json=(
                _json.dumps(thread.controller_conversation_history)
                if thread.controller_conversation_history else None),
            controller_seed_json=(
                _json.dumps(thread.controller_retrieval_seed)
                if thread.controller_retrieval_seed else None),
            controller_todo_json=(
                _json.dumps(thread.controller_todos) if thread.controller_todos else None),
            controller_active_skill_json=(
                _json.dumps(thread.controller_active_skill)
                if thread.controller_active_skill else None),
            memory_anchor_md=memory_anchor_md,
        )
        self._store.insert_checkpoint(cp)
        self._prune(thread_id)
        return seq

    def capture(self, thread_id: str, paths: list[str]) -> None:
        """Copy aside each path's current real content, first-write-wins.

        Writes into the thread's HIGHEST-seq checkpoint, so a continuation turn
        (resolve_mode -> implement, resolve_clarify) folds into the same anchor with
        no open/closed bookkeeping. No checkpoint -> silent no-op: the restart-recovery
        path (_promote_orphaned_edit) must never be broken by rewind bookkeeping.
        """
        checkpoints = self._store.list_checkpoints(thread_id)
        if not checkpoints:
            return
        cp = checkpoints[-1]
        known = {f.path for f in cp.files}
        added: list[CapturedFile] = []
        for rel in paths:
            if rel in known:
                continue
            known.add(rel)
            added.append(self._capture_one(thread_id, cp.seq, rel))
        if added:
            self._store.set_checkpoint_files(thread_id, cp.seq, cp.files + added)

    def _capture_one(self, thread_id: str, seq: int, rel: str) -> CapturedFile:
        source = self._root / rel
        if not source.exists():
            return CapturedFile(path=rel, existed=False)
        try:
            if source.stat().st_size > self._max_bytes:
                return CapturedFile(path=rel, existed=True, oversize=True)
            dest = self._files_dir(thread_id, seq) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            return CapturedFile(path=rel, existed=True)
        except OSError:
            logger.warning("[rewind] could not capture %s", rel, exc_info=True)
            return CapturedFile(path=rel, existed=True, oversize=True)

    def _prune(self, thread_id: str) -> None:
        checkpoints = self._store.list_checkpoints(thread_id)
        if len(checkpoints) <= self._retention:
            return
        cutoff = checkpoints[-self._retention].seq
        for cp in checkpoints:
            if cp.seq < cutoff:
                shutil.rmtree(self._files_dir(cp.thread_id, cp.seq).parent, ignore_errors=True)
        self._store.delete_checkpoints_before(thread_id, cutoff)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_rewind_capture.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/rewind.py services/agentd-py/agentd/chat/models.py \
        services/agentd-py/agentd/chat/storage.py services/agentd-py/tests/test_rewind_capture.py
git commit -m "feat(chat): capture pre-edit file state into per-turn rewind checkpoints"
```

---

### Task 3: Preview and restore

**Files:**
- Modify: `services/agentd-py/agentd/chat/rewind.py`
- Modify: `services/agentd-py/agentd/chat/storage.py` (message truncation + state restore)
- Test: `services/agentd-py/tests/test_rewind_restore.py`

**Interfaces:**
- Consumes: everything from Task 2.
- Produces:
  - `RewindOutcome` (pydantic): `restored_files: list[str]`, `deleted_files: list[str]`, `oversize_files: list[str]`, `failed: list[dict[str, str]]`, `removed_messages: int`, `prefill_text: str`, `target_created_at: datetime`
  - `RewindPreview` (pydantic): `messages: int`, `files: int`, `commands_run: int`, `blocked_by_task: str | None`
  - `RewindStore.preview(thread_id, anchor_message_id) -> RewindPreview | None`
  - `RewindStore.restore(thread_id, anchor_message_id) -> RewindOutcome | None`
  - `ChatThreadStore.truncate_messages_at(thread_id, anchor_message_id) -> tuple[int, str]`
  - `ChatThreadStore.restore_controller_state(thread_id, *, history_json, seed_json, todo_json, active_skill_json) -> None`

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_rewind_restore.py
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore


def _fixture(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "a.py").write_text("v0\n")
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), "t")
    return ws, store, thread, RewindStore(store, ws)


def _turn(store, rewind, thread_id, text, turn_id):
    msg_id = store.append_message(thread_id, ChatMessage(role="user", content=text))
    rewind.open_checkpoint(thread_id, msg_id, turn_id,
                           thread=store.get_thread(thread_id), memory_anchor_md=None)
    return msg_id


def test_restore_folds_multiple_turns_first_seen_wins(tmp_path):
    """Rewinding past two turns must restore the OLDEST pre-state, not the most recent one."""
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id

    first = _turn(store, rewind, tid, "turn one", "t1")
    rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v1\n")

    _turn(store, rewind, tid, "turn two", "t2")
    rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v2\n")

    outcome = rewind.restore(tid, first)

    assert (ws / "a.py").read_text() == "v0\n"
    assert outcome.restored_files == ["a.py"]


def test_restore_deletes_files_the_span_created(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    first = _turn(store, rewind, tid, "make it", "t1")
    rewind.capture(tid, ["new.py"])
    (ws / "new.py").write_text("created\n")

    outcome = rewind.restore(tid, first)

    assert not (ws / "new.py").exists()
    assert outcome.deleted_files == ["new.py"]


def test_restore_truncates_transcript_and_returns_prefill(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    store.append_message(tid, ChatMessage(role="user", content="keep me"))
    store.append_message(tid, ChatMessage(role="agent", content="kept reply"))
    anchor = _turn(store, rewind, tid, "rewind me", "t1")
    store.append_message(tid, ChatMessage(role="agent", content="dropped reply"))

    outcome = rewind.restore(tid, anchor)

    remaining = [m.content for m in store.get_thread(tid).messages]
    assert remaining == ["keep me", "kept reply"]
    assert outcome.removed_messages == 2
    assert outcome.prefill_text == "rewind me"


def test_restore_restores_controller_blobs_from_the_target(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    store.set_controller_history(tid, [{"role": "user", "content": "old"}])
    anchor = _turn(store, rewind, tid, "go", "t1")
    store.set_controller_history(tid, [{"role": "user", "content": "new"}])
    store.set_controller_todos(tid, '[{"title": "later"}]')

    rewind.restore(tid, anchor)

    reloaded = store.get_thread(tid)
    assert reloaded.controller_conversation_history == [{"role": "user", "content": "old"}]
    assert reloaded.controller_todos is None


def test_restore_drops_checkpoints_from_the_target_forward(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    keep = _turn(store, rewind, tid, "one", "t1")
    anchor = _turn(store, rewind, tid, "two", "t2")

    rewind.restore(tid, anchor)

    assert [c.anchor_message_id for c in store.list_checkpoints(tid)] == [keep]


def test_restore_reports_per_file_failure_without_aborting(tmp_path, monkeypatch):
    """A rewind that half-worked must SAY so — never stop silently partway."""
    import shutil as _shutil

    ws, store, thread, rewind = _fixture(tmp_path)
    (ws / "b.py").write_text("b0\n")
    tid = thread.thread_id
    anchor = _turn(store, rewind, tid, "go", "t1")
    rewind.capture(tid, ["a.py", "b.py"])
    (ws / "a.py").write_text("a1\n")
    (ws / "b.py").write_text("b1\n")

    real_copy = _shutil.copy2

    def _explode(src, dst, **kw):
        if str(dst).endswith("a.py"):
            raise OSError("disk on fire")
        return real_copy(src, dst, **kw)

    monkeypatch.setattr("agentd.chat.rewind.shutil.copy2", _explode)
    outcome = rewind.restore(tid, anchor)

    assert outcome.failed and outcome.failed[0]["path"] == "a.py"
    assert (ws / "b.py").read_text() == "b0\n"  # the other file still restored


def test_restore_unknown_anchor_returns_none(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    assert rewind.restore(thread.thread_id, "nope") is None


def test_preview_counts_distinct_files_across_the_span(tmp_path):
    ws, store, thread, rewind = _fixture(tmp_path)
    tid = thread.thread_id
    first = _turn(store, rewind, tid, "one", "t1")
    rewind.capture(tid, ["a.py"])
    _turn(store, rewind, tid, "two", "t2")
    rewind.capture(tid, ["a.py", "c.py"])  # a.py already counted once

    preview = rewind.preview(tid, first)

    assert preview.files == 2
    assert preview.messages == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_rewind_restore.py -v`
Expected: FAIL — `AttributeError: 'RewindStore' object has no attribute 'restore'`

- [ ] **Step 3: Add the two `ChatThreadStore` methods**

```python
    def truncate_messages_at(self, thread_id: str, anchor_message_id: str) -> tuple[int, str]:
        """Drop the anchor message and everything after it. Returns (removed_count,
        anchor_text). The anchor's own text is returned so the composer can prefill it —
        it is the persisted display content, NOT the @-mention-expanded turn_message
        (that was turn-scoped and deliberately never stored)."""
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return 0, ""
        messages: list[dict] = json.loads(row["messages_json"])
        index = next((i for i, m in enumerate(messages) if m.get("id") == anchor_message_id), None)
        if index is None:
            return 0, ""
        removed = messages[index:]
        kept = messages[:index]
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(kept), thread_id),
        )
        self._conn.commit()
        return len(removed), str(removed[0].get("content") or "")

    def restore_controller_state(
        self, thread_id: str, *, history_json: str | None, seed_json: str | None,
        todo_json: str | None, active_skill_json: str | None,
    ) -> None:
        """Overwrite the four controller blobs with a checkpoint's verbatim copies and
        clear any pending gate (a gate from a turn that no longer exists is unresolvable)."""
        self._conn.execute(
            "UPDATE chat_threads SET controller_history_json = ?, controller_seed_json = ?, "
            "controller_todo_json = ?, controller_active_skill_json = ?, "
            "controller_gate_json = NULL WHERE thread_id = ?",
            (history_json, seed_json, todo_json, active_skill_json, thread_id),
        )
        self._conn.commit()
```

- [ ] **Step 4: Add preview + restore to `rewind.py`**

Add the two result models near the top (after the env helpers):

```python
class RewindPreview(BaseModel):
    messages: int
    files: int
    commands_run: int
    blocked_by_task: str | None = None


class RewindOutcome(BaseModel):
    restored_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    oversize_files: list[str] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)
    removed_messages: int = 0
    prefill_text: str = ""
    target_created_at: datetime | None = None
```

(add `from pydantic import BaseModel, Field` to the imports)

Then the methods on `RewindStore`:

```python
    def _span(self, thread_id: str, anchor_message_id: str) -> list[Checkpoint]:
        """Checkpoints from the target forward, ascending. Empty when the anchor is unknown."""
        target = self._store.get_checkpoint_by_anchor(thread_id, anchor_message_id)
        if target is None:
            return []
        return [c for c in self._store.list_checkpoints(thread_id) if c.seq >= target.seq]

    @staticmethod
    def _fold(span: list[Checkpoint]) -> dict[str, CapturedFile]:
        """Fold the span into one entry per path, FIRST-SEEN WINS.

        The oldest recorded pre-state is the correct one to restore; this is what makes
        multi-turn rewind correct without storing a full workspace snapshot per turn."""
        folded: dict[str, CapturedFile] = {}
        for cp in span:
            for f in cp.files:
                folded.setdefault(f.path, f)
        return folded

    def preview(self, thread_id: str, anchor_message_id: str) -> RewindPreview | None:
        span = self._span(thread_id, anchor_message_id)
        if not span:
            return None
        thread = self._store.get_thread(thread_id)
        messages = 0
        commands = 0
        if thread is not None:
            index = next((i for i, m in enumerate(thread.messages)
                          if m.id == anchor_message_id), None)
            if index is not None:
                dropped = thread.messages[index:]
                messages = len(dropped)
                for m in dropped:
                    events = m.metadata.get("tool_events") or []
                    commands += sum(
                        1 for e in events
                        if isinstance(e, dict)
                        and str(e.get("tool", "")) in {"run_command", "session_start"})
        return RewindPreview(
            messages=messages, files=len(self._fold(span)), commands_run=commands)

    def restore(self, thread_id: str, anchor_message_id: str) -> RewindOutcome | None:
        span = self._span(thread_id, anchor_message_id)
        if not span:
            return None
        target = span[0]
        outcome = RewindOutcome(target_created_at=target.created_at)

        # 1. Files. Per-path failures are COLLECTED, never raised: a rewind that
        #    half-worked must report it rather than stop silently partway.
        for path, entry in self._fold(span).items():
            # The checkpoint that captured this path is the one holding its bytes.
            owner = next(cp for cp in span if any(f.path == path for f in cp.files))
            try:
                real = self._root / path
                if entry.oversize:
                    outcome.oversize_files.append(path)
                elif entry.existed:
                    stored = self._files_dir(thread_id, owner.seq) / path
                    real.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stored, real)
                    outcome.restored_files.append(path)
                else:
                    if real.exists():
                        real.unlink()
                    outcome.deleted_files.append(path)
            except OSError as exc:
                outcome.failed.append({"path": path, "error": str(exc)})

        # 2. Transcript + controller state.
        removed, prefill = self._store.truncate_messages_at(thread_id, anchor_message_id)
        outcome.removed_messages = removed
        outcome.prefill_text = prefill
        self._store.restore_controller_state(
            thread_id,
            history_json=target.controller_history_json,
            seed_json=target.controller_seed_json,
            todo_json=target.controller_todo_json,
            active_skill_json=target.controller_active_skill_json,
        )

        # 3. Drop the rewound checkpoints. Permanent, by design.
        for cp in span:
            shutil.rmtree(self._files_dir(thread_id, cp.seq).parent, ignore_errors=True)
        self._store.delete_checkpoints_from(thread_id, target.seq)
        return outcome
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_rewind_restore.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Run both rewind suites together**

Run: `cd services/agentd-py && pytest tests/test_rewind_capture.py tests/test_rewind_restore.py`
Expected: PASS (14 passed)

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/chat/rewind.py services/agentd-py/agentd/chat/storage.py \
        services/agentd-py/tests/test_rewind_restore.py
git commit -m "feat(chat): restore files, transcript and controller state from a checkpoint"
```

---

### Task 4: Memory retirement and anchor round-trip

**Files:**
- Modify: `services/agentd-py/agentd/memory/store.py`
- Modify: `services/agentd-py/agentd/memory/harness.py`
- Modify: `services/agentd-py/agentd/memory/consolidator.py:174-182`
- Modify: `services/agentd-py/agentd/memory/tool_source.py:49-57,83-84`
- Test: `services/agentd-py/tests/test_memory_rewind.py`

**Interfaces:**
- Produces:
  - `MemoryStore.retire_since(source_ref: str, cutoff_iso: str) -> int`
  - `MemoryStore.clear_anchor(run_id: str) -> None`
  - `MemoryHarness.anchor_markdown(run_id: str) -> str | None`
  - `MemoryHarness.restore_anchor(run_id: str, summary_md: str | None) -> None`
  - `MemoryHarness.retire_since(source_ref: str, cutoff_iso: str) -> int`
  - `Consolidator.write_explicit(..., run_id: str = "")` — new trailing keyword
  - `MemoryToolSource(..., run_id: str = "")` — new keyword

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_memory_rewind.py
from datetime import UTC, datetime, timedelta

from agentd.memory.models import Memory
from agentd.memory.store import MemoryStore


def _memory(mid: str, source_ref: str, created: datetime) -> Memory:
    return Memory(
        id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic",
        content=f"fact {mid}", entities=[], importance=5,
        valid_from=created, valid_to=None, superseded_by=None,
        source_kind="consolidation", source_ref=source_ref,
        source_seq_lo=None, source_seq_hi=None, created_at=created,
    )


def test_retire_since_retires_only_this_thread_after_the_cutoff(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    old = datetime.now(UTC) - timedelta(hours=2)
    new = datetime.now(UTC)
    store.insert_memory(_memory("keep-old", "chat-1", old), [])
    store.insert_memory(_memory("retire", "chat-1", new), [])
    store.insert_memory(_memory("other-thread", "chat-2", new), [])

    count = store.retire_since("chat-1", (new - timedelta(minutes=1)).isoformat())

    assert count == 1
    assert store.get_memory("retire").valid_to is not None
    assert store.get_memory("keep-old").valid_to is None
    assert store.get_memory("other-thread").valid_to is None


def test_clear_anchor_removes_the_row(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.upsert_anchor("chat-1", "summary")
    store.clear_anchor("chat-1")
    assert store.get_anchor("chat-1") is None


def test_harness_anchor_round_trip_and_null_clears(tmp_path):
    """A checkpoint taken before any compaction records None. Restoring must DELETE the
    newer anchor — leaving it would reintroduce exactly the leak retirement closes."""
    from agentd.memory.harness import MemoryHarness

    store = MemoryStore(tmp_path / "memory.sqlite3")
    harness = MemoryHarness(enabled=True, compactor=None)
    harness._store = store  # the harness derives _store from its compactor

    assert harness.anchor_markdown("chat-1") is None
    store.upsert_anchor("chat-1", "leaked summary")
    assert harness.anchor_markdown("chat-1") == "leaked summary"

    harness.restore_anchor("chat-1", None)
    assert store.get_anchor("chat-1") is None


def test_harness_without_store_degrades_silently():
    from agentd.memory.harness import NO_OP_HARNESS

    assert NO_OP_HARNESS.anchor_markdown("chat-1") is None
    NO_OP_HARNESS.restore_anchor("chat-1", "x")  # must not raise
    assert NO_OP_HARNESS.retire_since("chat-1", "2026-01-01T00:00:00+00:00") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_memory_rewind.py -v`
Expected: FAIL — `AttributeError: 'MemoryStore' object has no attribute 'retire_since'`

- [ ] **Step 3: Add the two `MemoryStore` methods**

Add after `get_anchor`:

```python
    def retire_since(self, source_ref: str, cutoff_iso: str) -> int:
        """Retire live memories written by one run at or after a cutoff.

        `source_ref` is the consolidator's run_id, which for the chat controller IS the
        thread_id — so this one predicate catches both thread- and workspace-scoped
        memories a rewound span produced, with no schema change. RecallEngine already
        filters `valid_to IS NULL` before scoring, so retirement takes effect with no
        recall-path change and no vec/FTS index surgery."""
        cur = self._conn.execute(
            "UPDATE memories SET valid_to = ? "
            "WHERE source_ref = ? AND valid_to IS NULL AND created_at >= ?",
            (datetime.now(UTC).isoformat(), source_ref, cutoff_iso),
        )
        self._conn.commit()
        return cur.rowcount or 0

    def clear_anchor(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM anchored_summaries WHERE run_id=?", (run_id,))
        self._conn.commit()
```

- [ ] **Step 4: Add the three `MemoryHarness` passthroughs**

Add to `MemoryHarness` (all degrade to no-op when `self._store is None`, which is the `NO_OP_HARNESS` case):

```python
    def anchor_markdown(self, run_id: str) -> str | None:
        """The run's current compaction anchor text, for snapshotting into a checkpoint."""
        if self._store is None:
            return None
        anchor = self._store.get_anchor(run_id)
        return anchor.summary_md if anchor else None

    def restore_anchor(self, run_id: str, summary_md: str | None) -> None:
        """Put the anchor back to a checkpoint's copy. None means the checkpoint predates
        any compaction, so the anchor is DELETED — leaving a newer one in place would keep
        summarizing turns the rewind just removed."""
        if self._store is None:
            return
        if summary_md is None:
            self._store.clear_anchor(run_id)
        else:
            self._store.upsert_anchor(run_id, summary_md)

    def retire_since(self, source_ref: str, cutoff_iso: str) -> int:
        if self._store is None:
            return 0
        return self._store.retire_since(source_ref, cutoff_iso)
```

- [ ] **Step 5: Thread `run_id` into explicit remembers**

In `agentd/memory/consolidator.py`, change `write_explicit`'s signature and body:

```python
    async def write_explicit(
        self, content: str, kind: str, entities: list[str], scope_kind: str, scope_id: str,
        run_id: str = "",
    ) -> str:
        c = CandidateMemory(kind=kind, content=content, entities=entities, importance=8)
        emb = await self._embed(content)  # FIX #3
        mem = self._build_memory(c, run_id=run_id, scope_kind=scope_kind, scope_id=scope_id,
                                 source_kind="agent_tool", seq_lo=None, seq_hi=None)
        self._store.insert_memory(mem, emb)
        return mem.id
```

In `agentd/memory/tool_source.py`, accept and forward it:

```python
    def __init__(
        self, consolidator: object, scope_kind: str, scope_id: str,
        *, recall_engine: object | None = None, store: object | None = None,
        run_id: str = "",
    ) -> None:
        self._consolidator = consolidator
        self._scope_kind = scope_kind
        self._scope_id = scope_id
        self._recall_engine = recall_engine
        self._store = store
        # Tagging explicit remembers with the run (= thread_id) makes them rewindable;
        # an untagged memory carries source_ref="" and escapes retire_since entirely.
        self._run_id = run_id
```

and in `execute`:

```python
        mid = await self._consolidator.write_explicit(  # type: ignore[attr-defined]
            content, kind, entities, scope_kind, self._scope_id, run_id=self._run_id)
```

In `agentd/memory/harness.py`, `memory_tool_source` gains a passthrough:

```python
    def memory_tool_source(self, run_id: str = "") -> object | None:
        """A MemoryToolSource (remember + recall) for the controller registry, or None when
        memory has no consolidator wired (disabled / compaction-only)."""
        if self._consolidator is None:
            return None
        from agentd.memory.tool_source import MemoryToolSource
        return MemoryToolSource(
            self._consolidator, self._scope_kind, self._scope_id,
            recall_engine=self._recall_engine, store=self._store, run_id=run_id,
        )
```

In `agentd/chat/controller.py`'s `_build_registry`, pass the thread id at the `memory_tool_source()` call site: `self._memory_harness.memory_tool_source(thread_id)`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_memory_rewind.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Verify no memory regression**

Run: `cd services/agentd-py && pytest tests/ -k "memory"`
Expected: PASS. `run_id` is a defaulted keyword, so every existing caller is unaffected.

- [ ] **Step 8: Commit**

```bash
git add services/agentd-py/agentd/memory/ services/agentd-py/tests/test_memory_rewind.py \
        services/agentd-py/agentd/chat/controller.py
git commit -m "feat(memory): retire memories and restore the anchor on rewind"
```

---

### Task 5: Wire capture into the live edit path

**Files:**
- Modify: `services/agentd-py/agentd/chat/edit_session.py:89-129`
- Modify: `services/agentd-py/agentd/chat/controller.py:93-115,300-375,397-402`
- Modify: `services/agentd-py/agentd/chat/controller_factory.py`
- Test: `services/agentd-py/tests/test_rewind_integration.py`

**Interfaces:**
- Consumes: `RewindStore.open_checkpoint`, `.capture` (Task 2); `MemoryHarness.anchor_markdown` (Task 4).
- Produces: `TurnEditSession(..., checkpoint_cb: Callable[[list[str]], None] | None = None)`; `ChatController(..., rewind_store: RewindStore | None = None)`; `ChatController._rewind`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_rewind_integration.py
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
                          "search": "before", "replace": "after"}])
    await session.accept()
    await session.close()

    assert (ws / "a.py").read_text() == "after\n"
    outcome = rewind.restore(thread.thread_id, msg_id)
    assert (ws / "a.py").read_text() == "before\n"
    assert outcome.restored_files == ["a.py"]


@pytest.mark.asyncio
async def test_session_without_checkpoint_cb_still_works(tmp_path):
    """The cb is optional — the orphan-recovery and test paths construct sessions without it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("before\n")
    session = TurnEditSession(
        turn_id="t", real_path=ws,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
    )
    entries = await session.apply([{"op": "search_replace", "file": "a.py",
                                    "search": "before", "replace": "after"}])
    await session.close()
    assert [e.path for e in entries] == ["a.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_rewind_integration.py -v`
Expected: FAIL — `TypeError: TurnEditSession.__init__() got an unexpected keyword argument 'checkpoint_cb'`

- [ ] **Step 3: Add the seam to `TurnEditSession`**

In `agentd/chat/edit_session.py`, extend `__init__` and `apply`:

```python
    def __init__(
        self,
        *,
        turn_id: str,
        real_path: Path,
        workspace_manager: ShadowWorkspaceManager,
        patch_engine: PatchEngine,
        checkpoint_cb: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._turn_id = turn_id
        self._real = real_path
        self._wm = workspace_manager
        self._patch = patch_engine
        self._shadow: Path | None = None
        self._touched_ever: set[str] = set()  # files the shadow has ever held this turn
        self._pending_touched: list[str] = []
        # Rewind capture. Called BEFORE _ensure_shadow so the copy is taken while the
        # real workspace is still the clean before-state (the shadow == real invariant
        # in this module's docstring). Sync, like the shutil copies in _ensure_shadow.
        self._checkpoint_cb = checkpoint_cb

    async def apply(self, patch_ops: list[dict[str, object]]) -> list[DiffEntry]:
        _validate_patch_ops(patch_ops)
        touched = [str(op["file"]) for op in patch_ops if "file" in op]
        if self._checkpoint_cb is not None:
            self._checkpoint_cb(touched)
        shadow = await self._ensure_shadow(touched)
        applied = await apply_ops(self._patch, shadow, patch_ops, allowed_files=set(touched))
        self._pending_touched = applied
        return compute_diff_entries(self._real, shadow, applied, self._turn_id)
```

Add `from collections.abc import Callable` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_rewind_integration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Open the checkpoint in `handle_message`**

In `agentd/chat/controller.py`, add the constructor parameter (after `memory_harness`):

```python
        rewind_store: RewindStore | None = None,
```

and in the body:

```python
        # Rewind checkpoints. None when the feature has no store wired (tests, legacy
        # factories) — every call site guards, so rewind is purely additive.
        self._rewind = rewind_store
```

In `handle_message`, move the `turn_id` assignment **above** the `append_message` call and open the checkpoint right after it. Replace the existing `self._store.append_message(...)` block with:

```python
        turn_id = uuid4().hex
        anchor_message_id = self._store.append_message(thread_id, ChatMessage(
            role="user", content=message,
            metadata={"mentioned_files": mentioned_paths} if mentioned_paths else {}))
        if self._rewind is not None and anchor_message_id is not None:
            # `thread` is the object read at the top of this function — pre-turn history,
            # todos, skill and seed, which is exactly what a rewind to here restores.
            self._rewind.open_checkpoint(
                thread_id, anchor_message_id, turn_id, thread=thread,
                memory_anchor_md=self._memory_harness.anchor_markdown(thread_id))
```

Delete the later `turn_id = uuid4().hex` line (it is now assigned above).

- [ ] **Step 6: Pass the capture callback into the session factory**

In `_run_loop`, extend the `edit_session_factory` closure:

```python
        edit_session_factory = (
            (lambda: TurnEditSession(
                turn_id=thread_id, real_path=Path(self._workspace_path),
                workspace_manager=self._orchestrator._workspace_manager,
                patch_engine=self._orchestrator._patch_engine,
                checkpoint_cb=(
                    partial(self._rewind.capture, thread_id)
                    if self._rewind is not None else None)))
            if self._orchestrator is not None else None)
```

- [ ] **Step 7: Build the store in the factory**

In `agentd/chat/controller_factory.py`, where `ChatController(...)` is constructed, build and pass the store:

```python
    from agentd.chat.rewind import RewindStore

    rewind_store = RewindStore(thread_store, Path(workspace_path))
```

and add `rewind_store=rewind_store,` to the `ChatController(...)` call.

- [ ] **Step 8: Run the chat suites**

Run: `cd services/agentd-py && pytest tests/test_rewind_integration.py tests/ -k "controller or chat"`
Expected: PASS. `rewind_store` defaults to None, so tests constructing `ChatController` directly are unaffected.

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/chat/ services/agentd-py/tests/test_rewind_integration.py
git commit -m "feat(chat): capture rewind checkpoints on every controller turn"
```

---

### Task 6: Preview and rewind routes

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py` (after the `/stop` route, ~line 1750)
- Test: `services/agentd-py/tests/test_rewind_routes.py`

**Interfaces:**
- Consumes: `RewindStore.preview/restore` (Task 3), `MemoryHarness.retire_since/restore_anchor` (Task 4), `ChatController._rewind`/`_active_turns`/`_memory_harness` (Task 5).
- Produces: `GET /v1/chat/threads/{thread_id}/rewind-preview?message_id=`, `POST /v1/chat/threads/{thread_id}/rewind`.

- [ ] **Step 1: Write the failing test**

Build the router directly, the way `tests/test_chat_live_route.py` already does. Do **not** use
`chat/app_factory.py::build_app` — it returns only `app` (no handle on the handler) and wires the
legacy `ChatAgent`, which has no `_rewind`.

```python
# services/agentd-py/tests/test_rewind_routes.py
"""Rewind routes: preview counts, refusals, and the destructive POST."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ValidationResult
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


def _build(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    task_store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    orch = AgentOrchestrator(
        store=task_store, reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(), workspace_manager=ws_manager,
    )
    controller = ChatController(
        workspace_path=str(ws), reasoning_engine=_NoopReasoning(), thread_store=chat_store,
        orchestrator=orch, broadcaster=EventBroadcaster(),
        rewind_store=RewindStore(chat_store, ws),
    )
    app = FastAPI()
    app.include_router(
        build_router(task_store, orch, ws_manager, chat_agent=controller), prefix="/v1")
    thread = chat_store.create_thread(str(ws), "t")
    msg_id = chat_store.append_message(
        thread.thread_id, ChatMessage(role="user", content="go"))
    controller._rewind.open_checkpoint(
        thread.thread_id, msg_id, "turn1",
        thread=chat_store.get_thread(thread.thread_id), memory_anchor_md=None)
    return app, controller, thread.thread_id, msg_id


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_preview_returns_counts(tmp_path):
    app, _c, tid, msg_id = _build(tmp_path)
    async with await _client(app) as client:
        r = await client.get(f"/v1/chat/threads/{tid}/rewind-preview",
                             params={"message_id": msg_id})
    assert r.status_code == 200
    assert r.json()["messages"] == 1


@pytest.mark.asyncio
async def test_preview_unknown_message_is_404(tmp_path):
    app, _c, tid, _m = _build(tmp_path)
    async with await _client(app) as client:
        r = await client.get(f"/v1/chat/threads/{tid}/rewind-preview",
                             params={"message_id": "nope"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rewind_refuses_while_a_turn_is_in_flight(tmp_path):
    app, controller, tid, msg_id = _build(tmp_path)
    controller._active_turns[tid] = object()
    async with await _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
    assert r.status_code == 409
    assert "in flight" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rewind_truncates_and_returns_prefill(tmp_path):
    app, controller, tid, msg_id = _build(tmp_path)
    controller._store.append_message(tid, ChatMessage(role="agent", content="dropped"))
    async with await _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": msg_id})
    assert r.status_code == 200
    body = r.json()
    assert body["removed_messages"] == 2
    assert body["prefill_text"] == "go"
    assert controller._store.get_thread(tid).messages == []


@pytest.mark.asyncio
async def test_rewind_unknown_message_is_404(tmp_path):
    app, _c, tid, _m = _build(tmp_path)
    async with await _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": "nope"})
    assert r.status_code == 404
```

Match `build_router`'s real parameter names against `tests/test_chat_live_route.py::_build` — that
file is the working reference for this harness.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_rewind_routes.py -v`
Expected: FAIL — 404 on an unregistered route.

- [ ] **Step 3: Add both routes**

In `agentd/api/routes.py`, after the `/chat/threads/{thread_id}/stop` route:

```python
        def _require_rewind(thread_id: str):
            """The rewind store, the thread, and a 404 when either is missing."""
            rewind = getattr(_chat_agent, "_rewind", None)
            if rewind is None:
                raise HTTPException(status_code=404, detail="rewind unavailable")
            thread = _chat_agent._store.get_thread(thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="Thread not found")
            return rewind, thread

        # There is no is_terminal_status helper in domain/state_machine.py — the terminal
        # set is spelled out here rather than invented elsewhere.
        _TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}

        def _blocking_task_id(thread, anchor_message_id: str) -> str | None:
            """A task referenced by the rewound span that is still running.

            Exec sessions are deliberately NOT blocking — they are background shells and
            are left running, named in the confirm dialog. A task is different: it holds
            its own shadow and promotes into the real workspace when it finishes, so one
            still running after a rewind would re-write the files just reverted."""
            index = next((i for i, m in enumerate(thread.messages)
                          if m.id == anchor_message_id), None)
            if index is None:
                return None
            return next((m.task_id for m in thread.messages[index:] if m.task_id), None)

        @router.get("/chat/threads/{thread_id}/rewind-preview")
        async def get_rewind_preview(thread_id: str, message_id: str) -> dict:
            rewind, thread = _require_rewind(thread_id)
            preview = rewind.preview(thread_id, message_id)
            if preview is None:
                raise HTTPException(status_code=404, detail="No rewind point for that message")
            blocking = _blocking_task_id(thread, message_id)
            if blocking:
                try:
                    task = await store.get(blocking)
                    if task.status not in _TERMINAL:
                        preview.blocked_by_task = blocking
                except KeyError:
                    pass
            sessions = []
            _exec_mgr = getattr(_chat_agent, "_exec_sessions", None)
            if _exec_mgr is not None:
                sessions = [{"id": s["id"], "command": s.get("command", "")}
                            for s in (_exec_mgr.live_summaries(thread_id) or [])]
            return {**preview.model_dump(), "sessions": sessions}

        @router.post("/chat/threads/{thread_id}/rewind")
        async def post_rewind(thread_id: str, request: dict) -> dict:
            rewind, thread = _require_rewind(thread_id)
            message_id = str(request.get("message_id") or "")
            if thread_id in getattr(_chat_agent, "_active_turns", {}):
                raise HTTPException(
                    status_code=409,
                    detail="A turn is in flight for this thread — stop it before rewinding.")
            blocking = _blocking_task_id(thread, message_id)
            if blocking:
                try:
                    task = await store.get(blocking)
                    if task.status not in _TERMINAL:
                        raise HTTPException(
                            status_code=409,
                            detail=(f"This span started task {blocking}, which is still "
                                    "running. Cancel or abort it before rewinding."))
                except KeyError:
                    pass

            # Read the anchor snapshot BEFORE restoring: restore() deletes the checkpoint
            # row, so reading it afterwards always yields None and would silently clear
            # every anchor instead of putting the right one back.
            checkpoint = _chat_agent._store.get_checkpoint_by_anchor(thread_id, message_id)
            anchor_md = checkpoint.memory_anchor_md if checkpoint else None

            outcome = rewind.restore(thread_id, message_id)
            if outcome is None:
                raise HTTPException(status_code=404, detail="No rewind point for that message")

            # Memory is best-effort: it must never fail a rewind that already moved files.
            retired = 0
            harness = getattr(_chat_agent, "_memory_harness", None)
            if harness is not None and outcome.target_created_at is not None:
                try:
                    retired = harness.retire_since(
                        thread_id, outcome.target_created_at.isoformat())
                    harness.restore_anchor(thread_id, anchor_md)
                except Exception:
                    logger.warning("[rewind] memory cleanup failed", exc_info=True)
            return {**outcome.model_dump(mode="json"), "retired_memories": retired}
```

`TaskStatus` is already imported in `routes.py`; add it to the existing import if not.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_rewind_routes.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the whole Python suite**

Run: `cd services/agentd-py && pytest --color=no > /tmp/rewind-py.txt 2>&1; echo exit=$?; tail -5 /tmp/rewind-py.txt`
Expected: exit=0, all passing. Reproduce any failure in isolation before attributing it to this change.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/agentd/chat/app_factory.py \
        services/agentd-py/tests/test_rewind_routes.py
git commit -m "feat(api): add chat rewind preview and rewind routes"
```

---

### Task 7: editor-client contract

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/http-backend-client.test.ts`

**Interfaces:**
- Produces: `RewindPreviewSchema`, `RewindResultSchema`; `BackendTaskClient.previewRewind(threadId, messageId): Promise<RewindPreview>`; `BackendTaskClient.rewindThread(threadId, messageId): Promise<RewindResult>`.

- [ ] **Step 1: Write the failing test**

```typescript
// append to apps/editor-client/test/http-backend-client.test.ts
it("maps rewind preview snake_case to camelCase", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true, status: 200,
    json: async () => ({
      messages: 3, files: 2, commands_run: 1, blocked_by_task: null,
      sessions: [{ id: "s1", command: "npm test" }],
    }),
  });
  const client = new HttpBackendClient("http://localhost:8000", fetchMock as never);

  const preview = await client.previewRewind("chat-1", "m1");

  expect(preview.commandsRun).toBe(1);
  expect(preview.blockedByTask).toBeNull();
  expect(preview.sessions[0].command).toBe("npm test");
  expect(fetchMock.mock.calls[0][0]).toContain(
    "/v1/chat/threads/chat-1/rewind-preview?message_id=m1",
  );
});

it("maps rewind result snake_case to camelCase", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true, status: 200,
    json: async () => ({
      restored_files: ["a.py"], deleted_files: [], oversize_files: [],
      failed: [{ path: "b.py", error: "boom" }],
      removed_messages: 2, prefill_text: "do it", retired_memories: 4,
    }),
  });
  const client = new HttpBackendClient("http://localhost:8000", fetchMock as never);

  const result = await client.rewindThread("chat-1", "m1");

  expect(result.restoredFiles).toEqual(["a.py"]);
  expect(result.removedMessages).toBe(2);
  expect(result.prefillText).toBe("do it");
  expect(result.retiredMemories).toBe(4);
  expect(result.failed[0].path).toBe("b.py");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w @crucible/editor-client test`
Expected: FAIL — `client.previewRewind is not a function`

- [ ] **Step 3: Add the schemas**

In `apps/editor-client/src/contracts/task-contracts.ts`, after `ChatThreadSchema`:

```typescript
export const RewindPreviewSchema = z.object({
  messages: z.number(),
  files: z.number(),
  commandsRun: z.number(),
  blockedByTask: z.string().nullable().default(null),
  sessions: z.array(z.object({ id: z.string(), command: z.string() })).default([]),
});
export type RewindPreview = z.infer<typeof RewindPreviewSchema>;

export const RewindResultSchema = z.object({
  restoredFiles: z.array(z.string()).default([]),
  deletedFiles: z.array(z.string()).default([]),
  oversizeFiles: z.array(z.string()).default([]),
  failed: z.array(z.object({ path: z.string(), error: z.string() })).default([]),
  removedMessages: z.number(),
  prefillText: z.string().default(""),
  retiredMemories: z.number().default(0),
});
export type RewindResult = z.infer<typeof RewindResultSchema>;
```

Add to the `BackendTaskClient` interface:

```typescript
  previewRewind(threadId: string, messageId: string): Promise<RewindPreview>;
  rewindThread(threadId: string, messageId: string): Promise<RewindResult>;
```

- [ ] **Step 4: Implement both calls**

In `apps/editor-client/src/client/http-backend-client.ts`:

```typescript
  async previewRewind(threadId: string, messageId: string): Promise<RewindPreview> {
    const raw = await this.getJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/rewind-preview` +
      `?message_id=${encodeURIComponent(messageId)}`,
    );
    const r = raw as Record<string, unknown>;
    return RewindPreviewSchema.parse({
      messages: r["messages"], files: r["files"],
      commandsRun: r["commands_run"], blockedByTask: r["blocked_by_task"] ?? null,
      sessions: r["sessions"] ?? [],
    });
  }

  async rewindThread(threadId: string, messageId: string): Promise<RewindResult> {
    const raw = await this.postJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/rewind`,
      { message_id: messageId },
    );
    const r = raw as Record<string, unknown>;
    return RewindResultSchema.parse({
      restoredFiles: r["restored_files"] ?? [],
      deletedFiles: r["deleted_files"] ?? [],
      oversizeFiles: r["oversize_files"] ?? [],
      failed: r["failed"] ?? [],
      removedMessages: r["removed_messages"],
      prefillText: r["prefill_text"] ?? "",
      retiredMemories: r["retired_memories"] ?? 0,
    });
  }
```

Match the surrounding file's existing request helpers — if they are named differently from `getJson`/`postJson`, use the local names rather than introducing new ones.

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run -w @crucible/editor-client test`
Expected: PASS

- [ ] **Step 6: Build so the extension sees the new types**

Run: `npm run -w @crucible/editor-client build`
Expected: clean build. The extension types off compiled `dist/index.d.ts`, so skipping this produces stale-type errors that do not exist in source.

- [ ] **Step 7: Commit**

```bash
git add apps/editor-client/
git commit -m "feat(editor-client): add rewind preview and rewind client calls"
```

---

### Task 8: Rewind affordance, confirm dialog and prefill

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/RewindDialog.tsx`
- Create: `apps/vscode-extension/webview-ui/src/components/RewindDialog.test.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/UserMessage.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx:142-149`
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx:39,286-287`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`
- Modify: `apps/vscode-extension/src/chat-panel.ts`
- Modify: `apps/vscode-extension/src/controller.ts`
- Modify: `apps/vscode-extension/src/extension.ts`
- Test: `apps/vscode-extension/test/controller.test.ts` (append)

**Interfaces:**
- Consumes: `previewRewind`/`rewindThread` (Task 7); `ChatMsg.id` (Task 1).
- Produces: webview→host `{type: "rewindPreview", messageId}` and `{type: "rewindConfirm", messageId}`; host→webview `{type: "rewindPreviewResult", preview}` and `{type: "composerPrefill", text}`; `CrucibleController.previewRewind(messageId)` and `.rewindTo(messageId)`.

- [ ] **Step 1: Write the failing controller test**

**Append to the existing `apps/vscode-extension/test/controller.test.ts`** — do not create a new
file. `createUi` / `createSettings` / `MemorySessionStore` are local (unexported) helpers there,
and the constructor is `new CrucibleController(clientFactory, store, settings, ui, diffOpener, clock)`.

```typescript
describe("rewind", () => {
  const REWIND_RESULT = {
    restoredFiles: ["a.py"], deletedFiles: [], oversizeFiles: [], failed: [],
    removedMessages: 2, prefillText: "try again", retiredMemories: 1,
  };
  const THREAD = {
    threadId: "chat-1", workspacePath: "/ws", title: "t",
    messages: [{ role: "user", content: "kept", type: "text", timestamp: "", metadata: {} }],
    touchedFiles: [],
  };

  it("reloads the thread and prefills the composer after a rewind", async () => {
    const prefilled: string[] = [];
    const backend = {
      rewindThread: vi.fn().mockResolvedValue(REWIND_RESULT),
      getChatThread: vi.fn().mockResolvedValue(THREAD),
      listChatThreads: vi.fn().mockResolvedValue([]),
    } as unknown as BackendTaskClient;
    const ui = createUi({ prefillComposer: (t: string) => prefilled.push(t) });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async () => {} }, () => "2026-08-16T00:00:00.000Z",
    );
    controller.activeThreadId = "chat-1";

    await controller.rewindTo("m1");
    controller.dispose();

    expect(backend.rewindThread).toHaveBeenCalledWith("chat-1", "m1");
    expect(prefilled).toEqual(["try again"]);
  });

  it("surfaces a rewind failure instead of silently reloading", async () => {
    const errors: string[] = [];
    const backend = {
      rewindThread: vi.fn().mockRejectedValue(new Error("409 turn in flight")),
      getChatThread: vi.fn(),
    } as unknown as BackendTaskClient;
    const ui = createUi({ showError: (m: string) => errors.push(m) });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async () => {} }, () => "2026-08-16T00:00:00.000Z",
    );
    controller.activeThreadId = "chat-1";

    await controller.rewindTo("m1");
    controller.dispose();

    expect(errors.length).toBe(1);
    expect(backend.getChatThread).not.toHaveBeenCalled();
  });
});
```

Add `prefillComposer: () => {}` and `showRewindPreview: () => {}` to `createUi`'s default object
(line ~206) so every other test in the file keeps compiling against the widened `ControllerUI`.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- controller`
Expected: FAIL — `controller.rewindTo is not a function`

- [ ] **Step 3: Implement the controller methods**

In `apps/vscode-extension/src/controller.ts`, add `prefillComposer(text: string): void` and `showRewindPreview(preview: RewindPreview): void` to the `ControllerUI` interface, then:

```typescript
  async previewRewind(messageId: string): Promise<void> {
    if (!this.activeThreadId) return;
    const client = this.createClient(this.settings.getBackendBaseUrl());
    try {
      this.ui.showRewindPreview(await client.previewRewind(this.activeThreadId, messageId));
    } catch (error) {
      this.ui.showError(`Could not preview rewind: ${formatError(error)}`);
    }
  }

  async rewindTo(messageId: string): Promise<void> {
    const threadId = this.activeThreadId;
    if (!threadId) return;
    const client = this.createClient(this.settings.getBackendBaseUrl());
    let result;
    try {
      result = await client.rewindThread(threadId, messageId);
    } catch (error) {
      // A refusal (409 turn in flight / live task) must be visible — never fall through
      // to a reload that would look like the rewind worked.
      this.ui.showError(`Rewind failed: ${formatError(error)}`);
      return;
    }
    // The server is authoritative about what survived: reload rather than mutating the
    // transcript client-side. Mirrors switchChatThread's clear-then-append.
    await this.switchChatThread(threadId);
    this.ui.prefillComposer(result.prefillText);
    if (result.failed.length > 0) {
      this.ui.showError(
        `Rewind restored ${result.restoredFiles.length} file(s) but ${result.failed.length} ` +
        `could not be restored: ${result.failed.map((f) => f.path).join(", ")}`,
      );
    }
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run -w crucible-vscode-extension test -- controller`
Expected: PASS (2 passed)

- [ ] **Step 5: Add the host plumbing**

In `apps/vscode-extension/src/chat-panel.ts`, add two branches to the webview-message chain, alongside `else if (m["type"] === "modeDecision")`:

```typescript
      } else if (m["type"] === "rewindPreview") {
        p = this.onRewindPreview(m["messageId"] as string);
      } else if (m["type"] === "rewindConfirm") {
        p = this.onRewindConfirm(m["messageId"] as string);
```

Declare `onRewindPreview` / `onRewindConfirm` alongside the panel's other callbacks, add `prefillComposer(text)` and `showRewindPreview(preview)` posting `{type: "composerPrefill", text}` / `{type: "rewindPreviewResult", preview}` to the webview, and wire both callbacks to `controller.previewRewind` / `controller.rewindTo` in `apps/vscode-extension/src/extension.ts` where the other chat callbacks are wired.

- [ ] **Step 6: Write the failing dialog test**

```tsx
// apps/vscode-extension/webview-ui/src/components/RewindDialog.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RewindDialog } from "./RewindDialog";

const preview = {
  messages: 3, files: 2, commandsRun: 1, blockedByTask: null as string | null,
  sessions: [{ id: "s1", command: "npm run dev" }],
};

describe("RewindDialog", () => {
  it("states what rewind does not undo", () => {
    render(<RewindDialog preview={preview} onCancel={() => {}} onConfirm={() => {}} />);
    expect(screen.getByText(/are not undone/i)).toBeTruthy();
    expect(screen.getByText(/npm run dev/)).toBeTruthy();
  });

  it("disables confirm when a live task blocks the rewind", () => {
    render(
      <RewindDialog
        preview={{ ...preview, blockedByTask: "task-9" }}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /rewind/i }).hasAttribute("disabled")).toBe(true);
  });

  it("confirms with the counts shown", () => {
    const onConfirm = vi.fn();
    render(<RewindDialog preview={preview} onCancel={() => {}} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: /rewind/i }));
    expect(onConfirm).toHaveBeenCalled();
  });
});
```

- [ ] **Step 7: Run test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- RewindDialog`
Expected: FAIL — cannot resolve `./RewindDialog`

- [ ] **Step 8: Build the dialog**

```tsx
// apps/vscode-extension/webview-ui/src/components/RewindDialog.tsx
import type { RewindPreviewView } from "../types";

/**
 * Confirm gate for a permanent rewind. Built on the shared .scrim/.surface-card
 * primitives so it matches the rest of the webview design language.
 *
 * The copy is deliberately blunt about the limits: rewind restores files the agent
 * wrote and the conversation, and nothing else. Commands it ran already happened.
 */
export function RewindDialog({
  preview,
  onCancel,
  onConfirm,
}: {
  preview: RewindPreviewView;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const blocked = preview.blockedByTask != null;
  return (
    <div className="scrim" role="dialog" aria-modal="true" onClick={onCancel}>
      <div className="surface-card max-w-sm p-4" onClick={(e) => e.stopPropagation()}>
        <div className="text-text font-medium mb-2">Rewind to here?</div>
        <div className="text-text-2 text-xs mb-3">
          This permanently removes {preview.messages} message
          {preview.messages === 1 ? "" : "s"} and restores {preview.files} file
          {preview.files === 1 ? "" : "s"} the agent changed. It cannot be undone.
        </div>
        {preview.commandsRun > 0 && (
          <div className="text-text-2 text-xs mb-2">
            {preview.commandsRun} command{preview.commandsRun === 1 ? "" : "s"} the agent ran
            are not undone.
          </div>
        )}
        {preview.sessions.length > 0 && (
          <div className="text-text-2 text-xs mb-2">
            These sessions keep running:{" "}
            {preview.sessions.map((s) => s.command).join(", ")}
          </div>
        )}
        {blocked && (
          <div className="text-xs mb-2" style={{ color: "var(--color-danger)" }}>
            Task {preview.blockedByTask} is still running. Cancel or abort it first.
          </div>
        )}
        <div className="flex gap-2 justify-end mt-3">
          <button className="menu-item" onClick={onCancel}>
            Cancel
          </button>
          <button className="menu-item" disabled={blocked} onClick={onConfirm}>
            Rewind
          </button>
        </div>
      </div>
    </div>
  );
}
```

Add to `apps/vscode-extension/webview-ui/src/types.ts`:

```typescript
export interface RewindPreviewView {
  messages: number;
  files: number;
  commandsRun: number;
  blockedByTask: string | null;
  sessions: { id: string; command: string }[];
}
```

and extend the two message unions:

```typescript
  | { type: "rewindPreviewResult"; preview: RewindPreviewView }
  | { type: "composerPrefill"; text: string }
```

```typescript
  | { type: "rewindPreview"; messageId: string }
  | { type: "rewindConfirm"; messageId: string }
```

- [ ] **Step 9: Run test to verify it passes**

Run: `npm run -w crucible-vscode-extension test -- RewindDialog`
Expected: PASS (3 passed)

- [ ] **Step 10: Add the hover affordance**

In `UserMessage.tsx`, accept `onRewind?: () => void` and render a hover-revealed button. Wrap the existing bubble in a `group relative` container and add, as the bubble's first child:

```tsx
      {onRewind && (
        <button
          className="absolute -left-6 top-1 opacity-0 group-hover:opacity-100 transition-opacity"
          title="Rewind to here"
          aria-label="Rewind to here"
          onClick={onRewind}
          style={{ color: "var(--color-text-2)" }}
        >
          ↺
        </button>
      )}
```

In `MessageRow.tsx`, thread it through the user branch — the button only exists when the message has an id and no turn is running:

```tsx
      if (msg.role === "user") {
        return (
          <UserMessage
            content={msg.content}
            mentionedFiles={msg.metadata?.mentioned_files as string[] | undefined}
            onRewind={
              msg.id && !turnActive
                ? () => vscode.postMessage({ type: "rewindPreview", messageId: msg.id })
                : undefined
            }
          />
        );
      }
```

Add `turnActive?: boolean` to `MessageRow`'s `Props` and pass `turnActive={state.turnActive}` from `ThreadView.tsx`'s `messages.map`. Import `vscode` from `../vscodeApi` in `MessageRow.tsx`.

- [ ] **Step 11: Hold the preview and the prefill in `ThreadView`**

The composer draft is **not** in `useAppState` — it is `const [draft, setDraft] = useState("")` in
`ThreadView.tsx:39` ("draft state is owned here so EmptyState chips can pre-fill it"). So both the
pending preview and the prefill are local `ThreadView` state, driven by a window message listener
that mirrors the `promptExpanded` listener in `InputArea.tsx:131`. `useAppState.ts` is not touched
by this step.

Add to `ThreadView.tsx`, next to the existing `draft` state:

```tsx
  const [rewindPreview, setRewindPreview] = useState<RewindPreviewView | null>(null);
  const [rewindMessageId, setRewindMessageId] = useState<string | null>(null);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const m = event.data as { type?: string; preview?: RewindPreviewView; text?: string };
      if (m?.type === "rewindPreviewResult" && m.preview) {
        setRewindPreview(m.preview);
      } else if (m?.type === "composerPrefill") {
        setRewindPreview(null);
        setRewindMessageId(null);
        setDraft(m.text ?? "");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
```

`MessageRow`'s rewind click must record which message it was, so add `onRewindRequest={(id) => setRewindMessageId(id)}`
to the `messages.map` render and have `MessageRow` call it alongside its `postMessage`.

Render the dialog after the message list:

```tsx
      {rewindPreview && (
        <RewindDialog
          preview={rewindPreview}
          onCancel={() => { setRewindPreview(null); setRewindMessageId(null); }}
          onConfirm={() => {
            vscode.postMessage({ type: "rewindConfirm", messageId: rewindMessageId });
            setRewindPreview(null);
          }}
        />
      )}
```

- [ ] **Step 12: Run the full frontend suites**

Run: `npm run -w crucible-vscode-extension test`
Run: `npm run typecheck`
Expected: PASS on both.

- [ ] **Step 13: Commit**

```bash
git add apps/vscode-extension/
git commit -m "feat(webview): add the rewind affordance, confirm dialog and composer prefill"
```

---

### Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above. Produces no code.

- [ ] **Step 1: Document the feature**

Add a subsection under the chat interface section of `CLAUDE.md`, following the density and gotcha-first style of its neighbours:

```markdown
#### Chat rewind

Rewind a thread to an earlier user message: restores the files the agent touched,
truncates the transcript + controller history, retires the memories those turns wrote.
Spec/plan: `docs/superpowers/specs|plans/2026-08-16-chat-rewind*`.

- **Capture is copy-on-first-write at the `TurnEditSession.apply()` seam** (`chat/rewind.py`),
  BEFORE `_ensure_shadow` — the real workspace is the clean before-state there (the
  `shadow == real` invariant in `edit_session.py`'s docstring). Chat edits are
  instant-promoted and the turn shadow is `rmtree`'d at `close()`, so this is the only
  moment pre-edit content exists. Reverse-applying the persisted `unified_diff` does NOT
  work as an alternative: `patch/diffing.py` caps diffs at 400 lines / 24k chars.
- **One checkpoint per turn**, keyed on the user message that started it
  (`ChatMessage.id`). Continuations (`resolve_mode` → implement, `resolve_clarify`) fold
  into the thread's HIGHEST seq — same anchor, no open/closed bookkeeping.
- **`ChatMessage.id` is `str | None`, NOT a `default_factory`** — `model_validate` runs on
  raw dicts from `messages_json`, so a factory would mint a fresh id per read and every
  rewind anchor would 404 differently each poll. Legacy messages carry None and offer no
  affordance. Mirrored in editor-client Zod + webview `types.ts` (the `PendingGate.kind`
  footgun class).
- **Restore folds the span first-seen-wins** — the OLDEST pre-state per path is the right
  one, which is what makes multi-turn rewind correct without full snapshots. Per-file
  failures are collected and reported, never aborted on.
- **The four `controller_*` blobs are snapshotted whole, not truncated.**
  `controller_history_json` has no alignment to transcript messages, so truncating it to
  match has no correct implementation; a verbatim earlier copy sidesteps it and picks up
  todos/active skill/pinned seed for free.
- **Memory:** `retire_since(source_ref, cutoff)` keys on `source_ref` = the controller's
  `run_id` = the `thread_id`, catching thread- AND workspace-scoped memories with no
  schema change (`RecallEngine` already filters `valid_to IS NULL`). The compaction
  anchor is snapshotted per checkpoint and restored — a null snapshot **deletes** the
  anchor, since leaving a newer one keeps summarizing turns that no longer exist. Stale
  `compaction_segments` are accepted (downstream of the restored anchor). Best-effort
  throughout: memory never fails a rewind.
- **Refusals:** 409 while a turn is in flight; 409 when the span holds a non-terminal task
  (it would promote into the real workspace and re-write what was just reverted). Exec
  sessions are deliberately NOT blocking — they are background shells, left running and
  named in the confirm dialog. Commands already run are not undone, stated in the dialog.
- Env: `CRUCIBLE_REWIND_RETENTION_TURNS` (50) · `CRUCIBLE_REWIND_MAX_FILE_BYTES` (10000000).
```

Also add the two env vars to the "Key Configuration → Python backend env vars → Core" list.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document chat rewind's capture seam and refusal rules"
```

---

## Verification

After Task 9, confirm the whole thing before claiming completion:

```bash
cd services/agentd-py && pytest --color=no > /tmp/rewind-final-py.txt 2>&1; echo exit=$?; tail -5 /tmp/rewind-final-py.txt
cd - && npm run test && npm run typecheck
```

Then a live smoke against a real backend and dev host, since none of the above exercises the
actual UI path:

1. `bash scripts/stress/start-backend.sh --backend <provider> --workspace "$PWD/workspaces/crucible-stress"`
2. `code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/crucible-stress"`
3. Send a message that edits a file. Confirm the edit landed on disk.
4. Send a second message that edits the same file plus a new one.
5. Hover the FIRST user message, click ↺, confirm the dialog's counts, accept.
6. Verify: the first file is back to its original content, the created file is gone, both
   messages are gone from the transcript, and the composer holds the first message's text.
7. Send a message, and while the turn is running, confirm no ↺ button renders.
