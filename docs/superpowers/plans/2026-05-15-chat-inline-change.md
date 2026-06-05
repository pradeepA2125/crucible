# Chat Inline Change Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `small_change` and `large_change` paths end-to-end from the chat window, using a single `EventBroadcaster` mechanism for all streaming throughout the framework.

**Architecture:** `PatchEventBroadcaster` is renamed to `EventBroadcaster` and keyed by a generic `channel_id` (not just `task_id`), making it the single streaming mechanism for chat events, explore phase, ToolLoop events, and status transitions. `ChatAgent.handle_message()` becomes a plain async coroutine that pushes to the broadcaster; the chat message route background-tasks it and returns an SSE subscription — identical to how `/stream-patch` works today. All broadcast events migrate to `{"type": ..., "payload": {...}}` format for a single TypeScript union type.

**Tech Stack:** Python (asyncio, FastAPI, Pydantic), TypeScript (Zod, Vitest), pytest-asyncio

---

## File Map

**Modified — Python backend**
- `services/agentd-py/agentd/orchestrator/broadcaster.py` — rename `PatchEventBroadcaster` → `EventBroadcaster`, `task_id` → `channel_id`
- `services/agentd-py/agentd/domain/models.py` — add `DiffEntry`, `InlineChangeResult`; extend `TaskRecord`, `TaskCreateRequest`
- `services/agentd-py/agentd/tools/loop.py` — `{type,payload}` event format; `broadcast_key` param; `skip_verify` flag
- `services/agentd-py/agentd/planning/loop.py` — `{type,payload}` event format
- `services/agentd-py/agentd/orchestrator/engine.py` — `{type,payload}` for `done`/scope events; add `run_inline_change()`
- `services/agentd-py/agentd/patch/engine.py` — `{type,payload}` for `operation_success`/`operation_error`
- `services/agentd-py/agentd/workspace/shadow.py` — add `prepare_lightweight()`
- `services/agentd-py/agentd/planning/prompts.py` — add `initial_explore_context` support
- `services/agentd-py/agentd/chat/agent.py` — broadcaster coroutine pattern; `_draft_plan_markdown()`
- `services/agentd-py/agentd/api/routes.py` — background-task chat route; promote/discard endpoints
- `services/agentd-py/agentd/main.py` — pass shared broadcaster to `ChatAgent`
- `services/agentd-py/agentd/chat/app_factory.py` — pass shared broadcaster to `ChatAgent`

**Modified — TypeScript**
- `apps/editor-client/src/contracts/task-contracts.ts` — `StreamEvent` union (replaces `PatchStreamEvent`); `DiffEntry`; new client methods
- `apps/editor-client/src/client/http-backend-client.ts` — `applyInlineChange`, `discardInlineChange`
- `apps/vscode-extension/src/controller.ts` — `diff_ready` / `task_card` handling; `applyInlineChange`/`discardInlineChange`; `ControllerUI` extensions

**Created — Tests**
- `services/agentd-py/tests/test_event_broadcaster.py`
- `services/agentd-py/tests/test_tool_loop_event_format.py`
- `services/agentd-py/tests/test_tool_loop_skip_verify.py`
- `services/agentd-py/tests/test_shadow_lightweight.py`
- `services/agentd-py/tests/test_chat_agent_broadcaster.py`
- `services/agentd-py/tests/test_orchestrator_inline_change.py`
- `services/agentd-py/tests/test_orchestrator_large_change_chat.py`

---

### Task 1: Rename `PatchEventBroadcaster` → `EventBroadcaster`

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/broadcaster.py`
- Create: `services/agentd-py/tests/test_event_broadcaster.py`

- [ ] **Step 1: Write failing tests**

```python
# services/agentd-py/tests/test_event_broadcaster.py
from __future__ import annotations
import asyncio
import pytest
from agentd.orchestrator.broadcaster import EventBroadcaster, PatchEventBroadcaster

def test_patch_event_broadcaster_is_alias():
    assert PatchEventBroadcaster is EventBroadcaster

@pytest.mark.asyncio
async def test_broadcast_to_chat_channel_id():
    b = EventBroadcaster()
    queue = b.subscribe("chat:abc123")
    b.broadcast("chat:abc123", {"type": "chat_done", "payload": {}})
    event = queue.get_nowait()
    assert event == {"type": "chat_done", "payload": {}}
    b.unsubscribe("chat:abc123", queue)

@pytest.mark.asyncio
async def test_replay_buffer_on_late_subscribe():
    b = EventBroadcaster()
    b.broadcast("chan1", {"type": "e1", "payload": {}})
    b.broadcast("chan1", {"type": "e2", "payload": {}})
    queue = b.subscribe("chan1")
    assert [queue.get_nowait()["type"], queue.get_nowait()["type"]] == ["e1", "e2"]

@pytest.mark.asyncio
async def test_multiple_channels_are_isolated():
    b = EventBroadcaster()
    q1 = b.subscribe("chan1")
    q2 = b.subscribe("chan2")
    b.broadcast("chan1", {"type": "x", "payload": {}})
    assert not q1.empty()
    assert q2.empty()

def test_clear_replay_empties_buffer():
    b = EventBroadcaster()
    b.broadcast("chan", {"type": "e1", "payload": {}})
    b.clear_replay("chan")
    queue = b.subscribe("chan")
    assert queue.empty()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && pytest tests/test_event_broadcaster.py -v
