# Controller Todo Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the reactive chat controller grind a multi-feature request to completion in one EDIT loop instead of bailing after one feature, via a model-authored todo ledger that hard-blocks `submit_changes` while items remain — and surface the live checklist to the user.

**Architecture:** A per-request `TodoLedger` (plain object, 5 states) is mutated by a `write_todos` tool (a `ToolSource`, no response-schema change, full-list rewrite), re-surfaced into every loop iteration's payload tail, and enforced by a deterministic gate in `ControllerLoop.submit_changes`. The ledger persists on the thread (`controller_todo_json`) so it survives the DECIDE→EDIT and clarify-resume loop boundaries, and is exposed via `/live` + a flat read-only checklist card.

**Tech Stack:** Python 3.13 backend (`services/agentd-py`), pytest + pytest-asyncio, SQLite (`ChatThreadStore`); TypeScript editor-client (Zod) + React webview-ui (vitest).

**Provenance:** Synthesized against `docs/todo_memory_plan.md` (the "Agenda" plan). Adopted from it: `blocked`/`cancelled` states, evidence-on-done, `/live` exposure, a flat live card, distilled decision policy. Rejected/deferred: op-delta mutations, recursive nesting, action-instead-of-tool, manual mutation-approval gate, mutation event log (see spec §9).

## Global Constraints

- Scope is the reactive controller only (`AI_EDITOR_CHAT_CONTROLLER=1`); within-request, no cross-turn persistence (deferred memory module).
- **No response-schema change** — `write_todos` is a `tool_call`; the flat + tight `oneOf` schemas stay byte-untouched.
- Full-list-rewrite ledger semantics (model resends the whole list each call) — no per-item id bookkeeping.
- Statuses are exactly: `pending`, `in_progress`, `done`, `blocked`, `cancelled`. The gate's "pending" set = `pending` ∪ `in_progress` only; `done`/`blocked`/`cancelled` do NOT block submit (so a `blocked` item can never deadlock the loop).
- KV-cache discipline: per-turn-varying fields (`todo_status`) go in the payload **tail**, never the cached head.
- The gate-block is a legitimate redirect, **not** malformed — must NOT count toward `_MAX_MALFORMED`.
- `/live` dedup invariant (CLAUDE.md): any durable signal consumed after the `lastLiveSignature` gate MUST be in the signature — `todos` included, or checklist updates get swallowed.
- Backstop (request-anchored completion check) is OUT OF SCOPE — deferred (spec §7).
- Run Python from `services/agentd-py` with the venv: `cd services/agentd-py && source .venv/bin/activate`. Run TS from repo root.

---

## File Structure

- **Create** `services/agentd-py/agentd/chat/todo_ledger.py` — `TodoItem` + `TodoLedger` (5 states).
- **Create** `services/agentd-py/agentd/chat/todo_source.py` — `write_todos` `ToolDefinition` + `TodoToolSource`.
- **Modify** `services/agentd-py/agentd/chat/storage.py` — `controller_todo_json` column + set/get + `ChatThread.controller_todos` population.
- **Modify** `services/agentd-py/agentd/chat/models.py` — `ChatThread.controller_todos`, `ThreadLiveState.todos`.
- **Modify** `services/agentd-py/agentd/chat/controller_loop.py` — hold ledger, gate `submit_changes`, re-surface.
- **Modify** `services/agentd-py/agentd/chat/controller_prompts.py` — `todo_status` tail; ledger-aware EDIT hint; `write_todos` teaching + enumerate-all + distilled policy + evidence-on-done.
- **Modify** `services/agentd-py/agentd/chat/controller.py` — rehydrate/build ledger, wire `TodoToolSource`, persist/clear, `AI_EDITOR_CONTROLLER_MAX_ITERS`.
- **Modify** `services/agentd-py/agentd/chat/live_state.py` — `resolve_thread_live` includes `todos`.
- **Modify** `apps/editor-client/src/contracts/task-contracts.ts` — `TodoItemSchema` + `ThreadLiveStateSchema.todos`.
- **Modify** `apps/vscode-extension/webview-ui/src/types.ts`, `hooks/useAppState.ts`, `components/LiveSlot.tsx`; **Create** `components/messages/TodoCard.tsx`; **Modify** `apps/vscode-extension/src/controller.ts` (poll mapping + signature).
- **Create** tests: `test_todo_ledger.py`, `test_controller_todo_tool.py`, `test_controller_todo_persistence.py`, `test_controller_todo_gate.py`, `test_controller_todo_integration.py`, `test_controller_todo_live.py`; **extend** `test_controller_payload.py`; **Create** webview `TodoCard.test.tsx`.

---

### Task 1: `TodoLedger` data object (5 states)

**Files:**
- Create: `services/agentd-py/agentd/chat/todo_ledger.py`
- Test: `services/agentd-py/tests/test_todo_ledger.py`

**Interfaces:**
- Produces: `TodoItem(title: str, status: str = "pending", note: str = "")`; `TodoLedger(items: list[TodoItem] = [])` with `replace(items) -> None`, `pending() -> list[TodoItem]`, `render() -> str`, `to_json() -> str`, `from_json(raw: str | None) -> TodoLedger`; constants `_STATUSES: tuple[str,...]`, `_GLYPH: dict[str,str]`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_todo_ledger.py
from agentd.chat.todo_ledger import TodoItem, TodoLedger


def test_pending_excludes_done_blocked_cancelled():
    led = TodoLedger()
    led.replace([
        TodoItem("Enemies", "done"),
        TodoItem("Jump", "in_progress"),
        TodoItem("Timer", "pending"),
        TodoItem("Sound", "blocked", note="needs audio asset"),
        TodoItem("Old", "cancelled"),
    ])
    # blocked + cancelled + done are NOT pending -> a blocked item cannot deadlock the gate
    assert [i.title for i in led.pending()] == ["Jump", "Timer"]


def test_render_includes_count_and_glyphs():
    led = TodoLedger()
    led.replace([TodoItem("A", "done"), TodoItem("B", "pending"), TodoItem("C", "blocked")])
    out = led.render()
    assert "3 items" in out and "(1 done)" in out
    assert "A" in out and "B" in out and "C" in out


def test_render_empty_is_blank():
    assert TodoLedger().render() == ""


def test_json_roundtrip_preserves_status_and_note():
    led = TodoLedger()
    led.replace([TodoItem("A", "blocked", note="why"), TodoItem("B", "cancelled")])
    back = TodoLedger.from_json(led.to_json())
    assert [(i.title, i.status, i.note) for i in back.items] == [
        ("A", "blocked", "why"), ("B", "cancelled", "")]


