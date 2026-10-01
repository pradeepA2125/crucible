# Sub-agents Plan 1B — Gates & Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a chat thread hold **several** pending approval gates at once, each addressable by a stable `gate_id` and optionally tagged with the sub-agent that raised it; make every command/MCP denial say truthfully *who* denied it; add the `StaleWriteError` type; and fix the `TurnEditSession` lost-update bug — all without changing anything the main agent or the user sees until Part II switches the UI to the gate list.

**Architecture:** Part I (backend) turns the single `pending_controller_gate` into a list keyed by `gate_id`, re-keys the decision futures by `gate_id`, and adds an optional `gate_id` to the decision routes (404 unknown / 409 ambiguous). It stays **backward compatible**: `/live` keeps a legacy `pending_gate` (the first gate) and a decision without `gate_id` still resolves the single pending gate of its kind, so today's frontend keeps working unchanged. Part II moves the editor-client, extension and webview to the gate list (stacked cards, agent chips, `gate_id` on every decision) and then deletes the legacy field.

**Tech Stack:** Python 3.13 + pytest (backend); TypeScript + Zod + vitest (editor-client, extension, webview). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-29-subagents-design.md` (rev 10) — this plan implements the rest of spec §16 phase 1: §4.5 (multiple gates), §4.6.4 (`ApprovalOutcome` / `denied_by`), §7.4 (the `StaleWriteError` type and `STALE_READ` code only — the write log that raises it is Phase 2), §7.5 (the `TurnEditSession` re-seed fix). Plan 1A (prompt foundations) is already merged on `feat/subagents`.

## Global Constraints

- **Main-agent behavior is unchanged by Part I.** Today the main agent never holds more than one gate, so every existing flow resolves exactly as before. `tests/test_prompt_goldens.py` (Plan 1A) must stay green on every commit.
- **`gate_id` is never a pydantic `default_factory`** — a factory mints a fresh id on every read of a stored row, so a decision route could never address a gate (the `ChatMessage.id` lesson, CLAUDE.md "Chat rewind"). `""` means "not stored yet"; `ChatThreadStore.add_controller_gate` assigns a uuid; callers that need the id before storing use `PendingGate.new(...)`. Legacy single-gate rows read back with the deterministic id `legacy-{kind}`.
- **Gate list mutations are synchronous read-modify-write with no `await`** (race-safe in single-process asyncio, the `_in_flight_*` pattern).
- **Decision route semantics** (spec §4.5): `gate_id` given → that gate, **404** if it is not pending; omitted → the single pending gate of that kind (unchanged `{"ok": false}` when there is none), **409** when there are several.
- **`denied_by` is server-internal** — never a field on a request body (`CommandDecision`/`McpToolDecision` are request bodies; a client must not be able to post `denied_by: "policy"`).
- **The `PendingGate` shape change lands in all four places in Part II** (Python model, editor-client Zod, `HttpBackendClient`'s explicit mapping, webview `types.ts`) — the footgun class CLAUDE.md warns about.
- **Test hygiene (repo rules):** never pass `-q` to pytest; never pipe pytest (redirect to a file and check `$?`); `asyncio.run`/`@pytest.mark.asyncio`, never `get_event_loop().run_until_complete`. After changing `editor-client`, run `npm run -w @crucible/editor-client build` before the extension typecheck.
- **Pre-existing failure:** `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` fails on `main` before this work. Compare failure sets; never let it hide a new failure.
- Backend commands run from `services/agentd-py` with `.venv/bin/python`; TypeScript commands from the repo root.

## Review Focus

1. **Silent field drop** — `ChatThread(pending_controller_gate=...)` is *ignored* by pydantic (unknown kwarg), so a test or caller still using the old name loses its gate without an error. Part I Task 2 greps the whole tree for the old name and requires zero hits.
2. **Two gates of the same kind at once** — the route must refuse a `gate_id`-less decision (409) rather than resolve an arbitrary one; `/review-pref` flipping to auto-accept must accept **every** pending edit gate. Pinned in Task 2.
3. **Restart orphans with several gates** — each orphaned gate recovers/clears independently, by id. Pinned in Task 2.
4. **A denial the user never saw** — a timeout must not tell the model "rejected by user". Pinned in Task 3 for commands (chat + task path + exec sessions) and MCP.
5. **Lost update on a re-touched file** — an edit to a file that changed on disk between two edits of the same turn must build on the current content. Pinned in Task 5.
6. **Request models must be module-level in `routes.py`** — the module uses `from __future__ import annotations`, so FastAPI resolves body annotations against module globals; a model imported inside `build_router` silently turns every call into a 422. Pinned by Task 2's route tests.

---

## File Structure

| File | Responsibility |
|---|---|
| `agentd/chat/models.py` | `GateKind`, `GateAgent`, `PendingGate(gate_id, agent, .new())`, `ChatThread.pending_controller_gates`, `ThreadLiveState.pending_gates`, `ChatCommandDecisionRequest`, `ChatMcpDecisionRequest` |
| `agentd/chat/storage.py` | gate-list persistence: `add_controller_gate`, `remove_controller_gate`, `clear_controller_gates`; legacy-row read |
| `agentd/chat/live_state.py` | `/live` gate list (controller gates first, then the task gate with a synthetic id) |
| `agentd/chat/controller.py` | futures keyed by `gate_id`; `_select_gate`; `GateNotFoundError`/`GateAmbiguousError`; every raise/resolve site |
| `agentd/api/routes.py` | `gate_id` on the edit/command/MCP decision routes, 404/409 mapping |
| `agentd/domain/models.py` | `ApprovalOutcome`; `PatchFailureCode.STALE_READ` |
| `agentd/tools/registry.py`, `agentd/exec_sessions/tool_source.py`, `agentd/mcp/tool_source.py`, `agentd/orchestrator/engine.py` | `ApprovalOutcome` producers/consumers; truthful denial wording |
| `agentd/chat/edit_session.py` | `StaleWriteError`; re-seed every touched file on every `apply` |
| `agentd/chat/controller_loop.py` | `STALE_READ` guidance entry |
| Part II: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`, `apps/vscode-extension/src/controller.ts`, `src/chat-panel.ts`, `src/extension.ts`, `webview-ui/src/{types.ts,hooks/useAppState.ts,inputAvailability.ts,components/LiveSlot.tsx,components/ThreadView.tsx,components/messages/gates/*.tsx}` | the gate list end to end |

---

# Part I — Backend (backward compatible)

### Task 1: Gate list model, storage and `/live` shape

**Files:**
- Modify: `agentd/chat/models.py`, `agentd/chat/storage.py`, `agentd/chat/live_state.py`
- Modify: `tests/test_controller_todo_live.py` (one silent-drop constructor)
- Test: `tests/test_controller_gate_list.py`

**Interfaces:**
- Produces:
  - `GateKind = Literal["command","step","scope","validation","mode","edit","clarify","mcp_tool"]`
  - `class GateAgent(BaseModel): id: str; label: str; name: str`
  - `class PendingGate(BaseModel): gate_id: str = ""; kind: GateKind; payload: dict; agent: GateAgent | None = None` and `PendingGate.new(kind, payload, agent=None) -> PendingGate` (mints a uuid)
  - `ChatThread.pending_controller_gates: list[PendingGate]`
  - `ThreadLiveState.pending_gates: list[PendingGate]` (+ legacy `pending_gate`, the first gate, removed in Task 8)
  - `ChatThreadStore.add_controller_gate(thread_id, gate) -> PendingGate`, `remove_controller_gate(thread_id, gate_id) -> bool`, `clear_controller_gates(thread_id, *, agent_id=None) -> None`
  - Task gates carry `gate_id = f"task:{task_id}:{kind}"`.
  - **Transitional (removed in Task 2):** `ChatThread.pending_controller_gate` read-only property (the first gate) and `ChatThreadStore.set_controller_gate(thread_id, gate | None)` (replaces the list with `[gate]` or `[]`). They keep every existing caller working inside this task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_controller_gate_list.py`:

```python
"""Multi-gate model + storage (spec §4.5). A thread holds a LIST of pending gates, each
with a stable gate_id and an optional agent tag."""
from pathlib import Path

from agentd.chat.live_state import resolve_live_state, resolve_thread_live
from agentd.chat.models import GateAgent, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import (
    CommandApprovalRequest,
    TaskExecutionState,
    TaskRecord,
    TaskStatus,
)