```

Expected: `ImportError: cannot import name 'EventBroadcaster'`

- [ ] **Step 3: Rename the class and add backward-compat alias**

Replace the entire content of `services/agentd-py/agentd/orchestrator/broadcaster.py`:

```python
"""General-purpose SSE event broadcaster keyed by channel_id.

Replaces the old PatchEventBroadcaster (which was keyed by task_id only).
All existing callers that pass task_id still work — task_id is a valid channel_id.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

_REPLAY_BUFFER_SIZE = 50


class EventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._replay: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=_REPLAY_BUFFER_SIZE)
        )

    def subscribe(self, channel_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self._replay[channel_id]:
            queue.put_nowait(event)
        self._subscribers[channel_id].add(queue)
        return queue

    def unsubscribe(self, channel_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(channel_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(channel_id, None)

    def broadcast(self, channel_id: str, event: dict[str, Any]) -> None:
        self._replay[channel_id].append(event)
        for queue in self._subscribers.get(channel_id, set()):
            queue.put_nowait(event)

    def clear_replay(self, channel_id: str) -> None:
        self._replay.pop(channel_id, None)


# Backward-compat alias — all existing callers using task_id as channel_id still compile.
PatchEventBroadcaster = EventBroadcaster
```

- [ ] **Step 4: Run tests — all pass**

```bash
cd services/agentd-py && pytest tests/test_event_broadcaster.py -v
```

Expected: 5 passed

- [ ] **Step 5: Verify nothing broke in existing tests**

```bash
cd services/agentd-py && pytest --tb=short -q
```

Expected: all existing tests still pass (alias preserves all call sites)

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/orchestrator/broadcaster.py \
        services/agentd-py/tests/test_event_broadcaster.py
git commit -m "feat(broadcaster): generalize PatchEventBroadcaster → EventBroadcaster with channel_id key"
```

---

### Task 2: Domain model additions

**Files:**
- Modify: `services/agentd-py/agentd/domain/models.py`

- [ ] **Step 1: Add `DiffEntry`, `InlineChangeResult`, and extend `TaskRecord` / `TaskCreateRequest`**

In `services/agentd-py/agentd/domain/models.py`, add after the existing imports (after line 8, `from pydantic import ...`):

```python
from dataclasses import dataclass
```

Add after the `TaskArtifactsResponse` class at the bottom of the file:

```python
@dataclass
class DiffEntry:
    path: str
    additions: int
    deletions: int
    temp_path: str


@dataclass
class InlineChangeResult:
    task_id: str
    diff_entries: list[DiffEntry]
    plan_document: dict[str, Any]
```

In `TaskRecord`, add three new fields after `artifacts_root_path`:

```python
is_inline_change: bool = False
chat_channel_id: str | None = None
initial_explore_context: list[dict[str, object]] | None = None
```

In `TaskCreateRequest`, add one optional field after `budget`:

```python
initial_explore_context: list[dict[str, object]] | None = None
```

- [ ] **Step 2: Verify serialisation round-trips**

```bash
cd services/agentd-py && python -c "
from agentd.domain.models import DiffEntry, InlineChangeResult, TaskRecord, TaskCreateRequest
e = DiffEntry(path='a.py', additions=3, deletions=1, temp_path='/tmp/a.py')
print(e)
r = TaskRecord(task_id='t1', goal='g', workspace_path='/ws', is_inline_change=True, chat_channel_id='chat:x', initial_explore_context=[{'tool': 'read_file'}])
print(r.is_inline_change, r.chat_channel_id, r.initial_explore_context)
req = TaskCreateRequest(goal='g', workspace_path='/ws', initial_explore_context=[{'tool': 'read_file', 'result': 'x'}])
print(req.initial_explore_context)
"
```

Expected: values print correctly, no errors.

- [ ] **Step 3: Run full test suite to confirm no regressions**

```bash
cd services/agentd-py && pytest --tb=short -q
```

- [ ] **Step 4: Commit**

```bash
git add services/agentd-py/agentd/domain/models.py
git commit -m "feat(models): add DiffEntry, InlineChangeResult; extend TaskRecord and TaskCreateRequest for inline changes"
```

---

### Task 3: Migrate event format to `{type, payload}` — Python

**Files:**
- Modify: `services/agentd-py/agentd/tools/loop.py`
- Modify: `services/agentd-py/agentd/planning/loop.py`
- Modify: `services/agentd-py/agentd/orchestrator/engine.py`
- Modify: `services/agentd-py/agentd/patch/engine.py`
- Create: `services/agentd-py/tests/test_tool_loop_event_format.py`

- [ ] **Step 1: Write a failing test that asserts the new event shape**

```python
# services/agentd-py/tests/test_tool_loop_event_format.py
from __future__ import annotations
import asyncio
import pytest
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.domain.models import PlanStep, TaskBudget, TaskUsage

class _ToolCallEngine:
    """Scripted engine: one tool_call then emit_patch."""
    async def create_tool_step(self, step_context, history, tool_definitions):
        if not history:
            return {"type": "tool_call", "thought": "t", "tool": "read_file", "args": {"path": "a.py"}}
        if len(history) == 2:  # after tool_result
            return {
                "type": "emit_patch",
                "thought": "p",
                "patch_ops": [{"op": "search_replace", "file": "a.py", "search": "x", "replace": "y", "reason": "r"}],
            }
        return {"type": "verify_done", "thought": "v", "verified": True, "test_output": ""}

    async def create_planning_step(self, *args, **kwargs):
        return {}

    async def create_plan(self, *args, **kwargs):
        return {}


@pytest.mark.asyncio
async def test_tool_call_event_uses_payload_envelope(tmp_path):
    from agentd.tools.loop import ToolLoop, build_tool_registry
    from agentd.patch.engine import PatchEngine

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n")

    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe("task-t1")

    from agentd.tools.registry import ToolRegistry
    registry = ToolRegistry(shadow_root=ws, real_workspace_path=ws)
    loop = ToolLoop(
        _ToolCallEngine(), registry, broadcaster, "task-t1",
        patch_engine=PatchEngine(), shadow_path=ws,
    )
    step = PlanStep(
        id="S1", goal="g",
        targets=[{"path": "a.py", "intent": "existing"}],
        risk="low",
    )
    await loop.run(step, {}, TaskBudget(), TaskUsage())

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    tool_call_events = [e for e in events if e["type"] == "tool_call"]
    assert tool_call_events, "expected at least one tool_call event"
    evt = tool_call_events[0]
    assert "payload" in evt, f"event missing 'payload' key: {evt}"
    assert "tool" in evt["payload"], f"payload missing 'tool': {evt['payload']}"
    assert "tool" not in evt or evt.get("tool") is None, \
        "tool field should be inside payload, not at top level"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd services/agentd-py && pytest tests/test_tool_loop_event_format.py -v
```

Expected: `AssertionError: event missing 'payload' key`

- [ ] **Step 3: Migrate `tools/loop.py` — wrap all broadcast payloads**

In `services/agentd-py/agentd/tools/loop.py`, apply these replacements:

**`tool_call` event (around line 428):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "tool_call", "tool": tool_name,
    "thought": thought[:300], "iteration": iteration + 1, "phase": phase,
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "tool_call",
    "payload": {"tool": tool_name, "thought": thought[:300], "iteration": iteration + 1, "phase": phase},
})
```

**`tool_result` event (around line 449):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "tool_result", "tool": tool_name,
    "output": tool_output.output[:500], "is_error": tool_output.is_error,
    "iteration": iteration + 1,
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "tool_result",
    "payload": {"tool": tool_name, "output": tool_output.output[:500], "is_error": tool_output.is_error, "iteration": iteration + 1},
})
```

**`revision_needed` event — first occurrence (around line 228):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "revision_needed", "step_id": step.id,
    "reason": reason, "evidence": evidence[:300],
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "revision_needed",
    "payload": {"step_id": step.id, "reason": reason, "evidence": evidence[:300]},
})
```

**`revision_needed` event — second occurrence (around line 389):** same pattern.

**`patch_failed` — three occurrences (around lines 287, 306, 320):**
```python
# BEFORE (each instance):
self._broadcaster.broadcast(self._task_id, {
    "type": "patch_failed", "step_id": step.id, "error": <error_var>,
})
# AFTER (each instance):
self._broadcaster.broadcast(self._task_id, {
    "type": "patch_failed",
    "payload": {"step_id": step.id, "error": <error_var>},
})
```

**`patch_applied` event (around line 364):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "patch_applied", "step_id": step.id,
    "phase": "verify", "touched_files": all_touched_files,
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "patch_applied",
    "payload": {"step_id": step.id, "phase": "verify", "touched_files": all_touched_files},
})
```

- [ ] **Step 4: Migrate `planning/loop.py` — wrap all broadcast payloads**

In `services/agentd-py/agentd/planning/loop.py`:

**`planning_complete` event (around line 122):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "planning_complete",
    "files_examined": files_examined,
    "confidence": confidence,
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "planning_complete",
    "payload": {"files_examined": files_examined, "confidence": confidence},
})
```

**`planning_tool_call` event (around line 177):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "planning_tool_call",
    "tool": tool_name,
    "thought": thought[:300],
    "iteration": iteration + 1,
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "planning_tool_call",
    "payload": {"tool": tool_name, "thought": thought[:300], "iteration": iteration + 1},
})
```

**`planning_tool_result` event (around line 186):**
```python
# BEFORE:
self._broadcaster.broadcast(self._task_id, {
    "type": "planning_tool_result",
    "tool": tool_name,
    "output": tool_output.output[:500],
    "is_error": tool_output.is_error,
    "iteration": iteration + 1,
})
# AFTER:
self._broadcaster.broadcast(self._task_id, {
    "type": "planning_tool_result",
    "payload": {"tool": tool_name, "output": tool_output.output[:500], "is_error": tool_output.is_error, "iteration": iteration + 1},
})
```

- [ ] **Step 5: Migrate `engine.py` — `done` and `scope_extension_requested`**

In `services/agentd-py/agentd/orchestrator/engine.py`, all four `done` broadcasts:
```python
# BEFORE:
self.broadcaster.broadcast(task_id, {"type": "done"})
# AFTER (all 4 occurrences):
self.broadcaster.broadcast(task_id, {"type": "done", "payload": {}})
```

The `scope_extension_requested` broadcast (around line 794):
```python
# BEFORE:
self.broadcaster.broadcast(task_id, {
    "type": "scope_extension_requested",
    "decision_id": decision_id,
    "files": truly_new,
    "reason": reason,
    "step_id": step_id,
})
# AFTER:
self.broadcaster.broadcast(task_id, {
    "type": "scope_extension_requested",
    "payload": {"decision_id": decision_id, "files": truly_new, "reason": reason, "step_id": step_id},
})
```

- [ ] **Step 6: Migrate `patch/engine.py` — `on_patch_event` callback output**

In `services/agentd-py/agentd/patch/engine.py`, find the two `on_patch_event` calls (around lines 1004 and 1014):

```python
# BEFORE:
on_patch_event({"type": "operation_success", "op_type": operation.op, "path": operation.file})
# AFTER:
on_patch_event({"type": "operation_success", "payload": {"op_type": operation.op, "path": operation.file}})

# BEFORE:
on_patch_event({"type": "operation_error", "op_type": operation.op, "path": operation.file, "error": str(exc)})
# AFTER:
on_patch_event({"type": "operation_error", "payload": {"op_type": operation.op, "path": operation.file, "error": str(exc)}})
```

- [ ] **Step 7: Run the format test — now passes**

```bash
cd services/agentd-py && pytest tests/test_tool_loop_event_format.py -v
```

Expected: 1 passed

- [ ] **Step 8: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

Expected: all tests pass

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/tools/loop.py \
        services/agentd-py/agentd/planning/loop.py \
        services/agentd-py/agentd/orchestrator/engine.py \
        services/agentd-py/agentd/patch/engine.py \
        services/agentd-py/tests/test_tool_loop_event_format.py
git commit -m "feat(events): migrate all broadcast events to {type, payload} envelope"
```

---

### Task 4: TypeScript — migrate `PatchStreamEvent` to nested payload format

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/vscode-extension/src/controller.ts`

- [ ] **Step 1: Replace `PatchStreamEvent` with `StreamEvent` in contracts**

In `apps/editor-client/src/contracts/task-contracts.ts`, find the `PatchStreamEvent` type definition and replace it:

```typescript
// Find and replace this block:
export type PatchStreamEvent =
  | { type: "operation_success"; op_type: string; path: string }
  | { type: "operation_error"; op_type: string; path: string; error: string }
  | { type: "scope_extension_requested"; decision_id: string; files: string[]; reason: string; step_id: string }
  | { type: "done" };

// With this:
export interface DiffEntry {
  path: string;
  additions: number;
  deletions: number;
  tempPath: string;
}

export type StreamEvent =
  | { type: "operation_success"; payload: { op_type: string; path: string } }
  | { type: "operation_error"; payload: { op_type: string; path: string; error: string } }
  | { type: "done"; payload: Record<string, never> }
  | { type: "tool_call"; payload: { tool: string; thought: string; iteration: number; phase: string } }
  | { type: "tool_result"; payload: { tool: string; output: string; is_error: boolean; iteration: number } }
  | { type: "planning_tool_call"; payload: { tool: string; thought: string; iteration: number } }
  | { type: "planning_tool_result"; payload: { tool: string; output: string; is_error: boolean; iteration: number } }
  | { type: "planning_complete"; payload: { files_examined: string[]; confidence: string } }
  | { type: "revision_needed"; payload: { step_id: string; reason: string; evidence: string } }
  | { type: "patch_applied"; payload: { step_id: string; phase: string; touched_files: string[] } }
  | { type: "patch_failed"; payload: { step_id: string; error: string } }
  | { type: "scope_extension_requested"; payload: { decision_id: string; files: string[]; reason: string; step_id: string } }
  | { type: "chat_agent_thinking"; payload: { message: string } }
  | { type: "explore_tool_call"; payload: { tool: string; args: Record<string, unknown> } }
  | { type: "intent_classified"; payload: { intent: string; rationale: string; likely_targets: string[] } }
  | { type: "chat_response"; payload: { chunk: string } }
  | { type: "chat_done"; payload: Record<string, never> }
  | { type: "task_card"; payload: { task_id: string } }
  | { type: "task_status_changed"; payload: { task_id: string; status: string; plan_markdown?: string } }
  | { type: "diff_ready"; payload: { task_id: string; diff_entries: DiffEntry[]; completed_steps: number; total_steps: number } };

// Backward-compat alias
export type PatchStreamEvent = StreamEvent;
```

Also update the `BackendTaskClient` interface to use `StreamEvent`:

```typescript
streamPatch(taskId: string, onEvent: (event: StreamEvent) => void, signal?: AbortSignal): Promise<void>;
streamPatchEvents(taskId: string): AsyncIterable<StreamEvent>;
sendChatMessage(threadId: string, message: string): AsyncIterable<StreamEvent>;
```

- [ ] **Step 2: Update controller to access nested `payload` fields**

In `apps/vscode-extension/src/controller.ts`, find `streamTaskIntoChatThread` and update the event field access:

```typescript
// Find these patterns and update:
// BEFORE:
if (event.type === "operation_success") {
  this.ui.appendChatMessage({ role: "agent", content: `✓ ${event.op_type}: ${event.path}`, type: "text" });
}
// AFTER:
if (event.type === "operation_success") {
  this.ui.appendChatMessage({ role: "agent", content: `✓ ${event.payload.op_type}: ${event.payload.path}`, type: "text" });
}

// BEFORE:
if (event.type === "operation_error") {
  this.ui.appendChatMessage({ role: "agent", content: `✗ ${event.op_type}: ${event.error}`, type: "text" });
}
// AFTER:
if (event.type === "operation_error") {
  this.ui.appendChatMessage({ role: "agent", content: `✗ ${event.payload.op_type}: ${event.payload.error}`, type: "text" });
}
```

Also update `sendChatMessage` handler if it accesses any payload fields from chat events (they were already nested, but `chat_response` chunk access):

```typescript
// BEFORE (if present):
else if (event.type === "chat_response") { this.ui.appendChatChunk(event.chunk) }
// AFTER:
else if (event.type === "chat_response") { this.ui.appendChatChunk(event.payload.chunk) }
```

- [ ] **Step 3: Run TypeScript typecheck**

```bash
npm run typecheck
```

Expected: no errors

- [ ] **Step 4: Run TypeScript tests**

```bash
npm run test
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client/src/contracts/task-contracts.ts \
        apps/vscode-extension/src/controller.ts
git commit -m "feat(contracts): migrate PatchStreamEvent to StreamEvent with nested payload envelope"
```

---

### Task 5: `ToolLoop` — `broadcast_key` and `skip_verify`

**Files:**
- Modify: `services/agentd-py/agentd/tools/loop.py`
- Create: `services/agentd-py/tests/test_tool_loop_skip_verify.py`

- [ ] **Step 1: Write failing test**

```python
# services/agentd-py/tests/test_tool_loop_skip_verify.py
from __future__ import annotations
import pytest
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.domain.models import PlanStep, TaskBudget, TaskUsage
from agentd.tools.loop import ToolLoop, VerifyResult


class _PatchOnlyEngine:
    """Emits a patch immediately, no verify_done needed."""
    async def create_tool_step(self, step_context, history, tool_definitions):
        if not history:
            return {
                "type": "emit_patch",
                "thought": "apply",
                "patch_ops": [{"op": "search_replace", "file": "f.py", "search": "old", "replace": "new", "reason": "r"}],
            }
        # If we reach here in skip_verify=False mode, enter verify
        return {"type": "verify_done", "thought": "v", "verified": True, "test_output": ""}

    async def create_planning_step(self, *a, **k): return {}
    async def create_plan(self, *a, **k): return {}


@pytest.mark.asyncio
async def test_skip_verify_returns_without_verify_phase(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("old code\n")

    from agentd.patch.engine import PatchEngine
    from agentd.tools.registry import ToolRegistry

    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe("chat:ch1")
    registry = ToolRegistry(shadow_root=ws, real_workspace_path=ws)

    loop = ToolLoop(
        _PatchOnlyEngine(), registry, broadcaster, "task-x",
        patch_engine=PatchEngine(), shadow_path=ws,
        broadcast_key="chat:ch1",
    )
    step = PlanStep(id="S1", goal="g", targets=[{"path": "f.py", "intent": "existing"}], risk="low")
    result = await loop.run(step, {}, TaskBudget(), TaskUsage(), skip_verify=True)

    assert isinstance(result, VerifyResult)
    assert result.verified is True
    # patch_applied event should be in chat channel (via broadcast_key), not task channel
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(e["type"] == "patch_applied" for e in events), \
        f"expected patch_applied in chat channel; got {[e['type'] for e in events]}"


@pytest.mark.asyncio
async def test_broadcast_key_routes_events_to_custom_channel(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "g.py").write_text("x\n")

    from agentd.patch.engine import PatchEngine
    from agentd.tools.registry import ToolRegistry

    broadcaster = EventBroadcaster()
    task_queue = broadcaster.subscribe("task-y")
    chat_queue = broadcaster.subscribe("chat:ch2")

    registry = ToolRegistry(shadow_root=ws, real_workspace_path=ws)
    loop = ToolLoop(
        _PatchOnlyEngine(), registry, broadcaster, "task-y",
        patch_engine=PatchEngine(), shadow_path=ws,
        broadcast_key="chat:ch2",
    )
    step = PlanStep(id="S1", goal="g", targets=[{"path": "g.py", "intent": "existing"}], risk="low")
    await loop.run(step, {}, TaskBudget(), TaskUsage(), skip_verify=True)

    # Events go to chat channel, not to task channel
    assert not task_queue.empty() is False or task_queue.qsize() == 0, \
        "events should not be in task channel when broadcast_key is set"
    assert not chat_queue.empty(), "events should be in chat channel"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && pytest tests/test_tool_loop_skip_verify.py -v
```

Expected: `TypeError` (unexpected keyword argument `broadcast_key` or `skip_verify`)

- [ ] **Step 3: Add `broadcast_key` to `ToolLoop.__init__`**

In `services/agentd-py/agentd/tools/loop.py`, update `__init__`:

```python
def __init__(
    self,
    reasoning_engine: ReasoningEngine,
    registry: ToolRegistry,
    broadcaster: PatchEventBroadcaster,
    task_id: str,
    patch_engine: object | None = None,
    shadow_path: Path | None = None,
    scope_extension_callback: ScopeExtensionCallback | None = None,
    broadcast_key: str | None = None,       # NEW: if set, all broadcasts use this channel
) -> None:
    self._reasoning = reasoning_engine
    self._registry = registry
    self._broadcaster = broadcaster
    self._task_id = task_id
    self._broadcast_key = broadcast_key or task_id   # NEW
    self._patch_engine = patch_engine
    self._shadow_path = shadow_path
    self._scope_cb: ScopeExtensionCallback = (
        scope_extension_callback or _default_reject_callback
    )
```

Then replace every `self._broadcaster.broadcast(self._task_id, ...)` in the file with `self._broadcaster.broadcast(self._broadcast_key, ...)`. (There are 8 occurrences — use a global replace.)

- [ ] **Step 4: Add `skip_verify` parameter to `run()`**

In `tools/loop.py`, update the `run()` signature:

```python
async def run(
    self,
    step: PlanStep,
    patch_request_context: dict[str, object],
    budget: TaskBudget,
    usage: TaskUsage,
    skip_verify: bool = False,        # NEW
) -> StepOutcome:
```

Then, after the successful `patch_applied` broadcast (right before `continue` at end of the `emit_patch` success path, after line ~367), add:

```python
                self._broadcaster.broadcast(self._broadcast_key, {
                    "type": "patch_applied",
                    "payload": {"step_id": step.id, "phase": "verify", "touched_files": all_touched_files},
                })
                if skip_verify:
                    return VerifyResult(
                        patch_document=last_patch_document,
                        touched_files=all_touched_files,
                        verified=True,
                        test_output="",
                        tool_trace=trace,
                    )
                continue
```

- [ ] **Step 5: Run skip_verify tests — pass**

```bash
cd services/agentd-py && pytest tests/test_tool_loop_skip_verify.py -v
```

Expected: 2 passed

- [ ] **Step 6: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/tools/loop.py \
        services/agentd-py/tests/test_tool_loop_skip_verify.py
git commit -m "feat(tool-loop): add broadcast_key and skip_verify flag"
```

---

### Task 6: `ShadowWorkspaceManager.prepare_lightweight()`

**Files:**
- Modify: `services/agentd-py/agentd/workspace/shadow.py`
- Create: `services/agentd-py/tests/test_shadow_lightweight.py`

- [ ] **Step 1: Write failing tests**

```python
# services/agentd-py/tests/test_shadow_lightweight.py
from __future__ import annotations
import pytest
from agentd.workspace.shadow import ShadowWorkspaceManager, ShadowWorkspace


@pytest.mark.asyncio
async def test_prepare_lightweight_copies_only_target_files(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.py").write_text("a")
    (ws / "b.py").write_text("b")
    (ws / "c.py").write_text("c")

    manager = ShadowWorkspaceManager(tmp_path / "shadows")
    shadow = await manager.prepare_lightweight("task-1", str(ws), ["a.py", "b.py"])

    assert isinstance(shadow, ShadowWorkspace)
    assert (shadow.shadow_path / "a.py").read_text() == "a"
    assert (shadow.shadow_path / "b.py").read_text() == "b"
    assert not (shadow.shadow_path / "c.py").exists()


@pytest.mark.asyncio
async def test_prepare_lightweight_skips_missing_files(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "exists.py").write_text("x")

    manager = ShadowWorkspaceManager(tmp_path / "shadows")
    shadow = await manager.prepare_lightweight("task-1", str(ws), ["exists.py", "missing.py"])

    assert (shadow.shadow_path / "exists.py").exists()
    assert not (shadow.shadow_path / "missing.py").exists()


@pytest.mark.asyncio
async def test_prepare_lightweight_preserves_subdirectory_paths(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "module.py").write_text("m")

    manager = ShadowWorkspaceManager(tmp_path / "shadows")
    shadow = await manager.prepare_lightweight("task-1", str(ws), ["pkg/module.py"])

    assert (shadow.shadow_path / "pkg" / "module.py").read_text() == "m"


@pytest.mark.asyncio
async def test_prepare_lightweight_overwrites_existing_shadow(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.py").write_text("v2")

    manager = ShadowWorkspaceManager(tmp_path / "shadows")
    # Create once with old content, then again
    await manager.prepare_lightweight("task-1", str(ws), ["a.py"])
    (ws / "a.py").write_text("v2")
    shadow = await manager.prepare_lightweight("task-1", str(ws), ["a.py"])
    assert (shadow.shadow_path / "a.py").read_text() == "v2"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && pytest tests/test_shadow_lightweight.py -v
```

Expected: `AttributeError: 'ShadowWorkspaceManager' has no attribute 'prepare_lightweight'`

- [ ] **Step 3: Implement `prepare_lightweight()`**

In `services/agentd-py/agentd/workspace/shadow.py`, add after the `clone()` method:

```python
async def prepare_lightweight(
    self,
    task_id: str,
    workspace_path: str,
    target_files: list[str],
) -> ShadowWorkspace:
    """Create a minimal shadow containing only the listed target files."""
    real_path = Path(workspace_path).resolve()
    if not real_path.exists() or not real_path.is_dir():
        msg = f"Workspace path is not a directory: {workspace_path}"
        raise RuntimeError(msg)

    shadow_path = self._resolve_shadow_path(task_id)
    if shadow_path.exists():
        shutil.rmtree(shadow_path)
    shadow_path.mkdir(parents=True)

    for rel_path in target_files:
        src = self._resolve_inside(real_path, rel_path)
        if not src.exists():
            continue
        dst = self._resolve_inside(shadow_path, rel_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    return ShadowWorkspace(
        task_id=task_id,
        real_path=real_path,
        shadow_path=shadow_path,
    )
```

- [ ] **Step 4: Run tests — all pass**

```bash
cd services/agentd-py && pytest tests/test_shadow_lightweight.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/workspace/shadow.py \
        services/agentd-py/tests/test_shadow_lightweight.py
git commit -m "feat(shadow): add prepare_lightweight() for inline change temp shadows"
```

---

### Task 7: Planning prompt — `initial_explore_context`

**Files:**
- Modify: `services/agentd-py/agentd/planning/prompts.py`

- [ ] **Step 1: Add instruction block to `PLANNING_SYSTEM_PROMPT`**

In `services/agentd-py/agentd/planning/prompts.py`, find `PLANNING_SYSTEM_PROMPT` and append this block inside it (after the existing rules, before the closing `"""`):

```python
# Find the closing triple-quote of PLANNING_SYSTEM_PROMPT and insert before it:

PRE_GATHERED_EXPLORE_CONTEXT_INSTRUCTION = """
PRE-GATHERED EXPLORE CONTEXT
If initial_explore_context is present in the payload, these tool call results were already
gathered before planning began. Treat them as pre-gathered evidence — do NOT re-read these
files or re-run these searches. Build your plan directly on top of these findings. Direct your
tool budget toward files and symbols not yet examined.
"""
```

Add the constant as a module-level string and append it to `PLANNING_SYSTEM_PROMPT`:

```python
PLANNING_SYSTEM_PROMPT = (
    PLANNING_SYSTEM_PROMPT
    + "\n"
    + PRE_GATHERED_EXPLORE_CONTEXT_INSTRUCTION
)
```

- [ ] **Step 2: Update `build_planning_step_payload()` to include the field**

In `build_planning_step_payload()` in `prompts.py`, after the existing `initial_context` handling block, add:

```python
initial_explore_context = plan_context.get("initial_explore_context")
if initial_explore_context:
    payload["initial_explore_context"] = initial_explore_context
```

- [ ] **Step 3: Verify payload builder includes the field**

```bash
cd services/agentd-py && python -c "
from agentd.planning.prompts import build_planning_step_payload
ctx = {'goal': 'fix bug', 'workspace_path': '/ws', 'initial_explore_context': [{'tool': 'read_file', 'result': 'x', 'is_error': False}]}
payload = build_planning_step_payload(ctx, [], [])
assert 'initial_explore_context' in payload, 'missing field'
print('OK:', payload['initial_explore_context'])
"
```

Expected: `OK: [{'tool': 'read_file', 'result': 'x', 'is_error': False}]`

- [ ] **Step 4: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/planning/prompts.py
git commit -m "feat(planning): pass initial_explore_context from chat into planning prompt"
```