def test_from_json_none_is_empty():
    assert TodoLedger.from_json(None).items == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_todo_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.chat.todo_ledger'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/agentd-py/agentd/chat/todo_ledger.py
"""TodoLedger — per-request checklist the controller grinds to completion.

Mutated by the write_todos tool (full-list rewrite), re-surfaced into every loop
iteration's payload tail, enforced by ControllerLoop's submit_changes gate, and
shown to the user via /live. Plain state object: no I/O (storage persists via
to_json/from_json). Five states; blocked/cancelled adopted from the Agenda plan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# blocked = parked with an unblock reason in `note`; cancelled = abandoned but kept
# in the list (audit). The gate's pending set is pending|in_progress ONLY, so neither
# blocked nor cancelled nor done can deadlock the loop.
_STATUSES: tuple[str, ...] = ("pending", "in_progress", "done", "blocked", "cancelled")
_GLYPH: dict[str, str] = {
    "pending": "☐", "in_progress": "▶", "done": "✓", "blocked": "⛔", "cancelled": "~"}


@dataclass
class TodoItem:
    title: str
    status: str = "pending"
    note: str = ""  # holds evidence (done), unblock reason (blocked), or cancel reason


@dataclass
class TodoLedger:
    items: list[TodoItem] = field(default_factory=list)

    def replace(self, items: list[TodoItem]) -> None:
        """Full-list rewrite — the model resends the whole list each write_todos call."""
        self.items = list(items)

    def pending(self) -> list[TodoItem]:
        return [i for i in self.items if i.status in ("pending", "in_progress")]

    def render(self) -> str:
        """Compact one-line status for the payload tail; '' when no list exists."""
        if not self.items:
            return ""
        cells = " ".join(f"[{_GLYPH.get(i.status, '☐')} {i.title}]" for i in self.items)
        n_done = sum(1 for i in self.items if i.status == "done")
        return f"{len(self.items)} items ({n_done} done) — {cells}"

    def to_json(self) -> str:
        return json.dumps(
            [{"title": i.title, "status": i.status, "note": i.note} for i in self.items])

    @classmethod
    def from_json(cls, raw: str | None) -> "TodoLedger":
        if not raw:
            return cls()
        return cls(items=[
            TodoItem(
                title=str(d["title"]),
                status=str(d.get("status", "pending")),
                note=str(d.get("note", "")),
            )
            for d in json.loads(raw)
        ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_todo_ledger.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/todo_ledger.py services/agentd-py/tests/test_todo_ledger.py
git commit -m "feat(controller): add 5-state TodoLedger for chat-turn checklists"
```

---

### Task 2: `write_todos` tool + `TodoToolSource`

**Files:**
- Create: `services/agentd-py/agentd/chat/todo_source.py`
- Test: `services/agentd-py/tests/test_controller_todo_tool.py`

**Interfaces:**
- Consumes: `TodoLedger`, `TodoItem`, `_STATUSES` (Task 1); `ToolDefinition`, `ToolOutput` from `agentd.tools.registry`; the `ToolSource` shape from `agentd.tools.sources`.
- Produces: `TodoToolSource(ledger: TodoLedger)` (a `ToolSource`); `_WRITE_TODOS_DEF: ToolDefinition` (name `"write_todos"`).

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_controller_todo_tool.py
import pytest

from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource


def test_source_owns_only_write_todos():
    src = TodoToolSource(TodoLedger())
    assert src.owns("write_todos") is True
    assert src.owns("read_file") is False
    assert [d.name for d in src.definitions()] == ["write_todos"]


def test_definition_status_enum_has_five_states():
    d = TodoToolSource(TodoLedger()).definitions()[0]
    enum = d.parameters["properties"]["items"]["items"]["properties"]["status"]["enum"]
    assert set(enum) == {"pending", "in_progress", "done", "blocked", "cancelled"}


@pytest.mark.asyncio
async def test_write_todos_mutates_ledger_and_returns_render():
    led = TodoLedger()
    out = await TodoToolSource(led).execute("write_todos", {"items": [
        {"title": "Enemies", "status": "done", "note": "added in last edit"},
        {"title": "Jump", "status": "pending"},
    ]})
    assert out.is_error is False
    assert [(i.title, i.status) for i in led.items] == [("Enemies", "done"), ("Jump", "pending")]
    assert "Enemies" in out.output and "Jump" in out.output


@pytest.mark.asyncio
async def test_write_todos_rejects_bad_status_without_mutating():
    led = TodoLedger()
    out = await TodoToolSource(led).execute(
        "write_todos", {"items": [{"title": "X", "status": "doing"}]})
    assert out.is_error is True
    assert led.items == []


@pytest.mark.asyncio
async def test_write_todos_rejects_empty_items():
    out = await TodoToolSource(TodoLedger()).execute("write_todos", {"items": []})
    assert out.is_error is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.chat.todo_source'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/agentd-py/agentd/chat/todo_source.py
"""TodoToolSource — exposes the write_todos tool over a shared TodoLedger.