def _store(tmp_path: Path) -> tuple[ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    return store, store.create_thread(str(tmp_path)).thread_id


def test_new_gates_get_distinct_stable_ids_and_optional_agent() -> None:
    a = PendingGate.new("command", {"command": "ls"})
    b = PendingGate.new("edit", {}, agent=GateAgent(id="agent-1", label="impl", name="general"))
    assert a.gate_id and b.gate_id and a.gate_id != b.gate_id
    assert a.agent is None and b.agent is not None and b.agent.label == "impl"


def test_gates_are_stored_as_an_ordered_list(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    first = PendingGate.new("command", {"command": "ls"})
    second = PendingGate.new("mcp_tool", {"server": "gh", "tool": "x", "args": {}})
    store.add_controller_gate(tid, first)
    store.add_controller_gate(tid, second)
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [first.gate_id, second.gate_id]
    listed = store.list_threads(str(tmp_path))[0]
    assert [g.gate_id for g in listed.pending_controller_gates] == [first.gate_id, second.gate_id]


def test_remove_one_gate_by_id(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    keep, drop = PendingGate.new("command", {}), PendingGate.new("edit", {})
    store.add_controller_gate(tid, keep)
    store.add_controller_gate(tid, drop)
    assert store.remove_controller_gate(tid, drop.gate_id) is True
    assert store.remove_controller_gate(tid, drop.gate_id) is False
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [keep.gate_id]


def test_clear_all_or_one_agents_gates(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    agent = GateAgent(id="agent-1", label="impl", name="general")
    parent = PendingGate.new("command", {})
    child = PendingGate.new("command", {}, agent=agent)
    store.add_controller_gate(tid, parent)
    store.add_controller_gate(tid, child)
    store.clear_controller_gates(tid, agent_id="agent-1")
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [parent.gate_id]
    store.clear_controller_gates(tid)
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []


def test_a_legacy_single_gate_row_reads_as_a_one_item_list_with_a_stable_id(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    legacy = '{"kind": "edit", "payload": {"diff_entries": []}}'
    store._conn.execute("UPDATE chat_threads SET controller_gate_json = ? WHERE thread_id = ?",
                        (legacy, tid))
    store._conn.commit()
    first = store.get_thread(tid)
    second = store.get_thread(tid)
    assert first is not None and second is not None
    assert [g.gate_id for g in first.pending_controller_gates] == ["legacy-edit"]
    assert first.pending_controller_gates[0].gate_id == second.pending_controller_gates[0].gate_id
    assert first.pending_controller_gates[0].kind == "edit"


def test_a_gate_stored_without_an_id_gets_one(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    stored = store.add_controller_gate(tid, PendingGate(kind="mode", payload={}))
    assert stored.gate_id
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates[0].gate_id == stored.gate_id


def _no_task(task_id: str) -> TaskRecord:
    raise KeyError(task_id)


def _command_gated_task() -> TaskRecord:
    es = TaskExecutionState()
    es.pending_command_request = CommandApprovalRequest(
        decision_id="d", command="ls", args=[], cwd="", step_id="s1")
    return TaskRecord(task_id="task-1", goal="g", workspace_path="/w",
                      status=TaskStatus.AWAITING_COMMAND_DECISION, execution_state=es)


def test_live_state_carries_the_gate_list_and_the_legacy_first_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    first, second = PendingGate.new("command", {}), PendingGate.new("mcp_tool", {})
    store.add_controller_gate(tid, first)
    store.add_controller_gate(tid, second)
    live = resolve_thread_live(store.get_thread(tid), None, _no_task)
    assert [g.gate_id for g in live.pending_gates] == [first.gate_id, second.gate_id]
    assert live.pending_gate is not None and live.pending_gate.gate_id == first.gate_id
    dumped = live.model_dump(mode="json")
    assert dumped["pending_gates"][0]["gate_id"] == first.gate_id
    assert dumped["pending_gate"]["gate_id"] == first.gate_id


def test_no_gates_means_an_empty_list_and_no_legacy_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    live = resolve_thread_live(store.get_thread(tid), None, _no_task)
    assert live.pending_gates == [] and live.pending_gate is None


def test_task_gates_carry_a_synthetic_task_id() -> None:
    task = _command_gated_task()
    live = resolve_live_state(task.task_id, lambda _tid: task)
    assert [g.gate_id for g in live.pending_gates] == ["task:task-1:command"]
    assert live.pending_gate is not None and live.pending_gate.gate_id == "task:task-1:command"


def test_controller_gates_come_first_then_the_task_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    ctrl = store.add_controller_gate(tid, PendingGate.new("mode", {}))
    task = _command_gated_task()
    live = resolve_thread_live(store.get_thread(tid), task.task_id, lambda _tid: task)
    assert [g.gate_id for g in live.pending_gates] == [ctrl.gate_id, "task:task-1:command"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_controller_gate_list.py > /tmp/b1.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/b1.txt`
Expected: `exit=2`, `ImportError` (`GateAgent` does not exist).

- [ ] **Step 3: The model**

In `agentd/chat/models.py`, add `from uuid import uuid4` after `from typing import Any, Literal`. Replace the `PendingGate` class with:

```python
GateKind = Literal["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool"]


class GateAgent(BaseModel):
    """Which sub-agent raised a gate (spec §4.5). Absent on the main agent's gates."""
    id: str
    label: str
    name: str


class PendingGate(BaseModel):
    """One gate a thread is waiting on. A thread may hold several (spec §4.5).

    command/step/scope/validation are derived from the active *task* status
    (see live_state._GATE_FIELD) and carry a synthetic id "task:{task_id}:{kind}".
    mode/edit/clarify/mcp_tool/command are *controller* gates — the controller has no
    task, so they live on the thread (pending_controller_gates).

    gate_id is never a default_factory: that would mint a fresh id on every read of a
    stored row (the ChatMessage.id lesson — see CLAUDE.md "Chat rewind"), so decision
    routes could never address a gate. "" means "not stored yet": the store assigns a
    uuid on add_controller_gate. Callers that need the id before storing (to key a
    decision future) mint it with PendingGate.new().
    """
    gate_id: str = ""
    kind: GateKind
    payload: dict[str, Any] = Field(default_factory=dict)
    agent: GateAgent | None = None

    @classmethod
    def new(cls, kind: GateKind, payload: dict[str, Any],
            agent: GateAgent | None = None) -> PendingGate:
        return cls(gate_id=uuid4().hex, kind=kind, payload=payload, agent=agent)
```

In `ChatThread`, replace the `pending_controller_gate: PendingGate | None = None` field and its two comment lines with:

```python
    # Controller-turn gates. The controller has no task, so its gates live here (durable,
    # surfaced by /live via resolve_thread_live). A list: sub-agents can raise gates
    # concurrently with each other (spec §4.5).
    pending_controller_gates: list[PendingGate] = Field(default_factory=list)

    @property
    def pending_controller_gate(self) -> PendingGate | None:
        """TRANSITIONAL (Plan 1B Part I, removed in Task 2): the first pending gate."""
        return self.pending_controller_gates[0] if self.pending_controller_gates else None
```

In `ThreadLiveState`, replace `pending_gate: PendingGate | None = None` with:

```python
    # Every pending gate, controller gates first (spec §4.5).
    pending_gates: list[PendingGate] = Field(default_factory=list)
    # LEGACY (Plan 1B Part I only): the first of pending_gates, kept so the pre-1B
    # frontend keeps rendering single gates. Part II moves the frontend to pending_gates
    # and deletes this field.
    pending_gate: PendingGate | None = None
```

- [ ] **Step 4: Storage**

In `agentd/chat/storage.py` (it imports the `uuid` *module* — call `uuid.uuid4()`), replace `_gate_from_row` with:

```python
    @staticmethod
    def _gates_from_row(row: sqlite3.Row) -> list[PendingGate]:
        """The column holds a JSON list of gates. A row written before multi-gate holds a
        single gate object: read it as a one-item list with the deterministic id
        "legacy-{kind}", so repeated reads (and a decision route) agree on its id."""
        raw = row["controller_gate_json"]
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data.setdefault("gate_id", f"legacy-{data.get('kind', 'gate')}")
            return [PendingGate.model_validate(data)]
        return [PendingGate.model_validate(g) for g in data]

    def _write_gates(self, thread_id: str, gates: list[PendingGate]) -> None:
        raw = json.dumps([g.model_dump(mode="json") for g in gates]) if gates else None
        self._conn.execute(
            "UPDATE chat_threads SET controller_gate_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    def _read_gates(self, thread_id: str) -> list[PendingGate]:
        row = self._conn.execute(
            "SELECT controller_gate_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return self._gates_from_row(row) if row is not None else []
```

In both `list_threads` and `get_thread`, change `pending_controller_gate=self._gate_from_row(row),` to `pending_controller_gates=self._gates_from_row(row),`. Replace `set_controller_gate` with:

```python
    # Gate list mutations (spec §4.5). Each is a synchronous read-modify-write with no
    # await, so it is race-safe in single-process asyncio: two concurrent raise/resolve
    # sites can never interleave inside one of these.
    def add_controller_gate(self, thread_id: str, gate: PendingGate) -> PendingGate:
        """Append a gate; assigns a uuid when gate_id is "" (not yet stored). Returns
        the stored gate (with its id)."""
        stored = gate if gate.gate_id else gate.model_copy(update={"gate_id": uuid.uuid4().hex})
        self._write_gates(thread_id, [*self._read_gates(thread_id), stored])
        return stored

    def remove_controller_gate(self, thread_id: str, gate_id: str) -> bool:
        """Remove one gate by id. False when no such gate is pending."""
        gates = self._read_gates(thread_id)
        kept = [g for g in gates if g.gate_id != gate_id]
        if len(kept) == len(gates):
            return False
        self._write_gates(thread_id, kept)
        return True

    def clear_controller_gates(self, thread_id: str, *, agent_id: str | None = None) -> None:
        """Clear every gate, or only the gates one sub-agent raised."""
        if agent_id is None:
            self._write_gates(thread_id, [])
            return
        kept = [g for g in self._read_gates(thread_id)
                if g.agent is None or g.agent.id != agent_id]
        self._write_gates(thread_id, kept)

    def set_controller_gate(self, thread_id: str, gate: PendingGate | None) -> None:
        """TRANSITIONAL (Plan 1B Part I, removed in Task 2): replace the whole list with
        [gate], or clear it with None."""
        if gate is None:
            self._write_gates(thread_id, [])
            return
        stored = gate if gate.gate_id else gate.model_copy(update={"gate_id": uuid.uuid4().hex})
        self._write_gates(thread_id, [stored])
```

(`restore_controller_state` already sets the column to `NULL`, which now reads as `[]` — no change.)

- [ ] **Step 5: `/live`**

In `agentd/chat/live_state.py`, in `resolve_live_state` replace `gate = PendingGate(kind=kind, payload=payload)` with:

```python
            # Synthetic, deterministic id: task gates are resolved by the task routes,
            # and the id lets the frontend key and route them like controller gates.
            gate = PendingGate(gate_id=f"task:{task.task_id}:{kind}", kind=kind,
                               payload=payload)
```

and add `pending_gates=[gate] if gate is not None else [],` directly above `pending_gate=gate,` in its `ThreadLiveState(...)` return. In `resolve_thread_live`, replace the body after the `todos = ...` line with:

```python
    base = resolve_live_state(active_task_id, get_task)
    if thread is not None and thread.pending_controller_gates:
        # Controller gates own the live slot (the controller has no task). A task-derived
        # gate, when one exists, rides along after them (spec §4.5).
        gates = [*thread.pending_controller_gates, *base.pending_gates]
        return ThreadLiveState(
            active_task_id=active_task_id,
            pending_gates=gates,
            pending_gate=gates[0],
            todos=todos,
        )
    base.todos = todos  # ThreadLiveState is a mutable pydantic model; set after build
    return base
```

- [ ] **Step 6: Fix the one constructor that would silently drop its gate**

`ChatThread(pending_controller_gate=...)` is **ignored** by pydantic (unknown kwarg — no error). In `tests/test_controller_todo_live.py`, change

`        pending_controller_gate=PendingGate(kind="mode", payload={"x": 1}),`

to

`        pending_controller_gates=[PendingGate(gate_id="g1", kind="mode", payload={"x": 1})],`

Then run `grep -rnI "pending_controller_gate=" agentd tests` — Expected: no output.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_controller_gate_list.py tests/test_controller_todo_live.py tests/test_thread_live_state.py tests/test_prompt_goldens.py > /tmp/b1.txt 2>&1; echo exit=$?; tail -1 /tmp/b1.txt`
Expected: `exit=0`.

Run: `.venv/bin/python -m pytest tests/ -k "gate or controller or live or mode or clarify or edit or command or mcp or rewind or skills or chat" --color=no > /tmp/b1b.txt 2>&1; echo exit=$?; grep '^FAILED' /tmp/b1b.txt`
Expected: the only `FAILED` line is the pre-existing `test_command_only_step` one.

- [ ] **Step 8: Commit**

```bash
git add agentd/chat/models.py agentd/chat/storage.py agentd/chat/live_state.py tests/test_controller_gate_list.py tests/test_controller_todo_live.py
git commit -m "feat(chat): store controller gates as a list with stable gate ids"
```

---

### Task 2: Per-gate decision futures, `gate_id` routes, remove the shims

**Files:**
- Modify: `agentd/chat/models.py` (gate errors + two request bodies), `agentd/chat/controller.py`, `agentd/api/routes.py`, `agentd/chat/storage.py` (drop `set_controller_gate`), `agentd/chat/live_state.py` (docstring only)
- Create: `tests/gate_helpers.py`
- Modify (migration): the 16 test files listed in Step 6, and `tests/test_mcp_flag_wiring.py` (stub signature)
- Test: `tests/test_multi_gate_decisions.py`

**Interfaces:**
- Consumes: Task 1's list API.
- Produces:
  - `agentd.chat.models.GateNotFoundError(LookupError)` → HTTP 404; `GateAmbiguousError(ValueError)` → HTTP 409.
  - `ChatCommandDecisionRequest(CommandDecision)` and `ChatMcpDecisionRequest(McpToolDecision)`, each adding `gate_id: str | None = None` (request bodies only — the decision objects handed to the controller stay plain `CommandDecision`/`McpToolDecision`).
  - `ChatController.resolve_edit(thread_id, decision, gate_id=None)`, `resolve_command(thread_id, decision, gate_id=None)`, `resolve_mcp(thread_id, decision, gate_id=None)`.
  - `ChatController._pending_edit/_pending_command/_pending_mcp` keyed by **gate_id** (were thread_id).
  - `tests/gate_helpers.first_gate(thread) -> PendingGate | None`.
- Removes: `ChatThread.pending_controller_gate` (property), `ChatThreadStore.set_controller_gate`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_multi_gate_decisions.py`:

```python
"""Several gates pending at once (spec §4.5): each decision future is keyed by gate_id,
routes address a gate by gate_id (404 unknown / 409 ambiguous when omitted)."""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import GateAmbiguousError, GateNotFoundError, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.chat.turn_control import ChatTurnControl
from agentd.domain.models import CommandDecision, McpToolDecision, ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _controller(tmp_path: Path, store: ChatThreadStore) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK)


async def _two_command_gates(ctrl: ChatController, store: ChatThreadStore, tid: str):
    """Raise two concurrent command gates on one thread; return (tasks, gates)."""
    first = asyncio.create_task(ctrl._command_approval_cb(tid, "c1", "ls", ["a"], ""))
    second = asyncio.create_task(ctrl._command_approval_cb(tid, "c1", "ls", ["b"], ""))
    for _ in range(100):
        await asyncio.sleep(0)
        thread = store.get_thread(tid)
        if thread is not None and len(thread.pending_controller_gates) == 2:
            break
    thread = store.get_thread(tid)
    assert thread is not None and len(thread.pending_controller_gates) == 2
    return (first, second), thread.pending_controller_gates


@pytest.mark.asyncio
async def test_two_command_gates_resolve_independently_by_id(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    (first, second), gates = await _two_command_gates(ctrl, store, tid)
    assert gates[0].payload["args"] == ["a"] and gates[1].payload["args"] == ["b"]

    assert await ctrl.resolve_command(tid, CommandDecision(approve=False),
                                      gate_id=gates[1].gate_id) is True
    assert (await second).approve is False
    assert not first.done()
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [gates[0].gate_id]

    assert await ctrl.resolve_command(tid, CommandDecision(approve=True)) is True
    assert (await first).approve is True
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []


@pytest.mark.asyncio
async def test_omitted_gate_id_with_two_of_a_kind_is_ambiguous(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    (first, second), _gates = await _two_command_gates(ctrl, store, tid)
    with pytest.raises(GateAmbiguousError):
        await ctrl.resolve_command(tid, CommandDecision(approve=True))
    with pytest.raises(GateNotFoundError):
        await ctrl.resolve_command(tid, CommandDecision(approve=True), gate_id="nope")
    first.cancel()
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_review_pref_on_accepts_every_pending_edit_gate(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    ctrl._turn_controls[tid] = ChatTurnControl(auto_accept_edits=False)
    loop = asyncio.get_running_loop()
    futures = []
    for _ in range(2):
        gate = store.add_controller_gate(tid, PendingGate.new("edit", {}))
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        ctrl._pending_edit[gate.gate_id] = fut
        futures.append(fut)
    assert await ctrl.set_review_pref(tid, auto_accept=True) is True
    assert all(f.done() and f.result()["decision"] == "accept" for f in futures)


@pytest.mark.asyncio
async def test_each_restart_orphan_clears_only_itself(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    a = store.add_controller_gate(tid, PendingGate.new("mcp_tool", {"server": "s", "tool": "t"}))
    b = store.add_controller_gate(tid, PendingGate.new("mcp_tool", {"server": "s", "tool": "u"}))
    assert await ctrl.resolve_mcp(tid, McpToolDecision(approve=True), gate_id=a.gate_id) is False
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [b.gate_id]


def _app(tmp_path: Path, ctrl: ChatController) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    return app


@pytest.mark.asyncio
async def test_routes_map_unknown_gate_to_404_and_ambiguous_to_409(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    (first, second), gates = await _two_command_gates(ctrl, store, tid)
    async with AsyncClient(transport=ASGITransport(app=_app(tmp_path, ctrl)),
                           base_url="http://t") as client:
        base = f"/v1/chat/threads/{tid}"
        ambiguous = await client.post(f"{base}/command-decision", json={"approve": True})
        unknown = await client.post(f"{base}/command-decision",
                                    json={"approve": True, "gate_id": "nope"})
        unknown_mcp = await client.post(f"{base}/mcp-decision",
                                        json={"approve": True, "gate_id": "nope"})
        unknown_edit = await client.post(f"{base}/edit-decision",
                                         json={"decision": "accept", "gate_id": "nope"})
        chosen = await client.post(f"{base}/command-decision",
                                   json={"approve": True, "gate_id": gates[0].gate_id})
    assert ambiguous.status_code == 409
    assert unknown.status_code == 404
    assert unknown_mcp.status_code == 404
    assert unknown_edit.status_code == 404
    assert chosen.status_code == 200 and chosen.json() == {"ok": True}
    decision = await first
    assert decision.approve is True and type(decision) is CommandDecision
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)


@pytest.mark.asyncio
async def test_edit_route_does_not_forward_gate_id_into_the_decision(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    gate = store.add_controller_gate(tid, PendingGate.new("edit", {}))
    fut: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    ctrl._pending_edit[gate.gate_id] = fut
    async with AsyncClient(transport=ASGITransport(app=_app(tmp_path, ctrl)),
                           base_url="http://t") as client:
        resp = await client.post(f"/v1/chat/threads/{tid}/edit-decision",
                                 json={"decision": "accept", "gate_id": gate.gate_id})
    assert resp.json() == {"ok": True}
    assert fut.result() == {"decision": "accept"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_multi_gate_decisions.py > /tmp/b2.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/b2.txt`
Expected: `exit=2`, `ImportError` (`GateAmbiguousError`).

- [ ] **Step 3: Errors + request bodies in `agentd/chat/models.py`**

Add below `PendingGate`:

```python
class GateNotFoundError(LookupError):
    """A decision named a gate_id that is not pending on the thread (route → 404). Benign
    by design: a card click can race an auto-accept or a stop that just removed it."""


class GateAmbiguousError(ValueError):
    """A decision omitted gate_id while several gates of that kind are pending (→ 409)."""
```

Add `CommandDecision` and `McpToolDecision` to the existing `from agentd.domain.models import (...)` block of `agentd/chat/models.py` (keep it sorted), and append at the end of the file:

```python
class ChatCommandDecisionRequest(CommandDecision):
    """POST /chat/threads/{id}/command-decision body. gate_id is optional: omitted means
    "the single pending command gate" (pre-multi-gate clients). Request-only — the route
    hands the controller a plain CommandDecision."""
    gate_id: str | None = None


class ChatMcpDecisionRequest(McpToolDecision):
    """POST /chat/threads/{id}/mcp-decision body; gate_id as ChatCommandDecisionRequest."""
    gate_id: str | None = None
```

- [ ] **Step 4: Controller**

In `agentd/chat/controller.py`:

1. Import: `from agentd.chat.models import ChatMessage, GateAmbiguousError, GateNotFoundError, PendingGate`.
2. Replace the two comment blocks above `self._pending_edit` / `self._pending_command` with one:
   ```python
           # Held-open decision futures, keyed by gate_id (spec §4.5 — a thread can hold several
           # gates at once). resolve_edit/resolve_command/resolve_mcp fire them; each raise
           # site pops its own entry and removes its own gate in its finally.
   ```
   and change the `_pending_mcp` comment's first line to `# gate_id → future for an in-flight mcp_tool gate; same lifecycle as`.
3. In the `_active_turns` comment: `pending_controller_gate survive` → `pending_controller_gates survive`; in `_present_mode_choice`'s comment: `durable via pending_controller_gate.` → `durable via pending_controller_gates.`
4. `handle_message`: `self._store.set_controller_gate(thread_id, None)` → `self._store.clear_controller_gates(thread_id)`.
5. `_present_mode_choice` / `_present_clarify_choice`: the two-line call
   ```python
        self._store.set_controller_gate(
            thread_id, PendingGate(kind="mode", payload=outcome.payload or {}))
   ```
   becomes
   ```python
        self._store.add_controller_gate(
            thread_id, PendingGate.new("mode", outcome.payload or {}))
   ```
   (the same for `"clarify"`).
6. `_edit_decision_cb`: `self._store.set_controller_gate(thread_id, PendingGate(kind="edit", payload={` → `gate = self._store.add_controller_gate(thread_id, PendingGate.new("edit", {`; `self._pending_edit[thread_id] = fut` → `self._pending_edit[gate.gate_id] = fut`; in `finally`: `self._pending_edit.pop(gate.gate_id, None)` and `self._store.remove_controller_gate(thread_id, gate.gate_id)`.
7. `_command_approval_cb` and `_mcp_approval_cb`: the same three changes (`gate = self._store.add_controller_gate(thread_id, PendingGate.new("command", {...}))` / `"mcp_tool"`; the future keyed by `gate.gate_id`; the `finally` pops `gate.gate_id` and calls `remove_controller_gate(thread_id, gate.gate_id)`).
8. `set_review_pref` — replace the `if auto_accept:` block with:
   ```python
           if auto_accept:
               thread = self._store.get_thread(thread_id)
               for gate in (thread.pending_controller_gates if thread is not None else []):
                   if gate.kind != "edit":
                       continue
                   future = self._pending_edit.get(gate.gate_id)
                   if future is not None and not future.done():
                       future.set_result({
                           "decision": "accept", "reason": "auto-accept turned on"})
   ```
9. Add, directly above `resolve_edit`:
   ```python
       def _select_gate(
           self, thread_id: str, kind: str, gate_id: str | None,
       ) -> PendingGate | None:
           """The pending gate a decision targets (spec §4.5 route semantics).

           gate_id given → that gate, else GateNotFoundError (→ 404). gate_id omitted → the
           single pending gate of that kind (keeps pre-multi-gate clients working), None when
           there is none, GateAmbiguousError (→ 409) when there are several."""
           thread = self._store.get_thread(thread_id)
           gates = [g for g in (thread.pending_controller_gates if thread is not None else [])
                    if g.kind == kind]
           if gate_id is not None:
               match = next((g for g in gates if g.gate_id == gate_id), None)
               if match is None:
                   raise GateNotFoundError(f"no pending {kind} gate {gate_id!r} on {thread_id}")
               return match
           if len(gates) > 1:
               raise GateAmbiguousError(
                   f"{len(gates)} {kind} gates are pending on {thread_id}; pass gate_id")
           return gates[0] if gates else None
   ```
10. `resolve_edit` — signature `(self, thread_id: str, decision: dict[str, object], gate_id: str | None = None) -> bool`. Replace its first line `fut = self._pending_edit.get(thread_id)` with:
    ```python
            gate = self._select_gate(thread_id, "edit", gate_id)
            if gate is None:
                return False
            fut = self._pending_edit.get(gate.gate_id)
    ```
    Inside the orphan branch delete the four lines that re-read the thread (`thread = ...`, `gate = thread.pending_controller_gate ...`, `if gate is None or gate.kind != "edit":`, `return False`) and change `self._store.set_controller_gate(thread_id, None)` to `self._store.remove_controller_gate(thread_id, gate.gate_id)`. In its docstring, `` (`thread_id not in _pending_edit`) `` → `(no future for its gate_id)`.
11. `resolve_command` / `resolve_mcp` — add `gate_id: str | None = None`; replace each body with (for MCP: `"mcp_tool"` and `self._pending_mcp`):
    ```python
            gate = self._select_gate(thread_id, "command", gate_id)
            if gate is None:
                return False
            fut = self._pending_command.get(gate.gate_id)
            if fut is None or fut.done():
                # Restart orphan: the gate outlived its waiter. Clear it + breadcrumb.
                self._store.remove_controller_gate(thread_id, gate.gate_id)
                self._write_breadcrumb(
                    thread_id, f"chat:{thread_id}",
                    "Previous turn ended — please re-send your request.")
                return False
            fut.set_result(decision)
            return True
    ```
12. `resolve_mode` / `resolve_clarify` — replace the three-line read (`thread = ...`, `gate = thread.pending_controller_gate ...`, `if gate is None or gate.kind != "mode":`) with `gate = self._select_gate(thread_id, "mode", None)` / `if gate is None:` (`"clarify"` likewise), and their `self._store.set_controller_gate(thread_id, None)` with `self._store.remove_controller_gate(thread_id, gate.gate_id)`. (Mode/clarify gates are main-agent-only — at most one per thread — so these routes take no `gate_id`.)

Then `grep -n "set_controller_gate\|pending_controller_gate\b" agentd/chat/controller.py` — Expected: no output.

- [ ] **Step 5: Routes**

In `agentd/api/routes.py`, add a **module-level** import next to the other `agentd.*` imports (sorted before `agentd.domain.models`):

```python
from agentd.chat.models import (
    ChatCommandDecisionRequest,
    ChatMcpDecisionRequest,
    GateAmbiguousError,
    GateNotFoundError,
)
```

It must be module-level, NOT inside `build_router` next to the lazy `ChatAgent` import: `routes.py` has `from __future__ import annotations`, so FastAPI resolves the `request: ChatCommandDecisionRequest` annotation against the module globals — a name imported inside the function is invisible there and the route answers every request with 422. (`agentd.chat.models` imports only `agentd.domain.models`, so there is no cycle.)

Inside the chat-route section of `build_router` (next to the existing lazy `from agentd.chat.agent import ChatAgent as _ChatAgent`), add:

```python
        def _gate_http_error(exc: Exception) -> HTTPException:
            # 404: the named gate is not pending (benign — a click raced an auto-accept or
            # a stop). 409: gate_id omitted while several gates of that kind are pending.
            status = 404 if isinstance(exc, GateNotFoundError) else 409
            return HTTPException(status_code=status, detail=str(exc))
```

Replace the three decision routes with:

```python
        @router.post("/chat/threads/{thread_id}/edit-decision")
        async def post_edit_decision(thread_id: str, request: dict) -> dict:
            # Resolves a held-open per-edit gate. The continuation surfaces on the
            # ALREADY-open message SSE stream (the loop resumes), so this is a plain
            # JSON ack — not a new stream. Mirrors /step-decision (future.set_result).
            # gate_id addresses one of several pending gates (spec §4.5); it is routing,
            # not part of the decision, so it is stripped before the dict reaches the loop.
            raw_gate_id = request.pop("gate_id", None)
            gate_id = raw_gate_id if isinstance(raw_gate_id, str) else None
            try:
                ok = await _chat_agent.resolve_edit(  # type: ignore[attr-defined]
                    thread_id, request, gate_id=gate_id)
            except (GateNotFoundError, GateAmbiguousError) as exc:
                raise _gate_http_error(exc) from exc
            return {"ok": ok}
```

```python
        @router.post("/chat/threads/{thread_id}/command-decision")
        async def post_chat_command_decision(
            thread_id: str, request: ChatCommandDecisionRequest,
        ) -> dict:
            # Resolves a held-open run_command gate. The continuation surfaces on the
            # already-open message SSE stream (the loop resumes), so this is a plain
            # JSON ack — mirrors /edit-decision (future.set_result).
            resolve = getattr(_chat_agent, "resolve_command", None)
            if resolve is None:
                return {"ok": False}
            decision = CommandDecision(**request.model_dump(exclude={"gate_id"}))
            try:
                ok = await resolve(thread_id, decision, gate_id=request.gate_id)  # type: ignore[misc]
            except (GateNotFoundError, GateAmbiguousError) as exc:
                raise _gate_http_error(exc) from exc
            return {"ok": ok}

        @router.post("/chat/threads/{thread_id}/mcp-decision")
        async def post_chat_mcp_decision(
            thread_id: str, request: ChatMcpDecisionRequest,
        ) -> dict:
            # Resolves a held-open mcp_tool gate — plain JSON ack, mirrors /command-decision.
            resolve = getattr(_chat_agent, "resolve_mcp", None)
            if resolve is None:
                return {"ok": False}
            decision = McpToolDecision(**request.model_dump(exclude={"gate_id"}))
            try:
                ok = await resolve(thread_id, decision, gate_id=request.gate_id)  # type: ignore[misc]
            except (GateNotFoundError, GateAmbiguousError) as exc:
                raise _gate_http_error(exc) from exc
            return {"ok": ok}
```

(`CommandDecision` and `McpToolDecision` are already imported at the top of `routes.py`. If `ruff` reports a line over 100 chars on the `ok = await resolve(...)  # type: ignore[misc]` lines, wrap the call arguments onto the next line.)

In `tests/test_mcp_flag_wiring.py`, change the stub to accept the new keyword:

```python
    async def resolve_mcp(self, thread_id, decision, gate_id=None):
        self.calls.append((thread_id, decision))
        return True
```

- [ ] **Step 6: Remove the shims and migrate the tests**

Delete the `pending_controller_gate` property from `ChatThread` and `set_controller_gate` from `ChatThreadStore`. In `agentd/chat/live_state.py`'s `resolve_thread_live` docstring, `clears in place via set_controller_gate` → `clears in place via remove_controller_gate`; in `agentd/chat/storage.py`'s `set_controller_history` docstring, `Mirrors\n        set_controller_gate: an in-place durable update` → `Mirrors\n        set_controller_seed: an in-place durable update` (the method it named no longer exists).

Create `tests/gate_helpers.py`:

```python
"""Test helper: the first pending controller gate (what pre-multi-gate tests asserted)."""
from agentd.chat.models import ChatThread, PendingGate


def first_gate(thread: ChatThread | None) -> PendingGate | None:
    if thread is None or not thread.pending_controller_gates:
        return None
    return thread.pending_controller_gates[0]
```

Run this one-shot migration from `services/agentd-py` (it rewrites `<expr>.pending_controller_gate` → `first_gate(<expr>)`, `set_controller_gate(x, None)` → `clear_controller_gates(x)`, `set_controller_gate(` → `add_controller_gate(`, and adds the import):

```bash
.venv/bin/python - <<'PY'
import ast
import re
from pathlib import Path

ATTR = re.compile(r"((?:\w+\.)*\w+(?:\([\w.]*\))?)\.pending_controller_gate\b(?!s)")
CLEAR = re.compile(r"set_controller_gate\(([\w.]+), None\)")
IMPORT = "from tests.gate_helpers import first_gate\n"
for path in sorted(Path("tests").glob("test_*.py")):
    src = path.read_text()
    out = CLEAR.sub(r"clear_controller_gates(\1)", src)
    out = out.replace("set_controller_gate(", "add_controller_gate(")
    out, n = ATTR.subn(r"first_gate(\1)", out)
    if n and IMPORT not in out:
        # Insert after the last top-level import statement (end_lineno covers the
        # closing paren of a multi-line `from x import (...)`).
        tree = ast.parse(out)
        end = max(node.end_lineno for node in tree.body
                  if isinstance(node, (ast.Import, ast.ImportFrom)))
        lines = out.splitlines(keepends=True)
        lines.insert(end, IMPORT)
        out = "".join(lines)
    if out != src:
        path.write_text(out)
        print("migrated", path)
PY
```

Expected: it prints these 16 files — `test_chat_controller_qa`, `test_controller_clarify_gate`, `test_controller_command_gate`, `test_controller_durable_edit`, `test_controller_durable_turn`, `test_controller_edit_clarify`, `test_controller_live_gate`, `test_controller_mcp_gate`, `test_controller_mode_decision_guard`, `test_controller_rate_limit_exhaustion_message`, `test_controller_transcript_order_gates`, `test_edit_gate_controller`, `test_live_edit_review_pref`, `test_mode_decision`, `test_rewind_scenarios`, `test_skills_thread_persistence`.

Then fix the four sites that key a future by thread id (the script can't know the gate id):

- `tests/test_live_edit_review_pref.py`, the auto-accept test:
  ```python
      gate = store.add_controller_gate(thread.thread_id, PendingGate(kind="edit", payload={}))
      future: asyncio.Future[dict[str, object]] = asyncio.get_event_loop().create_future()
      controller._pending_edit[gate.gate_id] = future
  ```
- `tests/test_live_edit_review_pref.py`, `test_flipping_to_review_leaves_a_pending_gate_alone`: add `gate = store.add_controller_gate(thread.thread_id, PendingGate(kind="edit", payload={}))` above the future and key it `controller._pending_edit[gate.gate_id] = future`.
- `tests/test_controller_durable_edit.py` (two places): `assert thread.thread_id not in ctrl._pending_edit` → `assert ctrl._pending_edit == {}`.

Check the migration left nothing behind and the new import sits in the import block:

Run: `grep -rnIE "pending_controller_gate\b|set_controller_gate|_pending_(edit|command|mcp)\[(th|thread)\.thread_id\]|not in ctrl\._pending_edit" agentd tests`
Expected: no output.

Run: `.venv/bin/ruff check --select I,F401,E402 tests/gate_helpers.py $(git diff --name-only -- tests)`
Expected: exactly one finding — the **pre-existing** `I001` in `tests/test_controller_durable_edit.py` (its import block was already unsorted on `main`; do not `--fix` it here). Any other finding means the migration misplaced an import.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_multi_gate_decisions.py tests/test_controller_gate_list.py tests/test_mcp_flag_wiring.py > /tmp/b2.txt 2>&1; echo exit=$?; tail -1 /tmp/b2.txt`
Expected: `exit=0`.

Run: `.venv/bin/python -m pytest tests/ --color=no > /tmp/b2full.txt 2>&1; echo exit=$?; grep '^FAILED' /tmp/b2full.txt; tail -1 /tmp/b2full.txt`
Expected: `exit=1` with the pre-existing `test_command_only_step` failure as the ONLY `FAILED` line.

- [ ] **Step 8: Commit**

```bash
git add agentd/chat/models.py agentd/chat/controller.py agentd/chat/storage.py agentd/chat/live_state.py agentd/api/routes.py tests/
git commit -m "feat(chat): key gate decisions by gate_id with 404/409 route semantics"
```

---

### Task 3: `ApprovalOutcome` — truthful command/MCP denials

**Files:**
- Modify: `agentd/domain/models.py` (`ApprovalOutcome`)
- Create: `agentd/tools/approvals.py` (callback aliases + `denial_text`)
- Modify: `agentd/orchestrator/engine.py` (`_build_command_approval_callback`), `agentd/chat/controller.py` (`_command_approval_cb`, `_mcp_approval_cb`), `agentd/tools/registry.py` (run_command), `agentd/exec_sessions/tool_source.py` (`_start`), `agentd/mcp/tool_source.py` (`execute`)
- Modify (fakes/assertions): `tests/test_tools_registry.py`, `tests/test_exec_sessions_tool_source.py`, `tests/test_exec_sessions_wiring.py`, `tests/test_mcp_tool_source.py`, `tests/test_child_tool_texts.py`, `tests/prompt_goldens.py`, `tests/test_controller_command_gate.py`, `tests/test_command_gate_engine.py`, `tests/test_controller_mcp_gate.py`, `tests/test_multi_gate_decisions.py`
- Test: `tests/test_approval_outcome.py`

**Interfaces:**
- Produces:
  - `agentd.domain.models.DeniedBy = Literal["user", "policy", "timeout"]`
  - `class ApprovalOutcome(BaseModel): approved: bool; denied_by: DeniedBy | None = None; decision: CommandDecision | None = None` with `ApprovalOutcome.from_command(decision, *, denied_by="user")`, `ApprovalOutcome.allow()`, `ApprovalOutcome.deny(by)`. **Server-internal — never a request body.**
  - `agentd.tools.approvals.CommandApprovalCallback = Callable[[str, list[str], str], Awaitable[ApprovalOutcome]]`, `McpApprovalCallback = Callable[[str, str, dict[str, object]], Awaitable[ApprovalOutcome]]`, `denial_text(outcome, *, subject, user_text) -> str`.
- Changes: every command-approval callback (engine task path, chat `_command_approval_cb`) and the MCP callback (`_mcp_approval_cb`) now return `ApprovalOutcome`; every consumer (`ToolRegistry` run_command, `ExecSessionToolSource._start`, `McpToolSource.execute`) reads `.approved` (typed — no `getattr`, spec §4.5 "silent-denial risk").
- Wording (spec §4.6.4): `denied_by="user"` keeps today's strings **byte-identical**; `"timeout"` → `No decision arrived in time; {subject} was not run.`; `"policy"` → `{Subject} is not permitted for this agent (no remembered rule allows it); nobody was asked. Work without it or note the need in your report.` Nothing in Part I produces `"policy"` (the child `dontAsk` auto-deny is Phase 2); the wording lands now so every consumer handles every value.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_approval_outcome.py`:

```python
"""ApprovalOutcome (spec §4.6.4): who denied a command/MCP call, worded truthfully."""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ApprovalOutcome, CommandDecision, ShellPolicy
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource
from agentd.mcp.tool_source import McpToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.tools.approvals import denial_text
from agentd.tools.registry import ToolRegistry
from agentd.workspace.shadow import ShadowWorkspaceManager

_POLICY_TAIL = ("is not permitted for this agent (no remembered rule allows it); nobody was "
                "asked. Work without it or note the need in your report.")


def test_from_command_maps_approval_and_denier() -> None:
    ok = ApprovalOutcome.from_command(CommandDecision(approve=True))
    assert ok.approved is True and ok.denied_by is None and ok.decision is not None
    no = ApprovalOutcome.from_command(CommandDecision(approve=False), denied_by="timeout")
    assert no.approved is False and no.denied_by == "timeout"
    assert ApprovalOutcome.from_command(CommandDecision(approve=False)).denied_by == "user"
    assert ApprovalOutcome.allow().approved is True
    assert ApprovalOutcome.deny("policy").denied_by == "policy"


def test_denial_text_words_each_denier() -> None:
    user = denial_text(ApprovalOutcome.deny("user"), subject="command `ls`", user_text="U")
    timeout = denial_text(ApprovalOutcome.deny("timeout"), subject="command `ls`", user_text="U")
    policy = denial_text(ApprovalOutcome.deny("policy"), subject="command `ls`", user_text="U")
    assert user == "U"
    assert timeout == "No decision arrived in time; command `ls` was not run."
    assert policy == f"Command `ls` {_POLICY_TAIL}"


def _registry(tmp_path: Path, outcome: ApprovalOutcome) -> ToolRegistry:
    async def cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        return outcome
    return ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path,
                        command_approval_callback=cb)


@pytest.mark.asyncio
async def test_run_command_user_rejection_wording_is_unchanged(tmp_path: Path) -> None:
    out = await _registry(tmp_path, ApprovalOutcome.deny("user")).execute(
        "run_command", {"command": "pytest", "args": ["-x"]})
    assert out.is_error and out.output == (
        "Command rejected by user: pytest -x. Try a different approach (e.g. a static check).")


@pytest.mark.asyncio
async def test_run_command_timeout_and_policy_never_blame_the_user(tmp_path: Path) -> None:
    timeout = await _registry(tmp_path, ApprovalOutcome.deny("timeout")).execute(
        "run_command", {"command": "pytest", "args": ["-x"]})
    policy = await _registry(tmp_path, ApprovalOutcome.deny("policy")).execute(
        "run_command", {"command": "pytest", "args": ["-x"]})
    assert timeout.output == "No decision arrived in time; command `pytest -x` was not run."
    assert policy.output == f"Command `pytest -x` {_POLICY_TAIL}"
    assert "by user" not in timeout.output + policy.output


@pytest.mark.asyncio
async def test_start_session_is_approved_by_an_outcome_not_silently_denied(tmp_path: Path) -> None:
    async def cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        return ApprovalOutcome.from_command(CommandDecision(approve=True))
    src = ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)
    out = await src.execute("start_session", {"command": "echo", "args": ["hi"],
                                              "yield_time_ms": 2000})
    assert not out.is_error and "hi" in out.output


@pytest.mark.asyncio
async def test_start_session_timeout_wording(tmp_path: Path) -> None:
    async def cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:
        return ApprovalOutcome.deny("timeout")
    src = ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)
    out = await src.execute("start_session", {"command": "echo", "args": ["hi"]})
    assert out.is_error
    assert out.output == "No decision arrived in time; command `echo hi` was not run."


@pytest.mark.asyncio
async def test_mcp_timeout_and_policy_wording() -> None:
    async def timeout(server: str, tool: str, args: dict[str, object]) -> ApprovalOutcome:
        return ApprovalOutcome.deny("timeout")

    async def policy(server: str, tool: str, args: dict[str, object]) -> ApprovalOutcome:
        return ApprovalOutcome.deny("policy")

    t = await McpToolSource(object(), timeout).execute("mcp__gh__create_issue", {})
    p = await McpToolSource(object(), policy).execute("mcp__gh__create_issue", {})
    assert t.output == "No decision arrived in time; MCP tool `gh.create_issue` was not run."
    assert p.output == f"MCP tool `gh.create_issue` {_POLICY_TAIL}"


def _controller(tmp_path: Path, store: ChatThreadStore, **kw: object) -> ChatController:
    return ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK, **kw)


@pytest.mark.asyncio
async def test_chat_command_timeout_is_marked_timeout(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store, command_decision_timeout_sec=0.05)
    outcome = await ctrl._command_approval_cb(tid, f"chat:{tid}", "rm", ["-rf"], "")
    assert outcome.approved is False and outcome.denied_by == "timeout"


@pytest.mark.asyncio
async def test_chat_mcp_timeout_is_marked_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_MCP_DECISION_TIMEOUT_SEC", "0.05")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    outcome = await _controller(tmp_path, store)._mcp_approval_cb(
        tid, f"chat:{tid}", "gh", "t", {})
    assert outcome.approved is False and outcome.denied_by == "timeout"


@pytest.mark.asyncio
async def test_a_client_cannot_post_denied_by(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    ctrl = _controller(tmp_path, store)
    gate = asyncio.create_task(ctrl._command_approval_cb(tid, f"chat:{tid}", "ls", [], ""))
    for _ in range(100):
        await asyncio.sleep(0)
        if ctrl._pending_command:
            break
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(f"/v1/chat/threads/{tid}/command-decision",
                                 json={"approve": False, "denied_by": "policy"})
    assert resp.status_code == 200
    outcome = await gate
    assert outcome.approved is False and outcome.denied_by == "user"
```

Append to `tests/test_command_gate_engine.py` (uses the file's own `_make_orchestrator`/`_seed_task` helpers):

```python
@pytest.mark.asyncio
async def test_task_command_timeout_is_marked_timeout(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    orch = _make_orchestrator(tmp_path, command_decision_timeout_sec=0.05)
    task = await _seed_task(orch, workspace=str(tmp_path / "ws"))
    cb = orch._build_command_approval_callback(task.task_id)
    outcome = await cb("pytest", ["-q"], str(tmp_path / "ws"))
    assert outcome.approved is False and outcome.denied_by == "timeout"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_approval_outcome.py > /tmp/b3.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/b3.txt`
Expected: `exit=2`, `ImportError` (`ApprovalOutcome`).

- [ ] **Step 3: The model and the wording helper**

In `agentd/domain/models.py`, directly after `McpToolDecision`:

```python
DeniedBy = Literal["user", "policy", "timeout"]


class ApprovalOutcome(BaseModel):
    """Server-internal result of a command/MCP approval gate (spec §4.6.4).

    NEVER a request body: CommandDecision/McpToolDecision are what clients post, and a
    client must not be able to claim denied_by="policy". The routes build a plain
    decision (→ denied_by="user"); only server code sets "timeout" (no decision arrived)
    or "policy" (an agent's permission mode denied it without asking anyone). `decision`
    carries the user's CommandDecision for commands so rule_from_decision keeps working.
    """
    approved: bool
    denied_by: DeniedBy | None = None
    decision: CommandDecision | None = None

    @classmethod
    def from_command(
        cls, decision: CommandDecision, *, denied_by: DeniedBy = "user",
    ) -> ApprovalOutcome:
        return cls(approved=decision.approve,
                   denied_by=None if decision.approve else denied_by,
                   decision=decision)

    @classmethod
    def allow(cls) -> ApprovalOutcome:
        return cls(approved=True)

    @classmethod
    def deny(cls, by: DeniedBy) -> ApprovalOutcome:
        return cls(approved=False, denied_by=by)
```

Create `agentd/tools/approvals.py`:

```python
"""Approval-callback types and truthful denial wording (spec §4.6.4).

Today's user-rejection strings stay byte-identical (each consumer passes its own as
user_text); timeouts and policy denials say what actually happened instead of blaming
a user who was never asked.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentd.domain.models import ApprovalOutcome

CommandApprovalCallback = Callable[[str, list[str], str], Awaitable[ApprovalOutcome]]
McpApprovalCallback = Callable[[str, str, dict[str, object]], Awaitable[ApprovalOutcome]]

_POLICY_TAIL = (
    " is not permitted for this agent (no remembered rule allows it); nobody was asked. "
    "Work without it or note the need in your report."
)


def denial_text(outcome: ApprovalOutcome, *, subject: str, user_text: str) -> str:
    """The tool-result text for a denied approval. `subject` is lower-case prose naming
    the call ("command `pytest -x`"); `user_text` is the consumer's existing wording."""
    if outcome.denied_by == "timeout":
        return f"No decision arrived in time; {subject} was not run."
    if outcome.denied_by == "policy":
        return subject[:1].upper() + subject[1:] + _POLICY_TAIL
    return user_text
```

- [ ] **Step 4: Producers**

`agentd/orchestrator/engine.py`, `_build_command_approval_callback`:
- add `ApprovalOutcome,` (between `AgentToolTrace,` and `CandidateScoreBreakdown,`) and `DeniedBy,` (between `DeltaReplanRequest,` and `Diagnostic,`) to the module-level `from agentd.domain.models import (...)` block — alphabetical, or ruff I001 fires;
- docstring: `(command, args, cwd) -> CommandDecision` → `(command, args, cwd) -> ApprovalOutcome`;
- `async def _cb(command: str, args: list[str], cwd: str) -> ApprovalOutcome:`;
- the three early `return CommandDecision(approve=True)` → `return ApprovalOutcome.allow()`;
- replace `decision = CommandDecision(approve=False)` (the line before `try:`) with `decision = CommandDecision(approve=False)` **and** `denied_by: DeniedBy = "user"` ; in `except asyncio.TimeoutError:` add `denied_by = "timeout"` after `decision = CommandDecision(approve=False)`;
- `return decision` → `return ApprovalOutcome.from_command(decision, denied_by=denied_by)`.

`agentd/chat/controller.py`:
- import `ApprovalOutcome, DeniedBy` in the `agentd.domain.models` import;
- `_command_approval_cb(...) -> ApprovalOutcome`; its two early `return CommandDecision(approve=True)` → `return ApprovalOutcome.allow()`; add `denied_by: DeniedBy = "user"` before `timeout = self._command_decision_timeout_sec`; in `except TimeoutError:` add `denied_by = "timeout"`; `return decision` → `return ApprovalOutcome.from_command(decision, denied_by=denied_by)`;
- `_mcp_approval_cb(...) -> ApprovalOutcome`; `return True` (remembered rule) → `return ApprovalOutcome.allow()`; `denied_by: DeniedBy = "user"` before `timeout = mcp_decision_timeout_sec()`, `denied_by = "timeout"` in `except TimeoutError:`; `return decision.approve` →
  ```python
          if decision.approve:
              return ApprovalOutcome.allow()
          return ApprovalOutcome.deny(denied_by)
  ```

(Breadcrumbs are unchanged: they are user-facing records, and the spec scopes truthful wording to the model-facing tool results.)

- [ ] **Step 5: Consumers**

`agentd/tools/registry.py`, run_command branch — replace the `decision = ...` / `if not decision.approve:` block with:

```python
            if self._command_approval_callback is not None:
                outcome = await self._command_approval_callback(command, cmd_args, cwd)
                if not outcome.approved:
                    cmdline = f"{command} {' '.join(cmd_args)}".strip()
                    return ToolOutput(
                        output=denial_text(
                            outcome, subject=f"command `{cmdline}`",
                            user_text=(f"Command rejected by user: {cmdline}"
                                       ". Try a different approach (e.g. a static check).")),
                        is_error=True,
                    )
```

and add `from agentd.tools.approvals import denial_text` to the module imports.

`agentd/exec_sessions/tool_source.py`: delete the local `CommandApprovalCallback = ...` alias (and the blank lines after it), `from collections.abc import Awaitable, Callable` and `from agentd.domain.models import CommandDecision` — nothing else in the module uses them; add `from agentd.tools.approvals import CommandApprovalCallback, denial_text` directly above `from agentd.tools.registry import ToolDefinition, ToolOutput`. In `_start`, replace the `decision = ...` / `if not getattr(...)` block with:

```python
        outcome = await self._approve(command, cmd_args, cwd)
        if not outcome.approved:
            cmdline = f"{command} {' '.join(cmd_args)}".strip()
            return ToolOutput(
                output=denial_text(
                    outcome, subject=f"command `{cmdline}`",
                    user_text=(f"Command rejected by user: {command}. Do not retry the "
                               "same command — adapt or ask.")),
                is_error=True)
```

`agentd/mcp/tool_source.py`: delete `ApprovalCallback = ...` and the now-unused `from collections.abc import Awaitable, Callable`; import `from agentd.tools.approvals import McpApprovalCallback, denial_text`; the constructor annotation becomes `approval_callback: McpApprovalCallback`. The import lines end up as `import logging` / (blank) / `from agentd.mcp.config import …` / `from agentd.prompting.tagged import …` / `from agentd.tools.approvals import McpApprovalCallback, denial_text` / `from agentd.tools.registry import …`. In `execute`:

```python
        outcome = await self._approve(server, tool_name, dict(args))
        if not outcome.approved:
            user_text = (render_prompt(_MCP_REJECTED_TEMPLATE, self._render_ctx)
                         .replace("{server}", server).replace("{tool}", tool_name))
            return ToolOutput(
                output=denial_text(outcome, subject=f"MCP tool `{server}.{tool_name}`",
                                   user_text=user_text),
                is_error=True)
```

Check for other importers of the removed alias: `grep -rn "ApprovalCallback\b" agentd tests | grep -v McpApprovalCallback\|CommandApprovalCallback` — Expected: no output.

- [ ] **Step 6: Migrate fakes and direct-call assertions**

- Fakes that return a decision now return an outcome:
  - `tests/test_tools_registry.py`: `async def cb(...) -> ApprovalOutcome:` returning `ApprovalOutcome.from_command(CommandDecision(approve=False))` (import `ApprovalOutcome` next to its local `CommandDecision` import).
  - `tests/test_exec_sessions_tool_source.py` `_source` and `tests/test_exec_sessions_wiring.py` `cb`: `return ApprovalOutcome.from_command(CommandDecision(approve=approve))` / `(approve=True)`; add `ApprovalOutcome` to their `agentd.domain.models` import.
  - `tests/test_mcp_tool_source.py`: `_approve` → `return ApprovalOutcome.allow()`, `_reject` → `return ApprovalOutcome.deny("user")`; add `from agentd.domain.models import ApprovalOutcome` above its `agentd.mcp.tool_source` import.
  - `tests/test_child_tool_texts.py` `deny` and `tests/prompt_goldens.py` `_deny`: `-> ApprovalOutcome:` / `return ApprovalOutcome.deny("user")`. Imports: in `test_child_tool_texts.py` add `from agentd.domain.models import ApprovalOutcome` after `from agentd.chat.todo_source import TodoToolSource`; `prompt_goldens.py` imports lazily inside its function (and has `from __future__ import annotations`, so the annotation alone is not enough at runtime) — add `    from agentd.domain.models import ApprovalOutcome` between its `from agentd.chat.edit_session import _validate_patch_ops` and `from agentd.mcp.tool_source import McpToolSource` lines. The `history/mcp_rejected` golden must stay unchanged — that is the byte-identity proof for the user wording.
- Direct callers of the callbacks:
  - `tests/test_controller_command_gate.py` and `tests/test_command_gate_engine.py`: `sed -i '' 's/decision\.approve is /decision.approved is /' tests/test_controller_command_gate.py tests/test_command_gate_engine.py` (every `decision` there is a callback result).
  - `tests/test_controller_mcp_gate.py`: `assert await cb_task is True` → `assert (await cb_task).approved is True`; `assert await cb_task is False` → `assert (await cb_task).approved is False`; the two `assert await ctrl._mcp_approval_cb(\n        th.thread_id, f"chat:{th.thread_id}", "gh", "t", {}) is True/False` → `assert (await ctrl._mcp_approval_cb(\n        th.thread_id, f"chat:{th.thread_id}", "gh", "t", {})).approved is True/False`. In `test_timeout_rejects` also assert `.denied_by == "timeout"`.
  - `tests/test_multi_gate_decisions.py`: `assert (await second).approve is False` → `.approved is False`; `assert (await first).approve is True` → `.approved is True`; and in the route test replace the two `decision` lines with
    ```python
        outcome = await first
        assert outcome.approved is True and type(outcome.decision) is CommandDecision
    ```

Run: `grep -nE "getattr\((decision|outcome), \"approve\"|decision\.approve\b" agentd/tools/registry.py agentd/exec_sessions/tool_source.py agentd/mcp/tool_source.py`
Expected: no output.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_approval_outcome.py tests/test_prompt_goldens.py tests/test_tools_registry.py tests/test_exec_sessions_tool_source.py tests/test_exec_sessions_wiring.py tests/test_mcp_tool_source.py tests/test_child_tool_texts.py tests/test_controller_command_gate.py tests/test_command_gate_engine.py tests/test_controller_mcp_gate.py tests/test_gate_breadcrumbs.py tests/test_multi_gate_decisions.py > /tmp/b3.txt 2>&1; echo exit=$?; tail -1 /tmp/b3.txt`
Expected: `exit=0`.

- [ ] **Step 8: Commit**

```bash
git add agentd/domain/models.py agentd/tools/approvals.py agentd/tools/registry.py agentd/exec_sessions/tool_source.py agentd/mcp/tool_source.py agentd/orchestrator/engine.py agentd/chat/controller.py tests/
git commit -m "feat(approvals): internal ApprovalOutcome with truthful timeout/policy denials"
```

---

### Task 4: `StaleWriteError` and the `STALE_READ` guidance

**Files:**
- Modify: `agentd/domain/models.py` (`PatchFailureCode.STALE_READ`), `agentd/chat/edit_session.py` (`StaleWriteError`), `agentd/chat/controller_loop.py` (guidance entry)
- Test: `tests/test_stale_write_error.py`

**Interfaces:**
- Produces: `PatchFailureCode.STALE_READ = "stale_read"`; `agentd.chat.edit_session.StaleWriteError(PatchPreflightFailed)` constructed as `StaleWriteError(path, message)`, carrying `.path` and `.issues == [PatchPreflightIssue(code=STALE_READ, file=path, message=message)]`.
- Nothing raises it yet — the write log that raises it (and the `accept()`/loop `"stale"` branch) is Phase 2 (spec §7.4). Because it subclasses `PatchPreflightFailed` (a `RuntimeError`), raising it from `apply()` already lands in the loop's existing `except Exception` PATCH FAILED branch, which reads `issues[0].code` for guidance.

- [ ] **Step 1: Write the failing test**

Create `tests/test_stale_write_error.py`:

```python
"""StaleWriteError (spec §7.4): the typed refusal for editing a file another agent
changed after this agent's last read."""
from agentd.chat.controller_loop import _edit_failure_guidance
from agentd.chat.edit_session import StaleWriteError
from agentd.domain.models import PatchFailureCode
from agentd.patch.engine import PatchPreflightFailed


def test_stale_write_error_carries_a_stale_read_issue() -> None:
    exc = StaleWriteError("src/a.py", "`src/a.py` was modified by agent `impl` (general) "
                                      "after your last read — read it again before editing.")
    assert isinstance(exc, PatchPreflightFailed) and isinstance(exc, RuntimeError)
    assert exc.path == "src/a.py"
    assert [(i.code, i.file) for i in exc.issues] == [(PatchFailureCode.STALE_READ, "src/a.py")]
    assert str(exc).startswith("`src/a.py` was modified by agent `impl`")


def test_stale_read_has_its_own_guidance() -> None:
    guidance = _edit_failure_guidance(StaleWriteError("a.py", "stale"))
    assert guidance == ("Another agent changed this file after your last read. read_file it "
                        "again, then re-emit your edit against its current content.")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stale_write_error.py > /tmp/b4.txt 2>&1; echo exit=$?; grep -m1 ImportError /tmp/b4.txt`
Expected: `exit=2`, `ImportError` (`StaleWriteError`).

- [ ] **Step 3: Implement**

`agentd/domain/models.py` — add to `PatchFailureCode` after `APPLY_ERROR`:

```python
    # Another agent promoted this file after the editing agent last read it (spec §7.4).
    STALE_READ = "stale_read"
```

`agentd/chat/edit_session.py` — extend imports to `from agentd.domain.models import DiffEntry, PatchFailureCode, PatchPreflightIssue` and `from agentd.patch.engine import PatchEngine, PatchPreflightFailed`, then add after `_CONTENT_FIELDS`:

```python
class StaleWriteError(PatchPreflightFailed):
    """An agent tried to edit a file another agent changed after its last read
    (spec §7.4). Carries a STALE_READ issue so the loop's PATCH FAILED branch gives the
    re-read guidance. Raised by the sub-agent write guard (Phase 2)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message, [PatchPreflightIssue(
            code=PatchFailureCode.STALE_READ, file=path, message=message)])
        self.path = path
```

`agentd/chat/controller_loop.py` — add to `_EDIT_GUIDANCE_BY_CODE` after the `PATH_ESCAPE` entry:

```python
    PatchFailureCode.STALE_READ: (
        "Another agent changed this file after your last read. read_file it again, "
        "then re-emit your edit against its current content."),
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_stale_write_error.py tests/test_turn_edit_session.py tests/test_prompt_goldens.py > /tmp/b4.txt 2>&1; echo exit=$?; tail -1 /tmp/b4.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/domain/models.py agentd/chat/edit_session.py agentd/chat/controller_loop.py tests/test_stale_write_error.py
git commit -m "feat(chat): StaleWriteError and STALE_READ edit guidance"
```

---

### Task 5: `TurnEditSession` re-seeds touched files on every `apply`

**Files:**
- Modify: `agentd/chat/edit_session.py`
- Test: `tests/test_turn_edit_session.py` (two tests appended)

**Interfaces:**
- Behavior change: before each `apply` after the first, every touched file is re-copied from real into the shadow; a touched file that no longer exists in real has its stale shadow copy removed. `_touched_ever` is deleted. The first `apply` is unchanged (`prepare_lightweight` seeds from real).
- Why: the old code seeded a file only on its FIRST touch, so a file changed on disk between two edits of the same turn (a formatter via `run_command` today; a sibling agent's promote in Phase 2) was patched from the stale shadow copy and promoted over the newer real content — a silent lost update (spec §7.5).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_turn_edit_session.py`:

```python
def _session(tmp_path: Path, real: Path, turn_id: str) -> TurnEditSession:
    return TurnEditSession(
        turn_id=turn_id, real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        patch_engine=PatchEngine(),
    )


@pytest.mark.asyncio
async def test_a_file_changed_on_disk_between_edits_is_not_overwritten(tmp_path: Path):
    """Lost update: an external change (formatter, sibling agent) between two edits of
    the same file must survive the second edit's promote."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("a = 1\nb = 1\n")
    sess = _session(tmp_path, real, "lost1")
    await sess.apply(_sr("f.py", "a = 1", "a = 2"))
    await sess.accept()
    (real / "f.py").write_text("a = 2\nb = 1\nexternal = True\n")  # changed on disk
    await sess.apply(_sr("f.py", "b = 1", "b = 2"))
    await sess.accept()
    assert (real / "f.py").read_text() == "a = 2\nb = 2\nexternal = True\n"
    await sess.close()


@pytest.mark.asyncio
async def test_a_file_deleted_on_disk_between_edits_can_be_recreated(tmp_path: Path):
    """The shadow must not keep a copy of a file real no longer has — create_file
    would otherwise fail with 'already exists'."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "g.py").write_text("x = 1\n")
    sess = _session(tmp_path, real, "gone1")
    await sess.apply(_sr("g.py", "x = 1", "x = 2"))
    await sess.accept()
    (real / "g.py").unlink()  # deleted on disk
    await sess.apply([{"op": "create_file", "file": "g.py", "content": "y = 1\n",
                       "reason": "r"}])
    await sess.accept()
    assert (real / "g.py").read_text() == "y = 1\n"
    await sess.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_turn_edit_session.py > /tmp/b5.txt 2>&1; echo exit=$?; grep '^FAILED' /tmp/b5.txt`
Expected: `exit=1`; `FAILED` lines for exactly the two new tests (the first loses `external = True`; the second raises because `g.py` already exists in the shadow).

- [ ] **Step 3: Implement**

In `agentd/chat/edit_session.py`, delete the `self._touched_ever: set[str] = set()` line from `__init__`, and replace `_ensure_shadow` with:

```python
    async def _ensure_shadow(self, touched: list[str]) -> Path:
        if self._shadow is None:
            sw = await self._wm.prepare_lightweight(
                f"chatturn-{self._turn_id}", str(self._real), touched
            )
            self._shadow = Path(sw.shadow_path)
            return self._shadow
        # Re-seed EVERY touched file from real on every apply, not only on first touch:
        # real may have changed since this shadow last held the file (a formatter run via
        # run_command, or another agent's promote), and patching a stale copy would
        # promote over the newer content — a silent lost update (spec §7.5). A file that
        # no longer exists in real loses its stale shadow copy, so create_file works.
        for rel in touched:
            src, dst = self._real / rel, self._shadow / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
        return self._shadow
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_turn_edit_session.py tests/test_edit_gate_controller.py tests/test_controller_durable_edit.py > /tmp/b5.txt 2>&1; echo exit=$?; tail -1 /tmp/b5.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/edit_session.py tests/test_turn_edit_session.py
git commit -m "fix(chat): re-seed touched files from real on every turn-edit apply"
```

---

### Part I verification

- [ ] **Step 1: Full backend suite**

Run: `.venv/bin/python -m pytest tests/ --color=no > /tmp/b-part1.txt 2>&1; echo exit=$?; grep '^FAILED' /tmp/b-part1.txt; tail -1 /tmp/b-part1.txt`
Expected: `exit=1`; the ONLY `FAILED` line is `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` (pre-existing).

- [ ] **Step 2: Lint — new files clean, touched files add nothing**

Run: `.venv/bin/ruff check agentd/tools/approvals.py tests/gate_helpers.py tests/test_controller_gate_list.py tests/test_multi_gate_decisions.py tests/test_approval_outcome.py tests/test_stale_write_error.py`
Expected: `All checks passed!`

The touched modules carry many pre-existing findings (`engine.py` alone has ~75), so compare per-file/per-rule counts against the commit before Task 1 (`BASE` = the SHA you started from):

```bash
F=(agentd/chat/models.py agentd/chat/storage.py agentd/chat/live_state.py agentd/chat/controller.py agentd/api/routes.py agentd/domain/models.py agentd/tools/registry.py agentd/exec_sessions/tool_source.py agentd/mcp/tool_source.py agentd/orchestrator/engine.py agentd/chat/edit_session.py agentd/chat/controller_loop.py)
counts() { sed $'s/\x1b\\[[0-9;]*m//g' | grep -E "^agentd" | sed -E 's/:[0-9]+:[0-9]+: ([A-Z0-9]+).*/ \1/' | sort | uniq -c; }
.venv/bin/ruff check "${F[@]}" --output-format concise | counts > /tmp/lint-after.txt
for f in "${F[@]}"; do git show "${BASE}:services/agentd-py/$f" | .venv/bin/ruff check --stdin-filename "$f" --output-format concise -; done | counts > /tmp/lint-base.txt
comm -13 /tmp/lint-base.txt /tmp/lint-after.txt > /tmp/lint-new.txt; echo new=$(wc -l < /tmp/lint-new.txt); cat /tmp/lint-new.txt
```

Expected: `new=0` — no new finding in any touched module (a pre-existing finding may disappear; that is fine).

- [ ] **Step 3: mypy on the touched modules**

mypy follows imports, so its total (~200 errors in ~30 files) is dominated by modules this plan never touches. Filter to the touched files:

Run: `.venv/bin/mypy --no-color-output agentd/chat/models.py agentd/chat/storage.py agentd/tools/approvals.py agentd/chat/edit_session.py > /tmp/b-mypy.txt 2>&1; grep -E "^agentd/(chat/(models|storage|edit_session)|tools/approvals)\.py.*error" /tmp/b-mypy.txt | sed -E 's/:[0-9]+:/:/' | sort | uniq -c`
Expected: exactly `16 agentd/chat/storage.py: error: Missing type parameters for generic type "dict"  [type-arg]` — all pre-existing; none in the other three files.

---

# Part II — Frontend (the gate list end to end)

Part II changes TypeScript only, until Task 8 deletes the legacy backend field. Commands run from the repo root. The extension tsconfig covers `src/` only (its `test/` is run by vitest, not typechecked); the webview tsconfig covers `webview-ui/src` **including** its tests.

### Task 6: editor-client — gate ids, agents, the gate list, `gateId` on decisions

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/decision-clients.test.ts`, `apps/editor-client/test/http-backend-client.test.ts`

**Interfaces:**
- Produces: `GateAgentSchema`/`GateAgent` (`{id, label, name}`); `PendingGateSchema` gains `gateId: string` and `agent: GateAgent | null` (default `null`); `ThreadLiveStateSchema.pendingGates: PendingGate[]` (default `[]`); `pendingGate` stays (LEGACY, removed in Task 8).
- `BackendTaskClient.postEditDecision(threadId, decision, reason?, gateId?)`, `postChatCommandDecision(threadId, decision, gateId?)`, `postChatMcpDecision(threadId, decision, gateId?)` — `gate_id` is sent only when `gateId` is given (omitted = the backend's single-gate fallback).

- [ ] **Step 1: Write the failing tests**

Append to `apps/editor-client/test/decision-clients.test.ts`:

```ts
describe("gate-addressed decisions (multi-gate)", () => {
  const ok = () => vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });

  it("sends gate_id on an edit decision only when given", async () => {
    const fetchMock = ok();
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.postEditDecision("th1", "accept", "", "g-edit");
    await c.postEditDecision("th1", "accept");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(
      { decision: "accept", reason: "", gate_id: "g-edit" });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(
      { decision: "accept", reason: "" });
  });

  it("sends gate_id on command and MCP decisions", async () => {
    const fetchMock = ok();
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.postChatCommandDecision("th1", { approve: false, remember: false, scope: "exact" }, "g-cmd");
    await c.postChatMcpDecision("th1", { approve: true, remember: false }, "g-mcp");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(
      { approve: false, remember: false, scope: "exact", gate_id: "g-cmd" });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(
      { approve: true, remember: false, gate_id: "g-mcp" });
  });
});
```

In `apps/editor-client/test/http-backend-client.test.ts`, replace the body of `test("getThreadLiveState maps a gate payload to camelCase", ...)`'s mocked JSON gate fields — `pending_gate: { kind: "command", payload: { command: "pytest" } },` — with:

```ts
            pending_gates: [
              { gate_id: "task:task-1:command", kind: "command",
                payload: { command: "pytest" }, agent: null },
              { gate_id: "g-2", kind: "mcp_tool", payload: {},
                agent: { id: "agent-1", label: "impl", name: "general" } },
            ],
            pending_gate: { gate_id: "task:task-1:command", kind: "command",
                            payload: { command: "pytest" }, agent: null },
```

and its `pendingGate` expectations with:

```ts
    expect(live.pendingGates.map((g) => g.gateId)).toEqual(["task:task-1:command", "g-2"]);
    expect(live.pendingGates[0].kind).toBe("command");
    expect(live.pendingGates[0].payload.command).toBe("pytest");
    expect(live.pendingGates[0].agent).toBeNull();
    expect(live.pendingGates[1].agent).toEqual({ id: "agent-1", label: "impl", name: "general" });
    expect(live.pendingGate?.gateId).toBe("task:task-1:command");
```

In the idle-thread test that expects `expect(live.pendingGate).toBeNull();`, add `expect(live.pendingGates).toEqual([]);` after it.

In `apps/editor-client/test/mcp-gate.test.ts`, the gate parsed by `PendingGateSchema.parse({...})` needs the now-required id: add `gateId: "g1",` as its first field.

- [ ] **Step 2: Run to verify they fail**

Run: `npm run -w @crucible/editor-client test > /tmp/p6.txt 2>&1; echo exit=$?; grep -E "✗|FAIL|×" /tmp/p6.txt | head`
Expected: `exit=1`; the new decision tests fail (no `gate_id` in the body) and the live-state test fails (`pendingGates` undefined).

- [ ] **Step 3: Contracts**

In `task-contracts.ts`, replace the `PendingGateSchema` block (and its comment) with:

```ts
// Which sub-agent raised a gate (spec §4.5); null on the main agent's gates.
export const GateAgentSchema = z.object({
  id: z.string(),
  label: z.string(),
  name: z.string(),
});
export type GateAgent = z.infer<typeof GateAgentSchema>;

// One gate a thread is waiting on (mirrors backend PendingGate). A thread can hold
// several at once (spec §4.5), each addressed by gateId: a uuid for controller gates,
// "task:{taskId}:{kind}" for task-derived gates. "mode"/"edit"/"clarify"/"mcp_tool" are
// controller gates (no task) — the Zod enum is the RUNTIME gate: a kind missing here
// makes ThreadLiveStateSchema.parse() throw, which pollThreadLiveState swallows, so the
// gate silently never renders.
export const PendingGateSchema = z.object({
  gateId: z.string(),
  kind: z.enum(["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool"]),
  payload: z.record(z.unknown()).default({}),
  agent: GateAgentSchema.nullable().default(null),
});
export type PendingGate = z.infer<typeof PendingGateSchema>;
```

In `ThreadLiveStateSchema`, replace `pendingGate: PendingGateSchema.nullable(),` with:

```ts
  // Every pending gate, controller gates first (spec §4.5).
  pendingGates: z.array(PendingGateSchema).default([]),
  // LEGACY: the first of pendingGates. Removed once nothing reads it (Plan 1B Task 8).
  pendingGate: PendingGateSchema.nullable(),
```

In the `BackendTaskClient` interface:

```ts
  postEditDecision(threadId: string, decision: "accept" | "reject", reason?: string, gateId?: string): Promise<void>;
  // Controller run_command gate: a plain JSON ack (continuation rides the open message stream).
  // gateId addresses one of several pending gates; omitted = the single pending one.
  postChatCommandDecision(threadId: string, decision: CommandDecision, gateId?: string): Promise<void>;
  // Controller mcp_tool gate: a plain JSON ack (continuation rides the open message stream).
  postChatMcpDecision(threadId: string, decision: McpToolDecision, gateId?: string): Promise<void>;
```

(`src/index.ts` re-exports the contracts module with `export *`, so the new `GateAgentSchema`/`GateAgent` need no index change.)

- [ ] **Step 4: Client**

In `http-backend-client.ts`:

```ts
  async postEditDecision(
    threadId: string,
    decision: "accept" | "reject",
    reason?: string,
    gateId?: string
  ): Promise<void> {
    const body: Record<string, unknown> = { decision, reason: reason ?? "" };
    if (gateId !== undefined) body.gate_id = gateId;
    await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/edit-decision`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }
```

`postChatCommandDecision(threadId, decision, gateId?: string)`: after **its** `if (decision.ruleValue !== undefined) body.rule_value = decision.ruleValue;` line add `if (gateId !== undefined) body.gate_id = gateId;`. (The task-route `sendCommandDecision` above it has an identical `rule_value` line — leave that one alone; it has no `gateId`.)

`postChatMcpDecision(threadId, decision, gateId?: string)`:

```ts
    const body: Record<string, unknown> = {
      approve: decision.approve, remember: decision.remember,
    };
    if (gateId !== undefined) body.gate_id = gateId;
    await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/mcp-decision`,
      { method: "POST", body: JSON.stringify(body) }
    );
```

In `getThreadLiveState`, replace the `const gate = ...` line and the `pendingGate:` entry with:

```ts
    // Gates keep their payload keys (snake_case) — same passthrough as before; only the
    // envelope fields are mapped. agent keys (id/label/name) are already camel-safe.
    const toGate = (g: Record<string, unknown>) => ({
      gateId: g["gate_id"],
      kind: g["kind"],
      payload: g["payload"] ?? {},
      agent: g["agent"] ?? null,
    });
    const rawGates = Array.isArray(raw["pending_gates"])
      ? (raw["pending_gates"] as Record<string, unknown>[])
      : [];
    const legacyGate = raw["pending_gate"] as Record<string, unknown> | null;
```

```ts
      pendingGates: rawGates.map(toGate),
      pendingGate: legacyGate ? toGate(legacyGate) : null,
```

- [ ] **Step 5: Run the tests, build**

Run: `npm run -w @crucible/editor-client test > /tmp/p6.txt 2>&1; echo exit=$?; tail -5 /tmp/p6.txt`
Expected: `exit=0`.

Run: `npm run -w @crucible/editor-client build > /tmp/p6b.txt 2>&1; echo exit=$?`
Expected: `exit=0` (the extension types off `dist/index.d.ts` — CLAUDE.md "Build order").

- [ ] **Step 6: Commit**

```bash
git add apps/editor-client
git commit -m "feat(editor-client): gate ids, agent tags and the pending gate list"
```

---

### Task 7: Extension + webview — stacked gate cards, `gateId` on every decision

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts`, `src/chat-panel.ts`, `src/extension.ts`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`, `hooks/useAppState.ts`, `inputAvailability.ts`, `components/LiveSlot.tsx`, `components/ThreadView.tsx`, `components/messages/gates/{CommandGate,McpGate,EditGate}.tsx`
- Tests: `apps/vscode-extension/test/controller.test.ts`; webview `src/test/{views,gates,assembly}.test.tsx`, `src/components/ThreadView.test.tsx`, `src/inputAvailability.test.ts`, `src/components/messages/gates/EditGate.test.tsx`

**Interfaces:**
- Host → webview: `{type: "renderLiveGates", gates: LiveGateView[]}` replaces `renderLiveGate`/`clearLiveGate` (an empty list clears). `LiveGateView` gains `gateId: string` and `agent: {id, label, name} | null`.
- `ControllerUI.renderLiveGates(gates)` / `clearLiveGates()` replace `renderLiveGate`/`clearLiveGate`.
- Webview → host: `commandDecision`, `mcpDecision`, `editDecision` carry `gateId`. `CommandDecisionHandler(taskId, decision, gateId)`, `EditDecisionHandler(threadId, decision, reason, gateId)`, `McpDecisionHandler(threadId, decision, gateId)`.
- A gate's `taskId` is the active task id for task gates (`gateId` starts with `task:`) and the **thread id** for controller gates — per gate, so a controller gate listed next to a task gate still posts to the thread. (Today one `activeTaskId ?? threadId` covered the single gate.)
- Command decisions route by gate id: `task:` → the task route; otherwise → the chat route with `gate_id`. This replaces the `latestLiveState.activeTaskId == null` heuristic.
- A 404 **or** 409 from an edit/command/MCP chat decision is benign (the gate already resolved — e.g. an auto-accept or a stop raced the click); the next poll reconciles the card.

- [ ] **Step 1: Write the failing tests**

In `apps/vscode-extension/test/controller.test.ts`, append inside the `describe` that holds `pollThreadLiveState renders one gate card, dedups, and removes on null`:

```ts
  test("pollThreadLiveState renders every pending gate, addressing each to its owner", async () => {
    const state: StubBackendState = {
      submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
      getResultCalls: [], planFeedbackCalls: [], liveCalls: [],
      liveResponse: NULL_LIVE_STATE,
    };
    const backend = createStubBackend(state);
    const renders: LiveGateView[][] = [];
    const ui = createUi({ renderLiveGates: (gates) => { renders.push(gates); } });
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(), ui,
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.switchChatThread("chat-1");
    await Promise.resolve();
    controller.dispose();
    renders.length = 0;

    const agent = { id: "agent-1", label: "impl", name: "general" };
    state.liveResponse = {
      activeTaskId: "task-1", status: "AWAITING_COMMAND_DECISION", plan: null,
      turnActive: true, pendingGate: null,
      pendingGates: [
        { gateId: "g-mcp", kind: "mcp_tool", payload: {}, agent },
        { gateId: "task:task-1:command", kind: "command", payload: { command: "ls" }, agent: null },
      ],
    };
    await controller.pollThreadLiveState();
    expect(renders).toHaveLength(1);
    expect(renders[0].map((g) => [g.gateId, g.taskId, g.agent?.label ?? null])).toEqual([
      ["g-mcp", "chat-1", "impl"],
      ["task:task-1:command", "task-1", null],
    ]);
  });

  test("a gate decision that lost the race (404) is swallowed, not shown as an error", async () => {
    const errors: string[] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend({
        submitPayloads: [], getTaskCalls: [], acceptCalls: [],
        rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
      }),
      postChatMcpDecision: async () => {
        throw Object.assign(new Error("no pending mcp_tool gate"), { status: 404 });
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ showError: (m: string) => { errors.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z"
    );
    await controller.handleMcpDecisionFromChat("chat-1", { approve: true, remember: false }, "g-gone");
    controller.dispose();
    expect(errors).toEqual([]);
  });
```

(If `createUi`'s override parameter does not accept `showError`, check its signature at the top of the file — it takes `Partial<ControllerUI>`; `showError` is a `ControllerUI` member.)

In `webview-ui/src/test/views.test.tsx`, append:

```tsx
describe("LiveSlot — several gates at once", () => {
  it("renders one card per gate and tags a sub-agent's gate with its label", () => {
    const gates: LiveGateView[] = [
      { gateId: "g1", kind: "command", taskId: "th", agent: null,
        payload: { command: "ls", args: [] } },
      { gateId: "g2", kind: "mcp_tool", taskId: "th",
        agent: { id: "agent-1", label: "impl", name: "general" },
        payload: { server: "gh", tool: "create_issue", args: {} } },
    ];
    render(
      <LiveSlot liveGates={gates} livePlan={null} liveReview={null} liveError={null}
                onDismissError={vi.fn()} />,
    );
    expect(screen.getByText(/run command\?/i)).toBeTruthy();
    expect(screen.getByText(/call mcp tool: gh\.create_issue/i)).toBeTruthy();
    expect(screen.getByText("impl · general")).toBeTruthy();
  });
});
```

Append to `webview-ui/src/inputAvailability.test.ts` (inside the existing `describe`):

```ts
  it("an edit gate anywhere in the list disables input", () => {
    const r = inputAvailability({
      ...base, liveGates: [
        { gateId: "a", kind: "command", taskId: "t", payload: {} },
        { gateId: "b", kind: "edit", taskId: "t", payload: {} },
      ] });
    expect(r.placeholder).toMatch(/decision on the card/i);
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm run -w crucible-vscode-extension test > /tmp/p7.txt 2>&1; echo exit=$?; grep -cE "✗|×" /tmp/p7.txt`
Expected: `exit=1` with failures including the two new controller tests.

Run: `(cd apps/vscode-extension/webview-ui && npx vitest run > /tmp/p7w.txt 2>&1; echo exit=$?)`
Expected: `exit=1`.

- [ ] **Step 3: Host — `controller.ts`**

1. `ControllerUI`: replace the `renderLiveGate`/`clearLiveGate` lines with
   ```ts
     renderLiveGates(gates: LiveGateView[]): void;
     clearLiveGates(): void;
   ```
   and update the comment above them to "One slot per kind for plan/review/error; gates are a list (spec §4.5)".
2. `LiveGateView`:
   ```ts
   export interface LiveGateView {
     gateId: string;
     kind: "command" | "step" | "scope" | "validation" | "mode" | "edit" | "clarify" | "mcp_tool";
     payload: Record<string, unknown>;
     // The active task for task gates ("task:" ids); the thread for controller gates.
     taskId: string;
     // The sub-agent that raised the gate; null for the main agent.
     agent: { id: string; label: string; name: string } | null;
   }
   ```
3. Every `this.ui.clearLiveGate();` → `this.ui.clearLiveGates();` (`grep -n "clearLiveGate()" src/controller.ts` — three sites; the third is inside the `if (live.pendingGate)` block that item 8 replaces wholesale). Also update the comment `(renderLiveGate kind "step")` in the stream-event switch to `(renderLiveGates kind "step")`, or Step 6's leftover-name grep flags it.
4. `handleEditDecisionFromChat(threadId, decision, reason, gateId: string)`: call `client.postEditDecision(threadId, decision, reason, gateId || undefined)`; its catch uses `this.isBenignGateMiss(error)`.
5. Replace `handleCommandDecisionFromChat` with:
   ```ts
     async handleCommandDecisionFromChat(
       taskId: string,
       decision: CommandDecision,
       gateId: string,
     ): Promise<void> {
       try {
         // Task gates carry "task:{taskId}:command" ids and resolve on the task route;
         // every other command gate is a controller gate on the thread (taskId carries the
         // thread id for those — see pollThreadLiveState).
         if (gateId.startsWith("task:")) {
           await this.clientForChat().sendCommandDecision(this.liveTaskIdOr(taskId), decision);
           return;
         }
         await this.clientForChat().postChatCommandDecision(taskId, decision, gateId || undefined);
       } catch (err) {
         if (this.isBenignGateMiss(err)) return;
         this.ui.showError(`Failed to send command decision: ${err instanceof Error ? err.message : String(err)}`);
       }
     }
   ```
6. `handleMcpDecisionFromChat(threadId, decision, gateId: string)`: `postChatMcpDecision(threadId, decision, gateId || undefined)`; catch uses `this.isBenignGateMiss(err)`.
7. Below `isBenignConflict`, add:
   ```ts
     /**
      * Gate-addressed decisions (edit/command/MCP on the chat routes) also answer 404 when
      * the named gate is no longer pending — an auto-accept, a stop, or another click got
      * there first. Same reconciliation as a 409: the next /live poll redraws the cards.
      * Kept separate from isBenignConflict so a 404 from any other route still surfaces.
      */
     private isBenignGateMiss(err: unknown): boolean {
       return (
         this.isBenignConflict(err) ||
         (typeof err === "object" && err !== null && (err as { status?: number }).status === 404)
       );
     }
   ```
8. `pollThreadLiveState`: `channelActive` becomes
   ```ts
       const channelActive = live.turnActive
         || live.pendingGates.some((g) => g.kind === "mode" || g.kind === "edit");
   ```
   in the signature object `gate: live.pendingGate,` → `gates: live.pendingGates,`; and replace the `if (live.pendingGate) { ... } else { this.ui.clearLiveGate(); }` block with:
   ```ts
       if (live.pendingGates.length > 0) {
         this.ui.renderLiveGates(live.pendingGates.map((g) => ({
           gateId: g.gateId,
           kind: g.kind,
           payload: g.payload,
           agent: g.agent,
           // Per gate: controller gates have no task (their decisions go to the thread),
           // task gates carry "task:" ids — so a mixed list addresses each correctly.
           taskId: g.gateId.startsWith("task:") ? (live.activeTaskId ?? threadId) : threadId,
         })));
       } else {
         this.ui.clearLiveGates();
       }
   ```

- [ ] **Step 4: Host — `chat-panel.ts` and `extension.ts`**

`chat-panel.ts`:
- handler types:
  ```ts
  export type CommandDecisionHandler = (taskId: string, decision: CommandDecision, gateId: string) => Promise<void>;
  export type EditDecisionHandler = (threadId: string, decision: "accept" | "reject", reason: string, gateId: string) => Promise<void>;
  export type McpDecisionHandler = (threadId: string, decision: McpToolDecision, gateId: string) => Promise<void>;
  ```
- in the message switch, pass the gate id (webview messages from an older cached bundle carry none → `""`, which the controller turns into "no gate_id"):
  - `p = this.onCommandDecision(m["taskId"] as string, decision, typeof m["gateId"] === "string" ? m["gateId"] : "");`
  - the `mcpDecision` branch: add a third argument `typeof m["gateId"] === "string" ? m["gateId"] : ""`;
  - the `editDecision` branch: `p = this.onEditDecision(m["threadId"] as string, decision, (m["reason"] as string) ?? "", typeof m["gateId"] === "string" ? m["gateId"] : "");`
- replace `renderLiveGate`/`clearLiveGate` with:
  ```ts
    // Every pending gate as a list (spec §4.5); an empty list clears the gate cards.
    renderLiveGates(gates: LiveGateView[]): void {
      this.panel?.webview.postMessage({ type: "renderLiveGates", gates });
    }

    clearLiveGates(): void {
      this.panel?.webview.postMessage({ type: "renderLiveGates", gates: [] });
    }
  ```

`extension.ts`:
- `(taskId, decision) => controller.handleCommandDecisionFromChat(taskId, decision),` → `(taskId, decision, gateId) => controller.handleCommandDecisionFromChat(taskId, decision, gateId),`
- `(threadId, decision, reason) => controller.handleEditDecisionFromChat(threadId, decision, reason),` → `(threadId, decision, reason, gateId) => controller.handleEditDecisionFromChat(threadId, decision, reason, gateId),`
- `(threadId, decision) => controller.handleMcpDecisionFromChat(threadId, decision),` → `(threadId, decision, gateId) => controller.handleMcpDecisionFromChat(threadId, decision, gateId),`
- the UI adapter: `renderLiveGate: (gate) => { chatPanel.renderLiveGate(gate); },` / `clearLiveGate: () => { chatPanel.clearLiveGate(); },` → `renderLiveGates: (gates) => { chatPanel.renderLiveGates(gates); },` / `clearLiveGates: () => { chatPanel.clearLiveGates(); },`.

- [ ] **Step 5: Webview**

`webview-ui/src/types.ts`:
- replace `LiveGateView` with
  ```ts
  export interface GateAgentView { id: string; label: string; name: string }

  export interface LiveGateView {
    gateId: string;
    kind: "command" | "scope" | "validation" | "step" | "mode" | "edit" | "clarify" | "mcp_tool" | "doc_write";
    taskId: string;
    payload: Record<string, unknown>;  // pending_* payload, snake_case
    agent?: GateAgentView | null;      // the sub-agent that raised it; absent/null = main
  }
  ```
- host→webview messages: replace `| { type: "renderLiveGate"; gate: LiveGateView }` and `| { type: "clearLiveGate" }` with `| { type: "renderLiveGates"; gates: LiveGateView[] }`;
- webview→host messages: add `gateId: string;` to the `commandDecision`, `mcpDecision` and `editDecision` variants;
- `AppState`: `liveGate: LiveGateView | null;` → `liveGates: LiveGateView[];`.

`hooks/useAppState.ts`: initial state `liveGate: null,` → `liveGates: [],`; replace the two reducer cases with
```ts
    case "renderLiveGates":
      return { ...state, liveGates: msg.gates };
```

`inputAvailability.ts`: in the `Pick<...>` and the destructuring, `liveGate` → `liveGates`; add below the destructuring
```ts
  const hasGate = (kind: LiveGateView["kind"]) => liveGates.some((g) => g.kind === kind);
```
(import `type LiveGateView` from `./types` alongside `AppState`), and change `liveGate?.kind === "edit"` / `"mode"` / `"clarify"` to `hasGate("edit")` / `hasGate("mode")` / `hasGate("clarify")`. The row order (edit, then mode, then clarify) is unchanged.

`components/ThreadView.tsx`: `liveGate={state.liveGate}` → `liveGates={state.liveGates}`.

`components/LiveSlot.tsx`:
- import `GateAgentView` with the other types;
- replace `GateDispatchProps` and `GateDispatch` with:
  ```tsx
  interface GateDispatchProps {
    gateId: string;
    taskId: string;
    kind: LiveGateView["kind"];
    payload: Record<string, unknown>;
    agent: GateAgentView | null;
  }

  /** Routes a live gate to its card; a sub-agent's gate gets an agent chip above it. */
  function GateDispatch({ agent, ...card }: GateDispatchProps) {
    if (agent === null) return <GateCard {...card} />;
    return (
      <div className="flex flex-col gap-1">
        <span
          className="self-start px-1.5 py-0.5 rounded text-[10px] text-text-3 bg-surface-2 border border-border"
          title={`Raised by sub-agent ${agent.label} (${agent.name})`}
        >
          {agent.label} · {agent.name}
        </span>
        <GateCard {...card} />
      </div>
    );
  }

  function GateCard({ gateId, taskId, kind, payload }: Omit<GateDispatchProps, "agent">) {
    switch (kind) {
      case "command":
        return <CommandGate gateId={gateId} taskId={taskId} payload={payload} />;
      case "scope":
        return <ScopeGate taskId={taskId} payload={payload} />;
      case "validation":
        return <ValidationGate taskId={taskId} payload={payload} />;
      case "step":
        return <StepGate taskId={taskId} payload={payload} />;
      case "mode":
        return <ModeGate taskId={taskId} payload={payload} />;
      case "clarify":
        return <ClarifyGate taskId={taskId} payload={payload} />;
      case "edit":
        return <EditGate gateId={gateId} taskId={taskId} payload={payload} />;
      case "mcp_tool":
        return <McpGate gateId={gateId} taskId={taskId} payload={payload} />;
    }
  }

  /** Content-addressed key: a new decision for a stable id (task gates are
   * "task:{id}:{kind}") must REMOUNT the card to discard the previous resolved state. */
  function gateKey(gate: LiveGateView): string {
    const p = JSON.stringify(gate.payload);
    return `${gate.gateId}:${p.length.toString(36)}.${sig(p)}`;
  }
  ```
  (If `GateCard`'s switch lacks a `"doc_write"` case and TS reports "not all code paths return a value", add `default: return null;` — `doc_write` is a stale kind with no card.)
- `Props`: `liveGate: LiveGateView | null;` → `liveGates: LiveGateView[];`; destructure `liveGates`; `hasContent` uses `liveGates.length > 0` in place of `liveGate !== null`; update the docstring's "at most one gate card" to "one card per pending gate";
- replace the `{liveGate !== null && ( ... )}` block with:
  ```tsx
        {liveGates.map((gate) => (
          // Key stability relies on consistent key insertion order: both SSE and /live
          // payloads pass through JSON.parse, so V8 preserves the backend serializer's order.
          <GateDispatch
            key={gateKey(gate)}
            gateId={gate.gateId}
            taskId={gate.taskId}
            kind={gate.kind}
            payload={gate.payload}
            agent={gate.agent ?? null}
          />
        ))}
  ```

Gate cards — add `gateId: string;` to each `Props` and post it:
- `CommandGate.tsx`: destructure `{ gateId, taskId, payload }`; add `gateId,` after `taskId,` in the two multi-line `postMessage` objects, and `vscode.postMessage({ type: "commandDecision", taskId, gateId, approve: false });` for reject.
- `McpGate.tsx`: destructure `{ gateId, taskId, payload }`; `vscode.postMessage({ type: "mcpDecision", threadId: taskId, gateId, approve, remember });`.
- `EditGate.tsx`: destructure `{ gateId, taskId, payload }`; add `gateId` to both `editDecision` messages (`{ type: "editDecision", threadId: taskId, gateId, decision: "accept", reason: "" }` and the reject one).

- [ ] **Step 6: Migrate the existing tests**

Run from `apps/vscode-extension`:

```bash
perl -0pi -e 's/renderLiveGate: \(\) => \{\},\n(\s*)clearLiveGate: \(\) => \{\},/renderLiveGates: () => {},\n$1clearLiveGates: () => {},/; s/pendingGate: null,(?!\s*pendingGates)/pendingGate: null, pendingGates: [],/g' test/controller.test.ts
perl -pi -e 's/liveGate: null,/liveGates: [],/g' webview-ui/src/components/ThreadView.test.tsx webview-ui/src/test/assembly.test.tsx
perl -pi -e 's/liveGate=\{null\}/liveGates={[]}/g; s/liveGate=\{(baseGate|newGate)\}/liveGates={[$1]}/g; s/liveGate: null/liveGates: []/g' webview-ui/src/test/views.test.tsx
perl -0pi -e 's/<CommandGate(\s+)taskId=/<CommandGate$1gateId="g1"$1taskId=/g; s/(type: "commandDecision",\n(\s*))taskId: "task-cmd",/$1gateId: "g1",\n$2taskId: "task-cmd",/g' webview-ui/src/test/gates.test.tsx
perl -0pi -e 's/<(EditGate|McpGate)(\s+)taskId=/<$1$2gateId="g1"$2taskId=/g; s/(type: "editDecision",\n(\s*))threadId: "thread-1",/$1gateId: "g1",\n$2threadId: "thread-1",/g; s/type: "mcpDecision", threadId: "th1",/type: "mcpDecision", threadId: "th1", gateId: "g1",/g' webview-ui/src/test/gates.test.tsx
perl -pi -e 's/<EditGate taskId="t1"/<EditGate gateId="g1" taskId="t1"/g; s/type: "editDecision", threadId: "t1",/type: "editDecision", threadId: "t1", gateId: "g1",/g' webview-ui/src/components/messages/gates/EditGate.test.tsx
```

Then the hand edits:

- `test/controller.test.ts`:
  - the stub backend's `postEditDecision`/`postChatCommandDecision` gain a trailing `_gateId?: string` parameter;
  - `test("pollThreadLiveState renders one gate card, ...")`: `const gateRenders: LiveGateView[][] = [];`, the UI overrides become `renderLiveGates: (gates) => { gateRenders.push(gates); },` / `clearLiveGates: () => { gateClears += 1; },`; the poll #1 response gets `pendingGates: [{ gateId: "task:task-1:command", kind: "command", payload: { command: "pytest" }, agent: null }],` (keep `pendingGate` as its own line); the three `gateRenders[0].…` assertions become `gateRenders[0][0].…`;
  - `test("handleEditDecisionFromChat posts ...")`: the stub records `gateId` (`postEditDecision: async (threadId, decision, reason, gateId) => { editCalls.push({ threadId, decision, reason: reason ?? "", gateId }); }`, widen `editCalls`' element type with `gateId?: string`), call `handleEditDecisionFromChat("thread-1", "accept", "looks good", "g-edit")`, expect `{ threadId: "thread-1", decision: "accept", reason: "looks good", gateId: "g-edit" }`;
  - `test("handleCommandDecisionFromChat posts the decision to the backend")`: add the third argument `"task:task-1:command"`;
  - `test("handleCommandDecisionFromChat routes a controller gate (no task) ...")`: its `liveResponse` gets `pendingGates: [{ gateId: "g-cmd", kind: "command", payload: { command: "rm", args: ["-rf", "x"] }, agent: null }],`; the chat stub records the id (`postChatCommandDecision: async (threadId, decision, gateId) => { chatSent.push({ threadId, decision, gateId }); }`, element type gains `gateId?: string`); call with the third argument `"g-cmd"`; expect `[{ threadId: "thread-9", decision: {...unchanged}, gateId: "g-cmd" }]`. Update its leading comment: routing is now by gate id, not by `activeTaskId`.
  - the rewind test (`reloads the thread and prefills the composer after a rewind`) mocks `getThreadLiveState: async () => ({}),` — the old `live.pendingGate?.kind` tolerated that, `live.pendingGates.some(...)` does not (an unhandled rejection that fails the run): change it to `getThreadLiveState: async () => ({ pendingGates: [] }),`.
- `webview-ui/src/test/views.test.tsx`: add `gateId: "task:t1:command",` to `baseGate`.
- `webview-ui/src/inputAvailability.test.ts`: `import type { LiveGateView } from "./types";`; `base` uses `liveGates: [] as LiveGateView[],` (the outer `as const` would otherwise make it a readonly tuple); the two existing gate cases become `liveGates: [{ gateId: "g", kind: "edit", taskId: "t", payload: {} }]` / `kind: "mode"`.

Check nothing was missed:

Run: `grep -rnE "liveGate\b|renderLiveGate\b|clearLiveGate\b|liveGate=|liveGate:" apps/vscode-extension/src apps/vscode-extension/test apps/vscode-extension/webview-ui/src`
Expected: no output.

Run: `grep -rn "type: \"commandDecision\"" apps/vscode-extension/webview-ui/src/test/gates.test.tsx | wc -l; grep -rn 'gateId: "g1"' apps/vscode-extension/webview-ui/src/test/gates.test.tsx | wc -l`
Expected: the two counts are equal (every `commandDecision` expectation carries the id). `gates.test.tsx` also renders `EditGate` and `McpGate` — the second perl line above covers them; `npx tsc --noEmit` in Step 7 is what catches a missed `gateId` prop (vitest alone does not: an `undefined` `gateId` still satisfies `toHaveBeenCalledWith`).

- [ ] **Step 7: Run everything**

Run: `npm run -w crucible-vscode-extension typecheck > /tmp/p7t.txt 2>&1; echo exit=$?`
Expected: `exit=0`.

Run: `(cd apps/vscode-extension/webview-ui && npx tsc --noEmit > /tmp/p7wt.txt 2>&1; echo exit=$?)`
Expected: `exit=0`.

Run: `npm run -w crucible-vscode-extension test > /tmp/p7.txt 2>&1; echo exit=$?; tail -4 /tmp/p7.txt`
Expected: `exit=0`.

Run: `(cd apps/vscode-extension/webview-ui && npx vitest run > /tmp/p7w.txt 2>&1; echo exit=$?; tail -4 /tmp/p7w.txt)`
Expected: `exit=0`.

Run: `npm run build > /tmp/p7b.txt 2>&1; echo exit=$?`
Expected: `exit=0` (also rebuilds `webview-ui/dist` — a stale bundle would keep posting gate-less decisions).

- [ ] **Step 8: Commit**

```bash
git add apps/vscode-extension
git commit -m "feat(chat-ui): render every pending gate and address decisions by gate id"
```

---

### Task 8: Remove the legacy single-gate field; document multi-gate

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py`, `agentd/chat/live_state.py`, `agentd/api/routes.py` (docstring)
- Modify: `services/agentd-py/tests/gate_helpers.py`, `tests/test_chat_live_route.py`, `tests/test_thread_live_state.py`, `tests/test_controller_live_gate.py`, `tests/test_controller_todo_live.py`, `tests/test_controller_gate_list.py`
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `src/client/http-backend-client.ts`, `test/http-backend-client.test.ts`; `apps/vscode-extension/test/controller.test.ts`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Backend**

- `ThreadLiveState`: delete the `pending_gate` field and its LEGACY comment.
- `live_state.py`: in `resolve_live_state` delete the `pending_gate=gate,` argument; in `resolve_thread_live` delete `pending_gate=gates[0],`.
- `routes.py` `get_thread_live` docstring: `(status + one active gate + plan)` → `(status + every pending gate + plan)`.

Add to `tests/gate_helpers.py`:

```python
def first_live_gate(live: ThreadLiveState) -> PendingGate | None:
    return live.pending_gates[0] if live.pending_gates else None
```

(and import `ThreadLiveState` next to `ChatThread`). Then from `services/agentd-py`:

```bash
.venv/bin/python - <<'PY'
import ast
import re
from pathlib import Path

IMPORT = "from tests.gate_helpers import first_live_gate\n"
for name in ["test_thread_live_state.py", "test_controller_live_gate.py",
             "test_controller_todo_live.py"]:
    path = Path("tests") / name
    src = path.read_text()
    out, n = re.subn(r"\b(\w+)\.pending_gate\b(?!s)", r"first_live_gate(\1)", src)
    if n and "from tests.gate_helpers import first_gate\n" in out:
        # Task 2 already imported first_gate here — extend that line instead.
        out = out.replace("from tests.gate_helpers import first_gate\n",
                          "from tests.gate_helpers import first_gate, first_live_gate\n")
    elif n and IMPORT not in out:
        tree = ast.parse(out)
        end = max(node.end_lineno for node in tree.body
                  if isinstance(node, (ast.Import, ast.ImportFrom)))
        lines = out.splitlines(keepends=True)
        lines.insert(end, IMPORT)
        out = "".join(lines)
    path.write_text(out)
    print(name, n)
PY
perl -pi -e 's/body\["pending_gate"\] is None/body["pending_gates"] == []/; s/body\["pending_gate"\]\[/body["pending_gates"][0][/' tests/test_chat_live_route.py
```

Expected output: three lines, each with a non-zero count.

In `tests/test_controller_gate_list.py`, delete the legacy assertions: in `test_live_state_carries_the_gate_list_and_the_legacy_first_gate` remove the `live.pending_gate` assertion and the `dumped["pending_gate"]` line and rename it `test_live_state_carries_the_gate_list`; in `test_no_gates_means_an_empty_list_and_no_legacy_gate` change the assertion to `assert live.pending_gates == []` and rename it `test_no_gates_means_an_empty_list`; in `test_task_gates_carry_a_synthetic_task_id` remove the `live.pending_gate` line. Add:

```python
def test_live_state_no_longer_ships_the_legacy_single_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    store.add_controller_gate(tid, PendingGate.new("command", {}))
    dumped = resolve_thread_live(store.get_thread(tid), None, _no_task).model_dump(mode="json")
    assert "pending_gate" not in dumped and len(dumped["pending_gates"]) == 1
```

Run: `grep -rnIE "pending_gate\b" services/agentd-py/agentd services/agentd-py/tests | grep -v "not in dumped"`
Expected: no output.

- [ ] **Step 2: editor-client + extension tests**

- `task-contracts.ts`: delete the `pendingGate` field and its LEGACY comment from `ThreadLiveStateSchema`.
- `http-backend-client.ts` `getThreadLiveState`: delete the `legacyGate` constant and the `pendingGate:` entry.
- `test/http-backend-client.test.ts`: delete the `pending_gate: {...}` raw field and the `expect(live.pendingGate?.gateId)...` line added in Task 6; in the idle-thread test delete `expect(live.pendingGate).toBeNull();`.
- `apps/vscode-extension/test/controller.test.ts`: `perl -pi -e 's/pendingGate: null, //g; s/ pendingGate: null,$//; s/^\s*pendingGate: null,\n//; s/^\s*pendingGate: \{ kind: .*\},\n//' test/controller.test.ts` (run from `apps/vscode-extension`), then `grep -n "pendingGate\b" test/controller.test.ts` — Expected: no output.

- [ ] **Step 3: CLAUDE.md**

Replace the "**Class-A live gates:**" bullet's first sentence — exactly `- **Class-A live gates:** the active gate is on the thread as \`pending_controller_gate\` and surfaced by \`GET /v1/chat/threads/{id}/live\` → \`{turn_active, pending_gate:{kind: mode|edit|clarify|command, payload}, status, plan, failure_summary, run_summary, task_narrative}\`.` — with:

```markdown
- **Class-A live gates:** a thread holds a LIST of pending gates (`ChatThread.pending_controller_gates`, spec §4.5 of the sub-agents design — sub-agents raise gates concurrently), each with a stable `gate_id` (uuid for controller gates; `task:{task_id}:{kind}` for task-derived gates; never a pydantic `default_factory` — the `ChatMessage.id` lesson) and an optional `agent: {id, label, name}`. Surfaced by `GET /v1/chat/threads/{id}/live` → `{turn_active, pending_gates:[{gate_id, kind: mode|edit|clarify|command|mcp_tool, payload, agent}], status, plan, failure_summary, run_summary, task_narrative}`. Decision futures are keyed by `gate_id`; `/edit-decision`, `/command-decision`, `/mcp-decision` take an optional `gate_id` — unknown → **404**, omitted with several of that kind pending → **409**, omitted with one → that gate (the frontend always sends it and treats 404/409 as benign). Mutations are `add_controller_gate`/`remove_controller_gate`/`clear_controller_gates(agent_id=…)` (sync read-modify-write, no `await`).
```

(keep the rest of that bullet — "Same render-from-`/live`…" onward — unchanged). After the MCP client section's "**Gate:**" bullet (and its indented continuation lines — i.e. directly before the "**Prompt:**" bullet) add:

```markdown
- **Truthful denials:** command and MCP approval callbacks return the server-internal `ApprovalOutcome{approved, denied_by: user|policy|timeout, decision}` (`domain/models.py`; never a request body, so a client cannot claim `policy`). `tools/approvals.py::denial_text` words each case — the user-rejection strings are unchanged; a timeout says "No decision arrived in time; … was not run."
```

In the "Live cards, gates & breadcrumbs" section's `/live` dedup-signature paragraph, change `gate` in the signature field list to `gates`.

- [ ] **Step 4: Run everything**

Run (from `services/agentd-py`): `.venv/bin/python -m pytest tests/ --color=no > /tmp/p8.txt 2>&1; echo exit=$?; grep '^FAILED' /tmp/p8.txt`
Expected: `exit=1`, only the pre-existing `test_command_only_step` failure.

Run (repo root): `npm run -w @crucible/editor-client build > /tmp/p8e.txt 2>&1 && npm run typecheck > /tmp/p8t.txt 2>&1 && npm run test > /tmp/p8v.txt 2>&1; echo exit=$?`
Expected: `exit=0`.

Run: `(cd apps/vscode-extension/webview-ui && npx tsc --noEmit && npx vitest run > /tmp/p8w.txt 2>&1; echo exit=$?)`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py apps/editor-client apps/vscode-extension/test CLAUDE.md
git commit -m "refactor(chat): drop the legacy single pending_gate field"
```

---

### Final verification (manual, live)

The unit suites cannot see the webview↔host message wiring end to end. Run one live pass (recipe: memory `smoke_env_devhost_recipe`, CDP driving: `smoke_controller_cdp_driving_recipe`):

1. `bash scripts/stress/start-backend.sh --backend <provider> --workspace "<abs path>" --validation-profile none` with `CRUCIBLE_SHELL_POLICY=ask`; open the dev host on the same workspace (`npm run build` first).
2. Ask for a change that edits a file and then runs a command (e.g. "add a docstring to X and run its tests"). Confirm, in order: the EditGate card renders; Accept promotes; the CommandGate card renders; Reject shows "Command rejected by user: …" in the tool pill output (byte-identical wording).
3. Reload the webview while a CommandGate is pending: the card re-renders from `/live` and Allow once resolves it (the `gate_id` survived the reload).
4. `curl -s -X POST localhost:8000/v1/chat/threads/<id>/command-decision -H 'content-type: application/json' -d '{"approve":true,"gate_id":"nope"}' -w '%{http_code}\n'` → `404`.
5. With `CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC=5`, let a command gate time out: the tool pill reads "No decision arrived in time; command `…` was not run." (not "by user").

Record the outcome of each step in the PR description.