---

### Task 8: `ChatAgent` — broadcaster coroutine + `_draft_plan_markdown`

**Files:**
- Modify: `services/agentd-py/agentd/chat/agent.py`
- Create: `services/agentd-py/tests/test_chat_agent_broadcaster.py`

- [ ] **Step 1: Write failing tests**

```python
# services/agentd-py/tests/test_chat_agent_broadcaster.py
from __future__ import annotations
import asyncio
import pytest
from agentd.chat.agent import ChatAgent
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster


class _NullTransport:
    async def generate_text(self, **_) -> str:
        return "test answer"

    async def generate_json(self, *, schema_name, **_) -> dict:
        if schema_name == "explore_step":
            return {"action": "done"}
        return {"intent": "qa", "rationale": "test", "likely_targets": [], "answer": "hi"}


@pytest.fixture
def broadcaster():
    return EventBroadcaster()


@pytest.fixture
def agent(tmp_path, broadcaster):
    store = ChatThreadStore(tmp_path / "chat.db")
    return ChatAgent(
        workspace_path=str(tmp_path),
        transport=_NullTransport(),
        model="test",
        thread_store=store,
        orchestrator=None,
        broadcaster=broadcaster,
    )


@pytest.mark.asyncio
async def test_handle_message_is_coroutine_not_generator(agent, tmp_path):
    """handle_message must be a plain coroutine, not an async generator."""
    import inspect
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(tmp_path), title="t")
    result = agent.handle_message(thread.thread_id, "hello", "chat:ch1")
    assert asyncio.iscoroutine(result), "handle_message must return a coroutine"
    await result  # consume it


@pytest.mark.asyncio
async def test_handle_message_emits_chat_done_to_broadcaster(agent, broadcaster, tmp_path):
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(tmp_path), title="t")

    queue = broadcaster.subscribe("chat:ch1")
    await agent.handle_message(thread.thread_id, "hello", "chat:ch1")

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    types = [e["type"] for e in events]
    assert "chat_done" in types, f"expected chat_done; got {types}"


@pytest.mark.asyncio
async def test_handle_message_emits_intent_classified(agent, broadcaster, tmp_path):
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(tmp_path), title="t")

    queue = broadcaster.subscribe("chat:ch2")
    await agent.handle_message(thread.thread_id, "explain this", "chat:ch2")

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    classified = [e for e in events if e["type"] == "intent_classified"]
    assert classified, f"expected intent_classified event; got {[e['type'] for e in events]}"
    assert "payload" in classified[0]
    assert "intent" in classified[0]["payload"]


@pytest.mark.asyncio
async def test_handle_message_events_have_payload_envelope(agent, broadcaster, tmp_path):
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(tmp_path), title="t")

    queue = broadcaster.subscribe("chat:ch3")
    await agent.handle_message(thread.thread_id, "explain this", "chat:ch3")

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    for e in events:
        assert "payload" in e, f"event {e['type']!r} missing 'payload' key"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && pytest tests/test_chat_agent_broadcaster.py -v
```