A ToolSource (the tools/sources.py seam) so adding it never touches the loop's tool
plumbing. The controller passes the SAME TodoLedger here and into ControllerLoop, so
a write_todos call is immediately visible to the loop's gate.
"""
from __future__ import annotations

from agentd.chat.todo_ledger import _STATUSES, TodoItem, TodoLedger
from agentd.tools.registry import ToolDefinition, ToolOutput

_WRITE_TODOS_DEF = ToolDefinition(
    name="write_todos",
    description=(
        "Create or update the todo list for a LARGE / multi-part change. Send the FULL "
        "list every call (full-list rewrite): every item with its current status. Use it "
        "when the request decomposes into multiple distinct features/steps; SKIP it for a "
        "single small edit. To reshape (split/insert/reorder), just resend the list in the "
        "new shape. Mark an item 'done' ONLY with evidence (cite the tool/edit result in "
        "'note'); 'blocked' (put the unblock condition in 'note') if you cannot proceed; "
        "'cancelled' (say why in 'note') to abandon one — never silently drop it. "
        "submit_changes is BLOCKED while any item is pending or in_progress."
    ),
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "status": {"type": "string", "enum": list(_STATUSES)},
                        "note": {"type": "string"},
                    },
                    "required": ["title", "status"],
                },
            }
        },
        "required": ["items"],
    },
)


class TodoToolSource:
    name = "todo"

    def __init__(self, ledger: TodoLedger) -> None:
        self._ledger = ledger

    def definitions(self) -> list[ToolDefinition]:
        return [_WRITE_TODOS_DEF]

    def owns(self, tool: str) -> bool:
        return tool == "write_todos"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != "write_todos":
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        raw_items = args.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            return ToolOutput(
                output="write_todos needs a non-empty 'items' array.", is_error=True)
        new_items: list[TodoItem] = []
        for it in raw_items:
            if not isinstance(it, dict) or not str(it.get("title", "")).strip():
                return ToolOutput(
                    output="each todo item needs a non-empty 'title'.", is_error=True)
            status = str(it.get("status", "pending"))
            if status not in _STATUSES:
                return ToolOutput(
                    output=f"invalid status {status!r}; use one of {list(_STATUSES)}.",
                    is_error=True)
            new_items.append(TodoItem(
                title=str(it["title"]).strip(), status=status, note=str(it.get("note", ""))))
        self._ledger.replace(new_items)
        return ToolOutput(output="Todo list updated:\n" + self._ledger.render())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_tool.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/todo_source.py services/agentd-py/tests/test_controller_todo_tool.py
git commit -m "feat(controller): add write_todos ToolSource (5 states, evidence-on-done)"
```

---

### Task 3: Persistence + `ChatThread.controller_todos`

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py` (`ChatThread`, ~line 73 after `controller_retrieval_seed`)
- Modify: `services/agentd-py/agentd/chat/storage.py` (migration ~line 43; `_todos_from_row` helper; populate in `list_threads`/`get_thread`; methods after `set_controller_gate` ~line 147)
- Test: `services/agentd-py/tests/test_controller_todo_persistence.py`

**Interfaces:**
- Produces: `ChatThread.controller_todos: list[dict] | None`; `ChatThreadStore.set_controller_todos(thread_id, raw: str | None) -> None`; `ChatThreadStore.get_controller_todos(thread_id) -> str | None`; `get_thread`/`list_threads` populate `controller_todos`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_controller_todo_persistence.py
from pathlib import Path

from agentd.chat.storage import ChatThreadStore
from agentd.chat.todo_ledger import TodoItem, TodoLedger


def test_set_get_roundtrip(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    led = TodoLedger()
    led.replace([TodoItem("A", "done"), TodoItem("B", "pending")])
    store.set_controller_todos(thread.thread_id, led.to_json())
    back = TodoLedger.from_json(store.get_controller_todos(thread.thread_id))
    assert [(i.title, i.status) for i in back.items] == [("A", "done"), ("B", "pending")]


def test_get_default_none(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    assert store.get_controller_todos(thread.thread_id) is None


def test_clear_with_none(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_todos(thread.thread_id, "[]")
    store.set_controller_todos(thread.thread_id, None)
    assert store.get_controller_todos(thread.thread_id) is None


def test_chatthread_carries_controller_todos(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_todos(thread.thread_id, '[{"title": "A", "status": "pending", "note": ""}]')
    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    assert reloaded.controller_todos == [{"title": "A", "status": "pending", "note": ""}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_persistence.py -v`
Expected: FAIL — `AttributeError: 'ChatThreadStore' object has no attribute 'set_controller_todos'`

- [ ] **Step 3a: Add the `ChatThread` field**

In `services/agentd-py/agentd/chat/models.py`, in `ChatThread`, right after the `controller_retrieval_seed: ...` field (~line 73), add:

```python
    # Request-scoped todo ledger (raw item dicts), surfaced to /live so the user sees the
    # live checklist. Populated from controller_todo_json; None until the first write_todos.
    controller_todos: list[dict[str, Any]] | None = None
```

- [ ] **Step 3b: Add the migration column**

In `services/agentd-py/agentd/chat/storage.py`, after the `controller_seed_json` ALTER (~line 43), add:

```python
        # Request-scoped todo ledger (survives DECIDE->EDIT + clarify resume), added later.
        if "controller_todo_json" not in existing:
            self._conn.execute("ALTER TABLE chat_threads ADD COLUMN controller_todo_json TEXT")
```

- [ ] **Step 3c: Add a row helper and populate both constructors**

In `services/agentd-py/agentd/chat/storage.py`, add a static helper next to `_seed_from_row` (~line 54):

```python
    @staticmethod
    def _todos_from_row(row: sqlite3.Row) -> list[dict] | None:
        raw = row["controller_todo_json"]
        return json.loads(raw) if raw else None
```

Then in BOTH `list_threads` (~line 92) and `get_thread` (~line 113), add to the `ChatThread(...)` kwargs (right after `controller_retrieval_seed=self._seed_from_row(row),`):

```python
                controller_todos=self._todos_from_row(row),
```

(Note: `get_thread` uses `controller_todos=self._todos_from_row(row),` with the same indentation as its sibling kwargs.)

- [ ] **Step 3d: Add the set/get methods**

In `services/agentd-py/agentd/chat/storage.py`, after `set_controller_gate` (~line 147), add:

```python
    def set_controller_todos(self, thread_id: str, raw: str | None) -> None:
        """Persist (raw = TodoLedger.to_json()) or clear (raw = None) the request's todo
        ledger. Mirrors set_controller_history: an in-place durable update the next loop
        run (mode-gate / clarify resume) rehydrates via TodoLedger.from_json."""
        self._conn.execute(
            "UPDATE chat_threads SET controller_todo_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    def get_controller_todos(self, thread_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT controller_todo_json FROM chat_threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row["controller_todo_json"] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_persistence.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/models.py services/agentd-py/agentd/chat/storage.py services/agentd-py/tests/test_controller_todo_persistence.py
git commit -m "feat(controller): persist request-scoped todo ledger + carry on ChatThread"
```

---

### Task 4: `ControllerLoop` gate + re-surfacing

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py` (import ~line 13; `__init__` ~lines 176-194; `_iterate` for-loop top ~line 266; `submit_changes` branch ~lines 473-482)
- Test: `services/agentd-py/tests/test_controller_todo_gate.py`

**Interfaces:**
- Consumes: `TodoLedger` (Task 1), `TodoToolSource` (Task 2), `AggregatingToolRegistry`, `ControllerPhaseSM`, `EventBroadcaster`.
- Produces: `ControllerLoop.__init__(..., todo_ledger: TodoLedger | None = None)`; loop reads `self._ledger.pending()` to gate `submit_changes` and sets `plan_context["todo_status"] = self._ledger.render()` each iteration.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_controller_todo_gate.py
import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.tools.sources import AggregatingToolRegistry


class _ScriptedReasoning:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    async def create_controller_step(self, **kwargs):
        resp = self._responses[self._i]
        self._i += 1
        return resp


def _edit_sm() -> ControllerPhaseSM:
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    return sm


def _wt(items):
    return {"type": "tool_call", "thought": "todos", "tool": "write_todos",
            "args": {"items": items}}


def _loop(ledger, reasoning):
    return ControllerLoop(
        reasoning, AggregatingToolRegistry([TodoToolSource(ledger)]), EventBroadcaster(),
        channel_id="c1", phase_sm=_edit_sm(), todo_ledger=ledger)


@pytest.mark.asyncio
async def test_submit_blocked_until_ledger_clear():
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "pending"}, {"title": "B", "status": "pending"}]),
        {"type": "submit_changes", "thought": "?", "summary": "early"},     # BLOCKED (2 pending)
        _wt([{"title": "A", "status": "done"}, {"title": "B", "status": "done"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "all done"},  # passes
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert outcome.kind == "submit_changes" and outcome.text == "all done"
    assert any("BLOCKED" in str(m.get("content", "")) for m in (outcome.history or []))


@pytest.mark.asyncio
async def test_blocked_item_does_not_deadlock_submit():
    # One done, one blocked -> nothing pending -> submit must pass (blocked != pending).
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "done"},
             {"title": "B", "status": "blocked", "note": "needs API key"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "A done, B blocked"},
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert outcome.kind == "submit_changes" and outcome.text == "A done, B blocked"


@pytest.mark.asyncio
async def test_gate_block_not_counted_as_malformed():
    ledger = TodoLedger()
    sub = {"type": "submit_changes", "thought": "?", "summary": "x"}
    loop = _loop(ledger, _ScriptedReasoning([
        _wt([{"title": "A", "status": "pending"}]),
        sub, sub, sub, sub,   # 4 blocked in a row would trip _MAX_MALFORMED (3) if counted
        _wt([{"title": "A", "status": "done"}]),
        {"type": "submit_changes", "thought": "ok", "summary": "done"},
    ]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=20)
    assert outcome.kind == "submit_changes" and outcome.text == "done"


@pytest.mark.asyncio
async def test_submit_passes_with_no_ledger():
    ledger = TodoLedger()
    loop = _loop(ledger, _ScriptedReasoning([
        {"type": "submit_changes", "thought": "ok", "summary": "nothing pending"}]))
    outcome = await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=5)
    assert outcome.kind == "submit_changes" and outcome.text == "nothing pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_gate.py -v`
Expected: FAIL — `TypeError: ControllerLoop.__init__() got an unexpected keyword argument 'todo_ledger'`

- [ ] **Step 3a: Import the ledger**

In `services/agentd-py/agentd/chat/controller_loop.py`, after `from agentd.chat.tool_events import trace_to_tool_events` (~line 13), add:

```python
from agentd.chat.todo_ledger import TodoLedger
```

- [ ] **Step 3b: Accept and store the ledger in `__init__`**

In `ControllerLoop.__init__`, add the kwarg after `edit_session` and store it:

```python
        phase_sm: ControllerPhaseSM,
        edit_session: TurnEditSession | None = None,
        todo_ledger: TodoLedger | None = None,
    ) -> None:
        self._reasoning = reasoning
        self._registry = registry
        self._broadcaster = broadcaster
        self._channel_id = channel_id
        self._sm = phase_sm
        self._edit = edit_session
        self._ledger = todo_ledger or TodoLedger()
```

(Keep the existing `self._calls = []` / `self._results = []` / `self._thinking = []` lines.)

- [ ] **Step 3c: Re-surface the ledger each iteration**

In `_iterate`, inside `for iteration in range(max_iters + 1):`, right after the `if iteration == 0:` "Thinking…" broadcast block, add:

```python
            # Re-surface the live todo ledger into the payload tail every iteration so the
            # model re-reads its own contract (the detail that makes discretion stick). Empty
            # string when no list exists -> build_controller_step_payload omits it.
            plan_context["todo_status"] = self._ledger.render()
```

- [ ] **Step 3d: Gate `submit_changes`**

In `_iterate`, replace the existing `submit_changes` branch with:

```python
            if atype == "submit_changes":
                # Hard gate: a non-empty ledger is a contract. Block submit while items are
                # pending/in_progress (NOT blocked/cancelled/done — those never deadlock) and
                # redirect to the next item. This is a legitimate redirect, NOT a malformed
                # action, so it does NOT touch consecutive_malformed — only max_iters bounds it.
                still_open = self._ledger.pending()
                if still_open:
                    titles = ", ".join(i.title for i in still_open)
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "",
                        "content": (
                            f"submit_changes BLOCKED — {len(still_open)} todo item(s) still "
                            f"open: {titles}. Continue with the next item (one edit at a time), "
                            "then call write_todos to mark it 'done' (cite evidence in 'note'). "
                            "If one is genuinely stuck, mark it 'blocked' (with the unblock "
                            "reason) or 'cancelled' (with why). Do NOT submit until nothing is "
                            "pending."),
                    })
                    continue
                # The shadow is closed by run()'s finally on return (no double-close).
                history.append(assistant_turn(resp))
                # Deterministic fallback for an empty summary — unlike answer/clarify we do NOT
                # retry (the edits are already promoted; retrying-to-exhaustion would convert a
                # done turn into a failure). A non-empty summary keeps the closing chat message
                # from collapsing to nothing (the "no closing message" gap).
                summary = str(resp.get("summary", "")).strip() or "Changes submitted."
                return ControllerOutcome(
                    kind="submit_changes", text=summary, history=history)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_gate.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run existing loop tests to confirm no regression**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_loop_edit.py tests/test_controller_loop_explore_answer.py tests/test_controller_loop_clarify_propose.py tests/test_controller_loop_resilience.py -v`
Expected: PASS (the new `todo_ledger` kwarg defaults to `None`)

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/tests/test_controller_todo_gate.py
git commit -m "feat(controller): gate submit_changes on todo ledger (blocked-safe) + re-surface"
```

---

### Task 5: Payload tail + prompt steering + decision policy

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (`build_controller_step_payload` ~lines 319-388; `CONTROLLER_SYSTEM_PROMPT` propose_mode section ~line 247 and a new block ~line 285)
- Test: extend `services/agentd-py/tests/test_controller_payload.py`

**Interfaces:**
- Consumes: `plan_context["todo_status"]` (Task 4).
- Produces: `build_controller_step_payload` adds `payload["todo_status"]` (non-empty only); ledger-aware EDIT hint; system prompt teaches `write_todos`, enumerate-all, the distilled decision policy, and evidence-on-done.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_controller_payload.py`:

```python
def test_todo_status_lands_in_tail_when_present():
    from agentd.chat.controller_prompts import build_controller_step_payload
    payload = build_controller_step_payload(
        {"goal": "add features", "workspace_path": "/w",
         "todo_status": "2 items (1 done) — [✓ A] [☐ B]"},
        history=[], tool_definitions=[], phase="EDIT")
    assert payload.get("todo_status") == "2 items (1 done) — [✓ A] [☐ B]"
    keys = list(payload.keys())
    assert keys.index("todo_status") > keys.index("workspace_path")


def test_todo_status_omitted_when_blank():
    from agentd.chat.controller_prompts import build_controller_step_payload
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "todo_status": ""},
        history=[], tool_definitions=[], phase="EDIT")
    assert "todo_status" not in payload


def test_system_prompt_teaches_write_todos_and_policy():
    from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT
    p = CONTROLLER_SYSTEM_PROMPT
    assert "write_todos" in p
    assert "enumerate" in p.lower()
    assert "evidence" in p.lower()          # evidence-on-done rule
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_payload.py -v -k "todo_status or write_todos"`
Expected: FAIL — `todo_status` not added; `write_todos` not in prompt.

- [ ] **Step 3a: Add `todo_status` to the payload tail**

In `build_controller_step_payload`, immediately after `payload["goal"] = plan_context.get("goal", "")` (~line 332), add:

```python
    # Per-turn-varying ledger status (Task 4 sets it each iteration). Tail-only so the KV
    # prefix stays stable; omitted when blank (no list) so simple turns are byte-identical.
    todo_status = plan_context.get("todo_status")
    if isinstance(todo_status, str) and todo_status:
        payload["todo_status"] = todo_status
```

- [ ] **Step 3b: Make the EDIT mid-turn hint ledger-aware**

In `build_controller_step_payload`, in `if phase == "EDIT":`, replace the `else:` hint (begins `"FIRST reflect on your last edit's result: ..."`) with:

```python
        else:
            hint = (
                "FIRST reflect on your last edit's result: did it apply ('applied+promoted') or "
                "fail ('PATCH FAILED: …')? If a todo list is active, todo_status shows the "
                "remaining items — work the next pending one; submit_changes is BLOCKED until "
                "nothing is pending. THEN choose ONE: (A) CONTINUE/FIX — if it failed, re-read "
                "the exact lines and re-emit ONE corrected op (do NOT repeat the failed op "
                "verbatim); for the next item emit type='edit', and after it applies call "
                "write_todos to mark it 'done' (cite evidence in 'note'). (B) DONE — only when no "
                "items remain, emit type='submit_changes' with a summary. A read-resistant "
                "blocker → mark the item 'blocked' or use type='clarify'. Do NOT propose_mode again."
            )
```

- [ ] **Step 3c: Teach enumerate-all in the propose_mode section**

In `CONTROLLER_SYSTEM_PROMPT`, change the line `  "recommended": EXACTLY one of edit | create_task | resume | explain.` to:

```
  "recommended": EXACTLY one of edit | create_task | resume | explain.
  When the change is LARGE / multi-part, "plan_sketch" MUST enumerate EVERY distinct part
  (e.g. "1. Enemies … 2. Jump … 3. Timer …"), not just the first — that full scope becomes
  your todo list.
```

- [ ] **Step 3d: Add the distilled TODO / AGENDA policy block**

In `CONTROLLER_SYSTEM_PROMPT`, immediately before the final paragraph beginning `After an edit, prefer live tools` (~line 285), insert:

```
TODO LIST POLICY (the write_todos tool) — optional working memory, NOT default:
Create a list (call write_todos with all items, status "pending") ONLY when the request needs
several distinct features/steps or more than ~2 edit cycles. SKIP it for a single small edit, a
plain answer, or a clarification. Once a list exists it is your contract: implement items ONE AT
A TIME (emit type='edit' for the next item, then write_todos to flip it 'done'), and resend the
WHOLE list each call (reshape freely — split/insert/reorder by resending in the new shape).
submit_changes is BLOCKED until nothing is pending — this is how you finish the whole request
instead of stopping after one feature.
Rules: mark 'done' ONLY with concrete evidence (a tool/edit result) cited in 'note' — never from
memory. Mark 'blocked' (with the unblock condition) instead of faking done when stuck; mark
'cancelled' (with why) instead of silently dropping. Every change must serve the user's original
goal — no speculative nice-to-haves.

```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_payload.py -v`
Expected: PASS (all, including 3 new)

- [ ] **Step 5: Confirm the response schema is byte-unchanged**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_schema.py tests/test_controller_tight_schema.py -v`
Expected: PASS (no response-schema variant added — write_todos rides `tool_call`)

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/tests/test_controller_payload.py
git commit -m "feat(controller): todo_status in payload tail + write_todos/enumerate/evidence policy"
```

---

### Task 6: Wire the ledger into `ChatController` + budget knob

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py` (imports ~lines 19-25; `_build_registry` ~lines 172-181; `_run_loop` ~lines 273-348)
- Test: `services/agentd-py/tests/test_controller_todo_integration.py`

**Interfaces:**
- Consumes: `TodoLedger`, `TodoToolSource`, `set_/get_controller_todos`, `ControllerLoop(todo_ledger=…)`, `ScriptedReasoningEngine`.
- Produces: per-request ledger rehydrated at `_run_loop` start, persisted on non-terminal outcomes, cleared on terminal (`submit_changes`/`answer`); `AI_EDITOR_CONTROLLER_MAX_ITERS` (default `500`) passed to `loop.run`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_controller_todo_integration.py
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.chat.todo_ledger import TodoLedger
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


def _ctrl(tmp_path, store, responses):
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=responses),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)


@pytest.mark.asyncio
async def test_write_todos_persists_on_nonterminal_turn(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = _ctrl(tmp_path, store, [
        {"type": "tool_call", "thought": "plan", "tool": "write_todos",
         "args": {"items": [{"title": "Enemies", "status": "pending"},
                            {"title": "Jump", "status": "pending"}]}},
        {"type": "propose_mode", "thought": "big", "plan_sketch": "1. Enemies 2. Jump",
         "recommended": "edit", "reason": "multi-part",
         "options": [{"mode": "edit", "label": "Edit inline now", "description": "do it"},
                     {"mode": "explain", "label": "Just explain", "description": "describe"}]},
    ])
    await ctrl.handle_message(thread.thread_id, "add enemies and jump", channel_id="c1")
    led = TodoLedger.from_json(store.get_controller_todos(thread.thread_id))
    assert [i.title for i in led.items] == ["Enemies", "Jump"]


@pytest.mark.asyncio
async def test_terminal_answer_clears_persisted_ledger(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    store.set_controller_todos(thread.thread_id, '[{"title": "stale", "status": "done", "note": ""}]')
    ctrl = _ctrl(tmp_path, store, [{"type": "answer", "thought": "t", "answer": "done"}])
    await ctrl.handle_message(thread.thread_id, "what does this do", channel_id="c1")
    assert store.get_controller_todos(thread.thread_id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_integration.py -v`
Expected: FAIL — `write_todos` not registered (ledger never set) and the clear test fails.

- [ ] **Step 3a: Add imports**

In `services/agentd-py/agentd/chat/controller.py`, near the other `from agentd.chat...` imports (~lines 19-25), add:

```python
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
```

Confirm `os` is imported at the top; if not, add `import os` with the stdlib imports.

- [ ] **Step 3b: Let `_build_registry` accept a ledger**

Replace `_build_registry` (~lines 172-181) with:

```python
    def _build_registry(
        self,
        command_approval_callback: object | None = None,
        todo_ledger: TodoLedger | None = None,
    ) -> AggregatingToolRegistry:
        sources: list[object] = [BuiltinToolSource(
            shadow_root=Path(self._workspace_path),
            real_workspace_path=Path(self._workspace_path),
            semantic_index=getattr(self._retrieval, "_semantic_index", None),
            command_approval_callback=command_approval_callback,
        )]
        if todo_ledger is not None:
            sources.append(TodoToolSource(todo_ledger))
        return AggregatingToolRegistry(sources)
```

- [ ] **Step 3c: Rehydrate, wire, persist/clear in `_run_loop`**

In `_run_loop`, after `sm = ControllerPhaseSM()` and before building the loop, add:

```python
        # Request-scoped todo ledger: rehydrate so it survives the DECIDE->EDIT (mode gate)
        # and clarify-resume loop boundaries within one request.
        ledger = TodoLedger.from_json(self._store.get_controller_todos(thread_id))
```

Change the `loop = ControllerLoop(...)` construction to:

```python
        loop = ControllerLoop(
            self._reasoning, self._build_registry(command_cb, ledger), self._broadcaster,
            channel_id=channel_id, phase_sm=sm, edit_session=edit, todo_ledger=ledger)
```

Change the `outcome = await loop.run(` call to pass the budget knob:

```python
        max_iters = int(os.environ.get("AI_EDITOR_CONTROLLER_MAX_ITERS", "500"))
        outcome = await loop.run(
            plan_context, max_iters=max_iters, seed_history=seed_history,
            auto_accept_edits=(not is_review), edit_decision_cb=edit_cb,
            edit_record_cb=record_cb, retrieval_delta_cb=self._retrieval_delta_cb,
            on_pills_update=pills_cb)
```

After `self._store.set_controller_history(thread_id, outcome.history or [])` (~line 339), add:

```python
        # Persist the ledger across this request's loop boundaries; clear on a terminal
        # outcome so the next request starts fresh. propose_mode/clarify are non-terminal —
        # the follow-on loop rehydrates the in-progress list.
        if outcome.kind in ("submit_changes", "answer"):
            self._store.set_controller_todos(thread_id, None)
        else:
            self._store.set_controller_todos(
                thread_id, ledger.to_json() if ledger.items else None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_integration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full controller suite**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/ -k controller -v`
Expected: PASS (all, including pre-existing)

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_controller_todo_integration.py
git commit -m "feat(controller): wire todo ledger into ChatController + AI_EDITOR_CONTROLLER_MAX_ITERS"
```

---

### Task 7: Expose the ledger on `/live` (backend + contract)

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py` (`ThreadLiveState`, after `task_narrative` ~line 101)
- Modify: `services/agentd-py/agentd/chat/live_state.py` (`resolve_thread_live` ~lines 120-139)
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`ThreadLiveStateSchema` ~line 255)
- Test: `services/agentd-py/tests/test_controller_todo_live.py`

**Interfaces:**
- Produces: `ThreadLiveState.todos: list[dict] | None`; `resolve_thread_live` populates `todos` in BOTH branches from `thread.controller_todos`; editor-client `TodoItemSchema` + `ThreadLiveStateSchema.todos`.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_controller_todo_live.py
from agentd.chat.live_state import resolve_thread_live
from agentd.chat.models import ChatThread


def _get_task_raises(_tid):
    raise KeyError("no task")


def test_todos_surface_with_no_task_no_gate():
    thread = ChatThread(
        thread_id="t1", workspace_path="/w",
        controller_todos=[{"title": "A", "status": "pending", "note": ""}])
    live = resolve_thread_live(thread, active_task_id=None, get_task=_get_task_raises)
    assert live.todos == [{"title": "A", "status": "pending", "note": ""}]


def test_todos_surface_alongside_controller_gate():
    from agentd.chat.models import PendingGate
    thread = ChatThread(
        thread_id="t1", workspace_path="/w",
        pending_controller_gate=PendingGate(kind="mode", payload={"x": 1}),
        controller_todos=[{"title": "A", "status": "in_progress", "note": ""}])
    live = resolve_thread_live(thread, active_task_id=None, get_task=_get_task_raises)
    assert live.pending_gate is not None and live.pending_gate.kind == "mode"
    assert live.todos == [{"title": "A", "status": "in_progress", "note": ""}]


def test_no_todos_is_none():
    thread = ChatThread(thread_id="t1", workspace_path="/w")
    live = resolve_thread_live(thread, active_task_id=None, get_task=_get_task_raises)
    assert live.todos is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_live.py -v`
Expected: FAIL — `AttributeError: 'ThreadLiveState' object has no attribute 'todos'`

- [ ] **Step 3a: Add the `ThreadLiveState` field**

In `services/agentd-py/agentd/chat/models.py`, in `ThreadLiveState`, after `task_narrative: TaskNarrative | None = None` (~line 101), add:

```python
    # The request's live todo checklist (raw item dicts), surfaced regardless of an active
    # task/gate so the UI can show progress. None when no list exists.
    todos: list[dict[str, Any]] | None = None
```

- [ ] **Step 3b: Populate `todos` in both `resolve_thread_live` branches**

In `services/agentd-py/agentd/chat/live_state.py`, replace `resolve_thread_live`'s body with:

```python
    todos = thread.controller_todos if thread is not None else None
    if thread is not None and thread.pending_controller_gate is not None:
        return ThreadLiveState(
            active_task_id=active_task_id,
            pending_gate=thread.pending_controller_gate,
            todos=todos,
        )
    base = resolve_live_state(active_task_id, get_task)
    base.todos = todos  # ThreadLiveState is a mutable pydantic model; set after build
    return base
```

- [ ] **Step 3c: Add the editor-client contract**

In `apps/editor-client/src/contracts/task-contracts.ts`, just above `export const ThreadLiveStateSchema = z.object({` (~line 255), add:

```typescript
export const TodoItemSchema = z.object({
  title: z.string(),
  status: z.enum(["pending", "in_progress", "done", "blocked", "cancelled"]),
  note: z.string().optional().default(""),
});
export type TodoItem = z.infer<typeof TodoItemSchema>;
```

Then inside `ThreadLiveStateSchema`, after the `taskNarrative: ...` line (~line 266), add:

```typescript
  todos: z.array(TodoItemSchema).nullable().optional(),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_controller_todo_live.py -v`
Expected: PASS (3 passed)

Run: `npm run -w @ai-editor/editor-client build && npm run -w @ai-editor/editor-client test`
Expected: build OK; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/models.py services/agentd-py/agentd/chat/live_state.py apps/editor-client/src/contracts/task-contracts.ts services/agentd-py/tests/test_controller_todo_live.py
git commit -m "feat(controller): expose todo ledger on /live (ThreadLiveState.todos + contract)"
```

---

### Task 8: Flat live checklist card (webview)

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/messages/TodoCard.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (live view types + WebviewMessage + AppState)
- Modify: `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts` (initial state + reducer)
- Modify: `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx` (render TodoCard)
- Modify: `apps/vscode-extension/src/controller.ts` (`pollThreadLiveState`: signature + post `renderLiveTodos`)
- Test: `apps/vscode-extension/webview-ui/src/test/TodoCard.test.tsx`

**Interfaces:**
- Consumes: `ThreadLiveState.todos` over `/live` (Task 7).
- Produces: `LiveTodosView` (`{ items: {title,status,note}[] }`); webview message `{ type: "renderLiveTodos"; todos: LiveTodosView | null }`; `AppState.liveTodos`; `TodoCard` component; `LiveSlot` renders it.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/test/TodoCard.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TodoCard } from "../components/messages/TodoCard";

describe("TodoCard", () => {
  it("renders a progress header and each item with a status glyph", () => {
    render(<TodoCard items={[
      { title: "Enemies", status: "done", note: "" },
      { title: "Jump", status: "in_progress", note: "" },
      { title: "Timer", status: "pending", note: "" },
      { title: "Sound", status: "blocked", note: "needs asset" },
    ]} />);
    expect(screen.getByText(/1 of 4/i)).toBeTruthy();   // done count (cancelled excluded from total)
    expect(screen.getByText("Enemies")).toBeTruthy();
    expect(screen.getByText("Jump")).toBeTruthy();
    expect(screen.getByText(/needs asset/i)).toBeTruthy();  // blocked reason shown
  });

  it("excludes cancelled items from the done/total count but still lists them", () => {
    render(<TodoCard items={[
      { title: "A", status: "done", note: "" },
      { title: "B", status: "cancelled", note: "superseded" },
    ]} />);
    expect(screen.getByText(/1 of 1/i)).toBeTruthy();   // cancelled not counted in total
    expect(screen.getByText("B")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w @ai-editor/vscode-extension test -- TodoCard` (or the webview-ui vitest runner used by the project)
Expected: FAIL — cannot resolve `../components/messages/TodoCard`.

- [ ] **Step 3a: Create the `TodoCard` component**

```tsx
// apps/vscode-extension/webview-ui/src/components/messages/TodoCard.tsx
import type { TodoItem } from "../../types";

const GLYPH: Record<TodoItem["status"], string> = {
  pending: "☐", in_progress: "▶", done: "✓", blocked: "⛔", cancelled: "✕",
};

/**
 * TodoCard — read-only flat checklist of the controller's live todo ledger.
 * Nested items + per-mutation approval are deferred (spec §9); v1 is a flat list.
 */
export function TodoCard({ items }: { items: TodoItem[] }) {
  // cancelled items are listed (audit) but excluded from the progress denominator.
  const counted = items.filter((i) => i.status !== "cancelled");
  const done = counted.filter((i) => i.status === "done").length;
  return (
    <div className="rounded border border-[var(--vscode-panel-border)] p-2 text-sm">
      <div className="mb-1 font-semibold">
        Todo — {done} of {counted.length} done
      </div>
      <ul className="flex flex-col gap-0.5">
        {items.map((it, idx) => (
          <li
            key={`${idx}:${it.title}`}
            className={it.status === "cancelled" ? "line-through opacity-60" : ""}
          >
            <span className="mr-1">{GLYPH[it.status]}</span>
            <span>{it.title}</span>
            {it.note && (it.status === "blocked" || it.status === "cancelled") && (
              <span className="ml-1 opacity-70">— {it.note}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3b: Add the types**

In `apps/vscode-extension/webview-ui/src/types.ts`:

After `export interface LivePlanView { ... }` (~line 61), add:

```typescript
export interface TodoItem {
  title: string;
  status: "pending" | "in_progress" | "done" | "blocked" | "cancelled";
  note: string;
}
export interface LiveTodosView { items: TodoItem[] }
```

In the `WebviewMessage` union (the `renderLive*` group, ~line 110-116), add:

```typescript
  | { type: "renderLiveTodos"; todos: LiveTodosView | null }
```

In the `AppState`-shaped interface (the block with `liveGate/livePlan/liveReview/liveError`, ~line 165-168), add:

```typescript
  liveTodos: LiveTodosView | null;
```

- [ ] **Step 3c: Wire the reducer**

In `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts`:

In the initial state (alongside `liveGate: null,` etc., ~line 32-35), add:

```typescript
  liveTodos: null,
```

In the reducer, alongside the `renderLivePlan`/`clearLivePlan` cases (~line 311-314), add:

```typescript
    case "renderLiveTodos":
      return { ...state, liveTodos: msg.todos };
```

(No separate clear case needed — `renderLiveTodos` with `todos: null` clears it.)

- [ ] **Step 3d: Render it in `LiveSlot`**

In `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx`:

Add the import: `import { TodoCard } from "./messages/TodoCard";` and `LiveTodosView` to the type import on line 2.

Add `liveTodos: LiveTodosView | null;` to `Props` (line 41-48), include it in the `hasContent` check, thread it from the caller, and render before `liveGate`:

```tsx
      {liveTodos !== null && liveTodos.items.length > 0 && (
        <TodoCard items={liveTodos.items} />
      )}
```

Update the caller of `LiveSlot` (the component in `ThreadView.tsx` that passes `liveGate`/`livePlan`/…) to also pass `liveTodos={state.liveTodos}`.

- [ ] **Step 3e: Map `/live` → `renderLiveTodos` in controller.ts (MIND THE SIGNATURE)**

In `apps/vscode-extension/src/controller.ts`, in `pollThreadLiveState` (~line 1548):

1. Add `todos: live.todos` to the `lastLiveSignature` object (~line 1600). **This is mandatory** — per the CLAUDE.md `/live` dedup invariant, a durable signal consumed after the dedup gate that is NOT in the signature gets swallowed and never renders.
2. After the dedup gate, post the webview message:

```typescript
    this.ui.postMessage({
      type: "renderLiveTodos",
      todos: live.todos && live.todos.length > 0 ? { items: live.todos } : null,
    });
```

(Match the exact `postMessage`/UI-send helper the neighboring `renderLivePlan`/`renderLiveReview` posts use in this file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w @ai-editor/vscode-extension test -- TodoCard`
Expected: PASS (2 passed).

Run: `npm run build && npm run typecheck`
Expected: build + typecheck clean across workspaces.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/TodoCard.tsx apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/hooks/useAppState.ts apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx apps/vscode-extension/webview-ui/src/components/ThreadView.tsx apps/vscode-extension/src/controller.ts apps/vscode-extension/webview-ui/src/test/TodoCard.test.tsx
git commit -m "feat(controller-ui): render the live todo checklist in the chat live slot"
```

---

### Task 9: Full-suite gate + CLAUDE.md note

**Files:**
- Modify: `CLAUDE.md` (the "Reactive controller" section)

- [ ] **Step 1: Run the entire Python suite**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/ -q`
Expected: all pass — read the summary line, do NOT trust a piped exit code.

- [ ] **Step 2: Run the entire TS suite**

Run: `npm run build && npm run typecheck && npm run test`
Expected: all green across `editor-client` + `vscode-extension`.

- [ ] **Step 3: Document the feature in CLAUDE.md**

In `CLAUDE.md`, under the "Reactive controller" section, add:

```markdown
- **Todo ledger (multi-feature completion):** in EDIT/DECIDE the controller can call the `write_todos` tool (a `TodoToolSource` over a per-request `TodoLedger`, `chat/todo_ledger.py` + `chat/todo_source.py`; 5 states pending/in_progress/done/blocked/cancelled, full-list-rewrite) to track a large/multi-part change. `submit_changes` is **hard-blocked** in `ControllerLoop` while any item is pending/in_progress (blocked/cancelled/done never deadlock it; not counted as malformed — only `max_iters` bounds it). The status is re-surfaced into the payload tail (`todo_status`) every iteration; persisted on `chat_threads.controller_todo_json` (request-scoped — survives DECIDE→EDIT + clarify resume; cleared on terminal); exposed via `/live` (`ThreadLiveState.todos`) and rendered as a flat read-only `TodoCard` in the live slot (**`todos` MUST be in controller.ts `lastLiveSignature`** or updates are deduped away). Discretionary (steered via propose_mode "enumerate every part" + a TODO LIST POLICY block; `done` requires evidence in `note`). `AI_EDITOR_CONTROLLER_MAX_ITERS` (default 500) is the loop cap — real within-turn limit is the context window until the memory module. NO completion backstop yet (deferred, spec §7); op-deltas/nesting/action-form/manual-approval/event-log deferred (spec §9).
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(controller): document the todo ledger + write_todos + /live card"
```

---

## Self-Review

**Spec coverage:**
- §4.1 TodoLedger (5 states, pending excludes blocked/cancelled) → Task 1. ✓
- §4.2 write_todos/TodoToolSource → Task 2. ✓
- §4.3 gate (blocked-safe) + re-surface → Task 4. ✓
- §4.4 prompt steering (todo_status tail, EDIT hint, teaching, enumerate-all, evidence, distilled policy) → Task 5. ✓
- §4.5 controller wiring → Task 6. ✓
- §4.6 persistence (column + set/get + ChatThread.controller_todos) → Task 3 + Task 6 (rehydrate/clear). ✓
- §4.7 budget knob → Task 6. ✓
- §4.8 /live exposure (ThreadLiveState.todos + resolve_thread_live + editor-client schema) → Task 7. ✓
- §4.9 flat live card → Task 8. ✓
- §3 evidence-on-done + blocked → Tasks 2/4/5. ✓
- §6 testing → Tasks 1-8. ✓
- §7 backstop / §8 memory / §9 synthesis deferrals → intentionally NOT implemented; CLAUDE.md note records the gaps (Task 9). ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Code steps show complete code; the two controller.ts mapping spots (3e) give exact insertions + the dedup invariant, pointing at the neighboring `renderLivePlan` lines as the template (existing-code pattern, not a placeholder). ✓

**Type consistency:** `TodoLedger`/`TodoItem`/`replace`/`pending`/`render`/`to_json`/`from_json`, `_STATUSES` (5), `TodoToolSource(ledger)`, `write_todos`, `set_/get_controller_todos`, `ChatThread.controller_todos`, `ThreadLiveState.todos`, `todo_ledger=` kwarg, `plan_context["todo_status"]`, `TodoItemSchema`/`LiveTodosView`/`renderLiveTodos`/`liveTodos`, `AI_EDITOR_CONTROLLER_MAX_ITERS` — used identically across tasks. The 5 statuses match across Python `_STATUSES`, the tool enum, the editor-client `z.enum`, and the webview `TodoItem` type. ✓