Expected: `TypeError` (unexpected keyword argument `broadcaster` or `handle_message` is async generator)

- [ ] **Step 3: Rewrite `ChatAgent`**

Replace the entire content of `services/agentd-py/agentd/chat/agent.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentd.chat.classifier import IntentClassifier
from agentd.chat.models import ChatMessage, IntentType
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
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
conversation_history contains recent turns — if the answer is already in history, emit action=done immediately without calling any tools.
Only use tools to find information that is not already covered by history or prior tool_results.
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
        broadcaster: EventBroadcaster,
        max_explore_calls: int = 5,
    ) -> None:
        self._workspace_path = workspace_path
        self._transport = transport
        self._model = model
        self._store = thread_store
        self._orchestrator = orchestrator
        self._broadcaster = broadcaster
        self._max_explore_calls = max_explore_calls
        self._registry = PlanningToolRegistry(real_path=Path(workspace_path))
        self._classifier = IntentClassifier(transport=transport, model=model)

    async def handle_message(self, thread_id: str, message: str, channel_id: str) -> None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id!r} not found")

        user_msg = ChatMessage(role="user", content=message)
        self._store.append_message(thread_id, user_msg)
        history = [{"role": m.role, "content": m.content} for m in thread.messages]

        self._broadcaster.broadcast(channel_id, {
            "type": "chat_agent_thinking",
            "payload": {"message": "Exploring workspace…"},
        })

        context: list[dict[str, Any]] = []
        files_examined: list[str] = []

        for _ in range(self._max_explore_calls):
            try:
                step = await self._transport.generate_json(
                    model=self._model,
                    schema_name="explore_step",
                    schema=_EXPLORE_SCHEMA,
                    system_instructions=_EXPLORE_PROMPT,
                    user_payload={
                        "message": message,
                        "workspace_path": self._workspace_path,
                        "conversation_history": history[-6:],
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

            self._broadcaster.broadcast(channel_id, {
                "type": "explore_tool_call",
                "payload": {"tool": tool_name, "args": args},
            })

            try:
                tool_output = await self._registry.execute(tool_name, args)
                context.append({"tool": tool_name, "result": tool_output.output, "is_error": tool_output.is_error})
            except Exception as exc:
                context.append({"tool": tool_name, "result": str(exc), "is_error": True})

            if tool_name in ("read_file", "list_directory"):
                path = args.get("path", "")
                if path and path not in files_examined:
                    files_examined.append(str(path))

        classification = await self._classifier.classify(
            message, context=context, history=history
        )
        self._broadcaster.broadcast(channel_id, {
            "type": "intent_classified",
            "payload": {
                "intent": classification.intent,
                "rationale": classification.rationale,
                "likely_targets": classification.likely_targets,
                "files_examined": files_examined,
            },
        })

        if classification.intent == IntentType.QA:
            if classification.answer:
                response_text = classification.answer
            else:
                try:
                    response_text = await self._transport.generate_text(
                        model=self._model,
                        system_instructions=_QA_PROMPT,
                        user_payload={
                            "workspace_path": self._workspace_path,
                            "conversation_history": history[-10:],
                            "workspace_context": context,
                            "question": message,
                        },
                    )
                except Exception:
                    logger.exception("Q&A LLM call failed")
                    response_text = "Sorry, I couldn't answer that. Please try again."

            self._store.append_message(thread_id, ChatMessage(role="agent", content=response_text))
            self._broadcaster.broadcast(channel_id, {
                "type": "chat_response",
                "payload": {"chunk": response_text},
            })

        elif classification.intent == IntentType.SMALL_CHANGE:
            if self._orchestrator is None:
                self._broadcaster.broadcast(channel_id, {
                    "type": "chat_response",
                    "payload": {"chunk": "[small_change: orchestrator not available]"},
                })
            else:
                self._broadcaster.broadcast(channel_id, {
                    "type": "chat_agent_thinking",
                    "payload": {"message": "Drafting implementation plan…"},
                })
                try:
                    plan_md = await self._draft_plan_markdown(
                        message, context, classification.likely_targets
                    )
                    await self._orchestrator.run_inline_change(
                        goal=message,
                        workspace_path=self._workspace_path,
                        explore_context=context,
                        likely_targets=classification.likely_targets,
                        plan_markdown=plan_md,
                        channel_id=channel_id,
                    )
                except Exception:
                    logger.exception("Inline change failed")
                    self._broadcaster.broadcast(channel_id, {
                        "type": "chat_response",
                        "payload": {"chunk": "Failed to apply change. Please try again."},
                    })
        else:
            # large_change and unknown intents
            self._broadcaster.broadcast(channel_id, {
                "type": "chat_response",
                "payload": {"chunk": f"[{classification.intent} routing — not yet wired]"},
            })

        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})

    async def _draft_plan_markdown(
        self,
        message: str,
        explore_context: list[dict[str, Any]],
        likely_targets: list[str],
    ) -> str:
        context_summary = "\n".join(
            f"[{r['tool']}]: {r['result'][:500]}"
            for r in explore_context
            if not r.get("is_error")
        )
        try:
            return await self._transport.generate_text(
                model=self._model,
                system_instructions=(
                    "Write a brief implementation plan in markdown. "
                    "Cover: which files to change, exactly what to change, how to verify. "
                    "Be concise — this is a small change."
                ),
                user_payload={
                    "user_request": message,
                    "explore_findings": context_summary,
                    "likely_targets": likely_targets,
                },
            )
        except Exception:
            logger.exception("_draft_plan_markdown failed")
            targets_str = ", ".join(likely_targets) or "unknown files"
            return f"## Plan\n\nImplement: {message}\n\nFiles: {targets_str}\n"
```

- [ ] **Step 4: Run tests — all pass**

```bash
cd services/agentd-py && pytest tests/test_chat_agent_broadcaster.py -v
```

Expected: 4 passed

- [ ] **Step 5: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/agent.py \
        services/agentd-py/tests/test_chat_agent_broadcaster.py
git commit -m "feat(chat-agent): convert handle_message to broadcaster coroutine; add _draft_plan_markdown"
```

---

### Task 9: Chat message route — background-task pattern + broadcaster wiring

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`
- Modify: `services/agentd-py/agentd/main.py`
- Modify: `services/agentd-py/agentd/chat/app_factory.py`

- [ ] **Step 1: Update the chat message route in `routes.py`**

In `services/agentd-py/agentd/api/routes.py`, find the chat message handler (around line 700) that currently reads:

```python
async def event_stream():
    async for event in _chat_agent.handle_message(thread_id, message):
        yield f"data: {event.model_dump_json()}\n\n"
return StreamingResponse(event_stream(), media_type="text/event-stream")
```

Replace it with:

```python
channel_id = f"chat:{uuid4().hex}"
queue = orchestrator.broadcaster.subscribe(channel_id)
asyncio.create_task(_chat_agent.handle_message(thread_id, message, channel_id))

async def event_stream():
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "chat_done":
                break
    finally:
        orchestrator.broadcaster.unsubscribe(channel_id, queue)

return StreamingResponse(event_stream(), media_type="text/event-stream")
```

Also add `from uuid import uuid4` and `import asyncio` to the routes imports if not already present.

- [ ] **Step 2: Pass `broadcaster` to `ChatAgent` in `main.py`**

In `services/agentd-py/agentd/main.py`, find the `ChatAgent` construction and add `broadcaster`:

```python
# BEFORE:
_chat_agent = ChatAgent(
    workspace_path=_chat_workspace_path,
    transport=transport,
    model=_chat_model,
    thread_store=_chat_thread_store,
    orchestrator=orchestrator,
) if reasoning_backend != "scripted" else None

# AFTER:
_chat_agent = ChatAgent(
    workspace_path=_chat_workspace_path,
    transport=transport,
    model=_chat_model,
    thread_store=_chat_thread_store,
    orchestrator=orchestrator,
    broadcaster=orchestrator.broadcaster,
) if reasoning_backend != "scripted" else None
```

- [ ] **Step 3: Pass `broadcaster` to `ChatAgent` in `app_factory.py`**

In `services/agentd-py/agentd/chat/app_factory.py`, update the `ChatAgent` construction:

```python
# BEFORE:
agent = ChatAgent(
    workspace_path=workspace_path,
    transport=_NullTransport(),
    model="test-model",
    thread_store=chat_store,
    orchestrator=None,
)

# AFTER:
agent = ChatAgent(
    workspace_path=workspace_path,
    transport=_NullTransport(),
    model="test-model",
    thread_store=chat_store,
    orchestrator=None,
    broadcaster=orchestrator.broadcaster,
)
```

- [ ] **Step 4: Verify the app factory still builds**

```bash
cd services/agentd-py && python -c "
from agentd.chat.app_factory import build_app
import tempfile, os
with tempfile.TemporaryDirectory() as tmp:
    app = build_app(tmp)
    print('OK:', app)
"
```

Expected: prints `OK: <FastAPI ...>`

- [ ] **Step 5: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py \
        services/agentd-py/agentd/main.py \
        services/agentd-py/agentd/chat/app_factory.py
git commit -m "feat(routes): chat message route uses broadcaster background-task pattern"
```

---

### Task 10: `AgentOrchestrator.run_inline_change()`

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py`
- Create: `services/agentd-py/tests/test_orchestrator_inline_change.py`

- [ ] **Step 1: Write failing tests**

```python
# services/agentd-py/tests/test_orchestrator_inline_change.py
from __future__ import annotations
import asyncio
from pathlib import Path
import pytest
from agentd.domain.models import TaskBudget, TaskStatus
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _InlineEngine:
    """Scripted engine: emits a single search_replace patch then skip_verify exits."""
    async def create_plan(self, task, workspace_path, retrieval_context):
        return {
            "analysis": "inline test",
            "steps": [
                {
                    "id": "S1",
                    "goal": "replace placeholder",
                    "targets": [{"path": "target.py", "intent": "existing"}],
                    "risk": "low",
                }
            ],
            "expected_files": ["target.py"],
            "stop_conditions": ["done"],
        }

    async def create_tool_step(self, step_context, history, tool_definitions):
        if not history:
            return {
                "type": "emit_patch",
                "thought": "replace",
                "patch_ops": [{"op": "search_replace", "file": "target.py", "search": "PLACEHOLDER", "replace": "REPLACED", "reason": "test"}],
            }
        return {"type": "verify_done", "thought": "v", "verified": True, "test_output": ""}

    async def create_planning_step(self, *a, **k): return {}
    async def create_patch(self, *a, **k): return {"candidates": []}


class _AlwaysPassValidator:
    async def run_touched(self, workspace_path, touched_files):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)

    async def run(self, workspace_path):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


@pytest.mark.asyncio
async def test_run_inline_change_broadcasts_diff_ready(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "target.py").write_text("PLACEHOLDER\n")

    store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=_InlineEngine(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ws_manager,
    )

    queue = orchestrator.broadcaster.subscribe("chat:ch1")
    await orchestrator.run_inline_change(
        goal="replace placeholder",
        workspace_path=str(ws),
        explore_context=[],
        likely_targets=["target.py"],
        plan_markdown="## Plan\nReplace PLACEHOLDER",
        channel_id="chat:ch1",
    )

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    diff_ready = [e for e in events if e["type"] == "diff_ready"]
    assert diff_ready, f"expected diff_ready event; got {[e['type'] for e in events]}"
    payload = diff_ready[0]["payload"]
    assert "task_id" in payload
    assert "diff_entries" in payload


@pytest.mark.asyncio
async def test_run_inline_change_creates_task_record_in_store(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "target.py").write_text("PLACEHOLDER\n")

    store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=_InlineEngine(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )

    orchestrator.broadcaster.subscribe("chat:ch2")
    await orchestrator.run_inline_change(
        goal="g", workspace_path=str(ws), explore_context=[],
        likely_targets=["target.py"], plan_markdown="## Plan",
        channel_id="chat:ch2",
    )

    tasks = await store.list_all()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.is_inline_change is True
    assert task.chat_channel_id == "chat:ch2"
    assert task.status == TaskStatus.READY_FOR_REVIEW


@pytest.mark.asyncio
async def test_run_inline_change_marks_failed_on_engine_error(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    class _BrokenEngine:
        async def create_plan(self, *a, **k):
            raise RuntimeError("simulated plan failure")
        async def create_tool_step(self, *a, **k): return {}
        async def create_planning_step(self, *a, **k): return {}
        async def create_patch(self, *a, **k): return {}

    store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=_BrokenEngine(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )

    orchestrator.broadcaster.subscribe("chat:ch3")
    await orchestrator.run_inline_change(
        goal="g", workspace_path=str(ws), explore_context=[],
        likely_targets=[], plan_markdown="## Plan",
        channel_id="chat:ch3",
    )

    tasks = await store.list_all()
    assert tasks[0].status == TaskStatus.FAILED
```

- [ ] **Step 2: Add `list_all()` to `InMemoryTaskStore` if missing**

```bash
grep -n "def list_all" services/agentd-py/agentd/storage/in_memory.py
```

If not found, add to `InMemoryTaskStore`:

```python
async def list_all(self) -> list[TaskRecord]:
    return list(self._tasks.values())
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd services/agentd-py && pytest tests/test_orchestrator_inline_change.py -v
```

Expected: `AttributeError: 'AgentOrchestrator' has no attribute 'run_inline_change'`

- [ ] **Step 4: Implement `run_inline_change()` in `engine.py`**

Add these helper functions near the top of `engine.py` (after the existing imports):

```python
def _format_explore_as_retrieval_supplement(
    explore_context: list[dict[str, object]],
) -> dict[str, str]:
    file_contents: dict[str, str] = {}
    for entry in explore_context:
        if entry.get("tool") == "read_file" and not entry.get("is_error"):
            args = entry.get("args")
            path = args.get("path") if isinstance(args, dict) else None
            if path:
                file_contents[str(path)] = str(entry.get("result", ""))
    return file_contents


def _compute_diff_entries(
    shadow_path: Path,
    real_path: Path,
    expected_files: list[str],
) -> list["DiffEntry"]:
    from agentd.domain.models import DiffEntry
    entries: list[DiffEntry] = []
    for rel_path in expected_files:
        shadow_file = (shadow_path / rel_path).resolve()
        real_file = (real_path / rel_path).resolve()
        if not shadow_file.exists():
            continue
        shadow_text = shadow_file.read_text(encoding="utf-8", errors="replace")
        real_text = real_file.read_text(encoding="utf-8", errors="replace") if real_file.exists() else ""
        diff_lines = list(difflib.unified_diff(
            real_text.splitlines(), shadow_text.splitlines(), lineterm=""
        ))
        additions = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        if additions > 0 or deletions > 0:
            entries.append(DiffEntry(
                path=rel_path,
                additions=additions,
                deletions=deletions,
                temp_path=str(shadow_file),
            ))
    return entries
```

Add the `run_inline_change()` method to `AgentOrchestrator` (after `resume_task()`):

```python
async def run_inline_change(
    self,
    *,
    goal: str,
    workspace_path: str,
    explore_context: list[dict[str, object]],
    likely_targets: list[str],
    plan_markdown: str,
    channel_id: str,
) -> None:
    """Execute a small change inline within a chat thread.

    Creates a lightweight shadow, runs ToolLoop with skip_verify=True, and
    broadcasts all events (tool calls, patch operations, diff_ready) to channel_id.
    The TaskRecord is created in PLANNED state and transitions to READY_FOR_REVIEW
    on success or FAILED on error.
    """
    from agentd.domain.models import DiffEntry, PlanDocument, TaskRecord, TaskStatus
    from agentd.tools.loop import ToolLoop, build_tool_registry

    task_id = f"inline-{uuid4().hex[:12]}"
    task = TaskRecord(
        task_id=task_id,
        goal=goal,
        workspace_path=workspace_path,
        status=TaskStatus.PLANNED,
        is_inline_change=True,
        chat_channel_id=channel_id,
        plan_markdown=plan_markdown,
    )
    await self._store.create(task)

    try:
        # Load retrieval context, merge explore supplement
        retrieval_context, _ = self._retrieval_client.load_context(workspace_path, goal)
        supplement_files = _format_explore_as_retrieval_supplement(explore_context)
        for path, content in supplement_files.items():
            if path not in retrieval_context.file_contents:
                retrieval_context.file_contents[path] = content

        plan_context_payload = retrieval_context.as_prompt_payload()
        if explore_context:
            plan_context_payload["initial_explore_context"] = explore_context

        # Create lightweight shadow (only target files)
        shadow = await self._workspace_manager.prepare_lightweight(
            task_id, workspace_path, likely_targets
        )
        task.shadow_workspace_path = str(shadow.shadow_path)
        await self._store.save(task)

        # Generate JSON plan from the markdown blueprint
        plan_raw = await self._reasoning_engine.create_plan(
            task, str(shadow.shadow_path), plan_context_payload
        )
        plan = PlanDocument.model_validate(plan_raw)
        task.plan = plan
        task.status = TaskStatus.EXECUTING
        await self._store.save(task)

        # Execute each step with skip_verify=True, broadcasting to chat channel
        registry = build_tool_registry(
            shadow.shadow_path,
            self._retrieval_client,
            real_workspace_path=Path(workspace_path),
        )
        for step in plan.steps:
            tool_loop = ToolLoop(
                self._reasoning_engine,
                registry,
                self.broadcaster,
                task_id,
                self._patch_engine,
                shadow.shadow_path,
                broadcast_key=channel_id,
            )
            patch_context: dict[str, object] = {
                **plan_context_payload,
                "plan_markdown": plan_markdown,
            }
            step_outcome = await tool_loop.run(
                step, patch_context, task.budget, task.usage, skip_verify=True
            )
            if isinstance(step_outcome, PlanHandoff):
                logger.warning(
                    "Inline change step %s emitted revision_needed — stopping at partial diff",
                    step.id,
                )
                break
            if hasattr(step_outcome, "touched_files"):
                for f in step_outcome.touched_files:
                    if f not in task.modified_files:
                        task.modified_files.append(f)

        # Compute diff and transition to READY_FOR_REVIEW
        diff_entries = _compute_diff_entries(
            shadow.shadow_path, Path(workspace_path), plan.expected_files
        )
        task.status = TaskStatus.READY_FOR_REVIEW
        await self._store.save(task)

        self.broadcaster.broadcast(channel_id, {
            "type": "diff_ready",
            "payload": {
                "task_id": task_id,
                "diff_entries": [
                    {
                        "path": e.path,
                        "additions": e.additions,
                        "deletions": e.deletions,
                        "temp_path": e.temp_path,
                    }
                    for e in diff_entries
                ],
                "completed_steps": len(task.modified_files),
                "total_steps": len(plan.steps),
            },
        })

    except Exception:
        logger.exception("run_inline_change failed for task %s", task_id)
        try:
            task.status = TaskStatus.FAILED
            await self._store.save(task)
        except Exception:
            pass
        self.broadcaster.broadcast(channel_id, {
            "type": "chat_response",
            "payload": {"chunk": "Failed to apply inline change. Please try again."},
        })
```

Also add `from agentd.domain.models import DiffEntry` to the top-level imports in `engine.py`.

- [ ] **Step 5: Run tests**

```bash
cd services/agentd-py && pytest tests/test_orchestrator_inline_change.py -v
```

Expected: 3 passed

- [ ] **Step 6: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/orchestrator/engine.py \
        services/agentd-py/agentd/storage/in_memory.py \
        services/agentd-py/tests/test_orchestrator_inline_change.py
git commit -m "feat(orchestrator): add run_inline_change() for small_change chat path"
```

---

### Task 11: Inline change promote/discard API routes

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`

- [ ] **Step 1: Add promote and discard routes**

In `services/agentd-py/agentd/api/routes.py`, add after the existing chat routes:

```python
from datetime import datetime, timezone
from pydantic import BaseModel

class _PromoteInlineResponse(BaseModel):
    task_id: str
    promoted_files: list[str]

class _DiscardInlineResponse(BaseModel):
    task_id: str

@router.post("/v1/chat/inline-changes/{task_id}/promote", response_model=_PromoteInlineResponse)
async def promote_inline_change(task_id: str) -> _PromoteInlineResponse:
    try:
        task = await store.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not task.is_inline_change:
        raise HTTPException(status_code=400, detail="Task is not an inline change")
    if task.status != TaskStatus.READY_FOR_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Expected READY_FOR_REVIEW, got {task.status}",
        )
    await workspace_manager.promote(task)
    task.status = TaskStatus.SUCCEEDED
    task.promoted_at = datetime.now(timezone.utc)
    await store.save(task)
    return _PromoteInlineResponse(task_id=task_id, promoted_files=task.modified_files)

@router.delete("/v1/chat/inline-changes/{task_id}", response_model=_DiscardInlineResponse)
async def discard_inline_change(task_id: str) -> _DiscardInlineResponse:
    try:
        task = await store.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not task.is_inline_change:
        raise HTTPException(status_code=400, detail="Task is not an inline change")
    try:
        await workspace_manager.cleanup(task)
    except Exception:
        logger.warning("Failed to clean up shadow for inline task %s", task_id, exc_info=True)
    task.status = TaskStatus.ABORTED
    await store.save(task)
    return _DiscardInlineResponse(task_id=task_id)
```

- [ ] **Step 2: Verify routes are reachable**

```bash
cd services/agentd-py && python -c "
from agentd.chat.app_factory import build_app
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    app = build_app(tmp)
    routes = [r.path for r in app.routes]
    assert any('inline-changes' in r for r in routes), f'route not found in {routes}'
    print('OK:', [r for r in routes if 'inline' in r])
"
```

Expected: prints the two new route paths

- [ ] **Step 3: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

- [ ] **Step 4: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py
git commit -m "feat(routes): add POST /promote and DELETE /discard for inline changes"
```

---

### Task 12: TypeScript inline change client + controller wiring

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/editor-client/src/client/http-backend-client.ts`
- Modify: `apps/vscode-extension/src/controller.ts`
- Modify: `apps/editor-client/test/http-backend-client.test.ts`

- [ ] **Step 1: Add `applyInlineChange` and `discardInlineChange` to `BackendTaskClient`**

In `apps/editor-client/src/contracts/task-contracts.ts`, add to the `BackendTaskClient` interface:

```typescript
applyInlineChange(taskId: string): Promise<{ taskId: string; promotedFiles: string[] }>;
discardInlineChange(taskId: string): Promise<{ taskId: string }>;
```

- [ ] **Step 2: Implement the methods in `HttpBackendClient`**

In `apps/editor-client/src/client/http-backend-client.ts`, add:

```typescript
async applyInlineChange(taskId: string): Promise<{ taskId: string; promotedFiles: string[] }> {
  const res = await this._fetchFn(
    `${this._baseUrl}/v1/chat/inline-changes/${taskId}/promote`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(`applyInlineChange failed: ${res.status}`);
  const data = await res.json() as { task_id: string; promoted_files: string[] };
  return { taskId: data.task_id, promotedFiles: data.promoted_files };
}

async discardInlineChange(taskId: string): Promise<{ taskId: string }> {
  const res = await this._fetchFn(
    `${this._baseUrl}/v1/chat/inline-changes/${taskId}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error(`discardInlineChange failed: ${res.status}`);
  const data = await res.json() as { task_id: string };
  return { taskId: data.task_id };
}
```

- [ ] **Step 3: Write tests for the new client methods**

In `apps/editor-client/test/http-backend-client.test.ts`, add:

```typescript
test("applyInlineChange POSTs to promote endpoint and maps response", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  const client = new HttpBackendClient({
    baseUrl: "http://localhost:8000",
    fetchFn: async (url, init) => {
      capturedUrl = String(url);
      capturedMethod = String(init?.method ?? "GET");
      return new Response(
        JSON.stringify({ task_id: "inline-abc", promoted_files: ["a.py"] }),
        { status: 200, headers: { "content-type": "application/json" } }
      );
    },
  });
  const result = await client.applyInlineChange("inline-abc");
  expect(capturedUrl).toContain("/v1/chat/inline-changes/inline-abc/promote");
  expect(capturedMethod).toBe("POST");
  expect(result.taskId).toBe("inline-abc");
  expect(result.promotedFiles).toEqual(["a.py"]);
});

test("discardInlineChange DELETEs to discard endpoint and maps response", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  const client = new HttpBackendClient({
    baseUrl: "http://localhost:8000",
    fetchFn: async (url, init) => {
      capturedUrl = String(url);
      capturedMethod = String(init?.method ?? "GET");
      return new Response(
        JSON.stringify({ task_id: "inline-xyz" }),
        { status: 200, headers: { "content-type": "application/json" } }
      );
    },
  });
  const result = await client.discardInlineChange("inline-xyz");
  expect(capturedUrl).toContain("/v1/chat/inline-changes/inline-xyz");
  expect(capturedMethod).toBe("DELETE");
  expect(result.taskId).toBe("inline-xyz");
});
```

- [ ] **Step 4: Run TypeScript tests**

```bash
npm run -w @ai-editor/editor-client test
```

Expected: all pass including 2 new tests

- [ ] **Step 5: Add `diff_ready` / `task_card` handling to `controller.ts`**

In `apps/vscode-extension/src/controller.ts`, add `applyInlineChange` and `discardInlineChange` methods to `AiEditorController`:

```typescript
async applyInlineChange(taskId: string): Promise<void> {
  const workspacePath = this.ui.getWorkspacePath();
  if (!workspacePath) return;
  const client = this.clientFactory(this.settings.getBackendBaseUrl());
  try {
    const result = await client.applyInlineChange(taskId);
    this.ui.appendChatMessage({
      role: "agent",
      content: `Changes applied to ${result.promotedFiles.length} file(s).`,
      type: "text",
    });
  } catch (error) {
    this.ui.showError(`Failed to apply changes: ${formatError(error)}`);
  }
}

async discardInlineChange(taskId: string): Promise<void> {
  const client = this.clientFactory(this.settings.getBackendBaseUrl());
  try {
    await client.discardInlineChange(taskId);
    this.ui.appendChatMessage({
      role: "agent",
      content: "Change discarded.",
      type: "text",
    });
  } catch (error) {
    this.ui.showError(`Failed to discard changes: ${formatError(error)}`);
  }
}
```

In `sendChatMessage()`, add `diff_ready` and `task_card` handling inside the event loop:

```typescript
} else if (event.type === "diff_ready") {
  this.ui.appendChatMessage({
    role: "agent",
    content: "",
    type: "diff_card",
    metadata: {
      taskId: event.payload.task_id,
      files: event.payload.diff_entries.map((e) => ({
        path: e.path,
        additions: e.additions,
        deletions: e.deletions,
        tempPath: e.tempPath,
      })),
      isInlineChange: true,
    },
  });
} else if (event.type === "task_card") {
  this.ui.appendChatMessage({
    role: "agent",
    content: "",
    type: "task_card",
    metadata: { taskId: event.payload.task_id },
  });
}
```

Also update `ChatMessage.type` in the contracts to include `"task_card"`:

In `apps/editor-client/src/contracts/task-contracts.ts`, find the `ChatMessage` type and add `"task_card"` to the union:

```typescript
// Find:
type: "text" | "plan_card" | "diff_card" | "diff_summary"
// Replace with:
type: "text" | "plan_card" | "diff_card" | "diff_summary" | "task_card"
```

Also add `applyInlineChange` and `discardInlineChange` callbacks to `ControllerUI` if the chat panel triggers them, and wire them up in `extension.ts`:

In `apps/vscode-extension/src/extension.ts`, pass the callbacks to `ChatPanel`:

```typescript
const chatPanel = new ChatPanel(
  (message) => controller.sendChatMessage(message),
  (taskId, action, feedback) => controller.handlePlanCardAction(taskId, action, feedback),
  () => controller.newChatThread(),
  (threadId) => controller.switchChatThread(threadId),
  (taskId) => controller.applyInlineChange(taskId),    // NEW
  (taskId) => controller.discardInlineChange(taskId),  // NEW
);
```

- [ ] **Step 6: Run typecheck**

```bash
npm run typecheck
```

Expected: no errors

- [ ] **Step 7: Run all TypeScript tests**

```bash
npm run test
```

Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add apps/editor-client/src/contracts/task-contracts.ts \
        apps/editor-client/src/client/http-backend-client.ts \
        apps/editor-client/test/http-backend-client.test.ts \
        apps/vscode-extension/src/controller.ts \
        apps/vscode-extension/src/extension.ts
git commit -m "feat(ts): inline change client methods, diff_ready/task_card controller handling"
```

---

### Task 13: `large_change` path — create full task with explore context

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py`
- Modify: `services/agentd-py/agentd/chat/agent.py`
- Create: `services/agentd-py/tests/test_orchestrator_large_change_chat.py`

When the intent is `large_change`, the chat agent creates a normal planning task but seeds it with the pre-gathered explore context. The PlanningAgent treats it as evidence already collected — avoiding re-reading the same files — and broadcasts a `task_card` event to the chat channel so the frontend can link to the task.

- [ ] **Step 1: Write failing tests**

```python
# services/agentd-py/tests/test_orchestrator_large_change_chat.py
from __future__ import annotations
import asyncio
import pytest
from agentd.domain.models import TaskStatus
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _MinimalEngine:
    """Scripted engine that emits the simplest possible plan and patch."""
    async def create_plan(self, task, workspace_path, retrieval_context):
        # Verify explore context was passed through
        assert retrieval_context.get("initial_explore_context") is not None, \
            "initial_explore_context was not passed to planning"
        return {
            "analysis": "test",
            "steps": [],
            "expected_files": [],
            "stop_conditions": ["done"],
        }

    async def create_tool_step(self, *a, **k):
        return {"type": "verify_done", "thought": "v", "verified": True, "test_output": ""}

    async def create_planning_step(self, payload, history, tool_definitions):
        return {"action": "emit_plan", "plan_markdown": "## Plan\n- done", "confidence": "high", "files_examined": []}

    async def create_patch(self, *a, **k):
        return {"candidates": []}


class _AlwaysPassValidator:
    async def run_touched(self, workspace_path, touched_files):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)

    async def run(self, workspace_path):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


@pytest.mark.asyncio
async def test_create_task_from_chat_stores_explore_context(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=_MinimalEngine(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    explore_ctx = [{"tool": "read_file", "result": "x", "is_error": False}]
    task_id = await orchestrator.create_task_from_chat(
        goal="add logging",
        workspace_path=str(ws),
        explore_context=explore_ctx,
    )

    task = await store.get(task_id)
    assert task.initial_explore_context == explore_ctx
    assert task.task_id == task_id


@pytest.mark.asyncio
async def test_create_task_from_chat_starts_run_task(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=_MinimalEngine(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )

    task_id = await orchestrator.create_task_from_chat(
        goal="add logging",
        workspace_path=str(ws),
        explore_context=[{"tool": "read_file", "result": "x", "is_error": False}],
    )

    # Give asyncio a chance to start the background task
    await asyncio.sleep(0.05)

    task = await store.get(task_id)
    # Task should have progressed past QUEUED
    assert task.status != TaskStatus.QUEUED, \
        f"Task should have started, still QUEUED: {task.status}"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/agentd-py && pytest tests/test_orchestrator_large_change_chat.py -v
```

Expected: `AttributeError: 'AgentOrchestrator' has no attribute 'create_task_from_chat'`

- [ ] **Step 3: Add `create_task_from_chat()` to `engine.py`**

In `services/agentd-py/agentd/orchestrator/engine.py`, add after `resume_task()` and before `run_inline_change()`:

```python
async def create_task_from_chat(
    self,
    *,
    goal: str,
    workspace_path: str,
    explore_context: list[dict[str, object]],
) -> str:
    """Create a full planning task pre-seeded with chat explore context.

    The explore context is stored on the TaskRecord and injected into the
    planning context in run_task(), letting the PlanningAgent treat already-read
    files as pre-gathered evidence rather than re-exploring them.
    """
    from agentd.domain.models import TaskRecord
    from uuid import uuid4
    task_id = f"task-{uuid4()}"
    task = TaskRecord(
        task_id=task_id,
        goal=goal,
        workspace_path=workspace_path,
        initial_explore_context=explore_context or None,
    )
    await self._store.create(task)
    import asyncio
    asyncio.create_task(self.run_task(task_id))
    return task_id
```

- [ ] **Step 4: Inject `initial_explore_context` into planning context in `run_task()`**

In `services/agentd-py/agentd/orchestrator/engine.py`, find the `run_task()` method. After the line:

```python
plan_context_payload = retrieval_context.as_prompt_payload()
```

Add:

```python
if task.initial_explore_context:
    plan_context_payload["initial_explore_context"] = task.initial_explore_context
```

- [ ] **Step 5: Wire `large_change` branch in `ChatAgent.handle_message()`**

In `services/agentd-py/agentd/chat/agent.py`, replace the final `else` clause (currently handles `large_change` and unknown intents) with:

```python
        elif classification.intent == IntentType.LARGE_CHANGE:
            if self._orchestrator is None:
                self._broadcaster.broadcast(channel_id, {
                    "type": "chat_response",
                    "payload": {"chunk": "[large_change: orchestrator not available]"},
                })
            else:
                self._broadcaster.broadcast(channel_id, {
                    "type": "chat_agent_thinking",
                    "payload": {"message": "Creating planning task…"},
                })
                try:
                    task_id = await self._orchestrator.create_task_from_chat(
                        goal=message,
                        workspace_path=self._workspace_path,
                        explore_context=context,
                    )
                    self._broadcaster.broadcast(channel_id, {
                        "type": "task_card",
                        "payload": {"task_id": task_id},
                    })
                except Exception:
                    logger.exception("create_task_from_chat failed")
                    self._broadcaster.broadcast(channel_id, {
                        "type": "chat_response",
                        "payload": {"chunk": "Failed to create planning task. Please try again."},
                    })
        else:
            self._broadcaster.broadcast(channel_id, {
                "type": "chat_response",
                "payload": {"chunk": f"[{classification.intent} routing not supported]"},
            })
```

- [ ] **Step 6: Run tests — all pass**

```bash
cd services/agentd-py && pytest tests/test_orchestrator_large_change_chat.py -v
```

Expected: 2 passed

- [ ] **Step 7: Run full test suite**

```bash
cd services/agentd-py && pytest --tb=short -q
```

Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add services/agentd-py/agentd/orchestrator/engine.py \
        services/agentd-py/agentd/chat/agent.py \
        services/agentd-py/tests/test_orchestrator_large_change_chat.py
git commit -m "feat(chat-agent): wire large_change path — create_task_from_chat with explore context"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `PatchEventBroadcaster` → `EventBroadcaster` with `channel_id` | Task 1 |
| `{type, payload}` event envelope everywhere | Tasks 3–4 |
| `ChatAgent.handle_message()` → broadcaster coroutine | Task 8 |
| Chat message route: background-task + broadcaster subscribe | Task 9 |
| `ToolLoop.skip_verify` flag | Task 5 |
| `ToolLoop.broadcast_key` for inline change events → chat channel | Task 5 |
| `ShadowWorkspaceManager.prepare_lightweight()` | Task 6 |
| `_draft_plan_markdown()` on `ChatAgent` | Task 8 |
| `AgentOrchestrator.run_inline_change()` | Task 10 |
| Inline change promote/discard routes | Task 11 |
| `DiffEntry`, `InlineChangeResult`, `TaskRecord` additions | Task 2 |
| `TaskCreateRequest.initial_explore_context` | Task 2 |
| Planning prompt `initial_explore_context` instruction | Task 7 |
| `diff_ready` event → diff card in controller | Task 12 |
| `task_card` message type | Tasks 4, 12 |
| TypeScript `applyInlineChange` / `discardInlineChange` | Task 12 |
| `large_change` path → `create_task_from_chat()` + `task_card` event | Task 13 |
| `TaskRecord.initial_explore_context` flows into PlanningAgent | Tasks 2, 13 |

**Type consistency check:**
- `DiffEntry` defined in `domain/models.py` (Task 2) → used in `engine.py` (Task 10) → serialised to `diff_ready` payload → matched by `DiffEntry` interface in `task-contracts.ts` (Task 4) → consumed in `controller.ts` (Task 12). ✓
- `EventBroadcaster` defined in `broadcaster.py` (Task 1) → imported in `chat/agent.py` (Task 8), `api/routes.py` (Task 9), `tools/loop.py` (Task 5). Alias `PatchEventBroadcaster` keeps all other importers working. ✓
- `VerifyResult` returned by `ToolLoop.run()` when `skip_verify=True` (Task 5) → consumed by `run_inline_change()` (Task 10). ✓
- `broadcast_key` in `ToolLoop.__init__` (Task 5) → passed as `channel_id` from `run_inline_change()` (Task 10). ✓
