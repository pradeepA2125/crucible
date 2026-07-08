# Chat Inline Change Design

**Date:** 2026-05-14  
**Status:** Approved  
**Scope:** Small-change and large-change execution paths from the chat interface

---

## Overview

The chat agent currently stubs `small_change` and `large_change` intent branches with a placeholder response. This spec defines how both execute end-to-end, feed into the existing planning and tool-loop infrastructure, and surface results back to the user inside the chat thread — without the user ever leaving the chat window.

---

## Goals

- `small_change`: fast, inline execution — explore → draft plan → JSON plan → ToolLoop → diff card in chat → user accepts or discards
- `large_change`: full existing pipeline enriched by the chat explore context — plan approval in chat, execution progress streamed to chat, diff card in chat at completion
- User retains veto power at every stage — nothing touches the real workspace until the user explicitly accepts
- Reuse `create_plan()`, `PlanDocument`, `ToolLoop`, `PatchEngine`, `ShadowWorkspaceManager` unchanged in behaviour
- No verify phase for small changes by default; test-on-demand available explicitly

## Non-Goals

- Replacing the ReviewPanel WebView — it remains the primary UI for full tasks (large_change and tasks submitted outside chat). For small changes, "Maximize" on the diff card opens VS Code's native `vscode.diff()` editor, not the ReviewPanel.
- Changing the full task lifecycle for tasks submitted outside the chat agent
- Auto-applying changes without user confirmation

---

## Architecture

```
User message
    │
    ▼
Chat Explore Phase (PlanningToolRegistry — read-only, up to 5 calls, unchanged)
    │
    ▼
IntentClassifier → intent + likely_targets
    │
    ├─── qa ──────────────────────────────→ generate_text answer (unchanged)
    │
    ├─── small_change ────────────────────→ _draft_plan_markdown(message, explore_context)
    │                                              │
    │                                              ▼
    │                                   AgentOrchestrator.run_inline_change()
    │                                       │                   │
    │                                 create_plan()       lightweight shadow
    │                             (retrieval_context      (temp dir, expected_files only)
    │                              merged with explore)         │
    │                                       │                   │
    │                                       └──→ ToolLoop (skip_verify=True) → diff
    │                                                                           │
    │                                                                  diff card in chat
    │                                                                  Apply / Discard
    │                                                                  Maximize → vscode.diff
    │
    └─── large_change ────────────────────→ POST /v1/tasks
                                           (explore_context as initial_explore_context)
                                                   │
                                        task_card message in chat thread
                                        plan card at AWAITING_PLAN_APPROVAL
                                        operation events stream to chat
                                        diff card at READY_FOR_REVIEW
```

---

## Components

### Python Backend

#### `ChatAgent._draft_plan_markdown(message, explore_context, likely_targets) → str`

Single `generate_text()` call after `IntentClassifier` returns `small_change`.

- **System prompt:** "Write a brief implementation plan in markdown. Cover: which files to change, exactly what to change, how to verify. Be concise — this is a small change."
- **User payload:** the user's original message + explore tool results formatted as readable findings + `likely_targets` from the classifier
- **Output:** plain markdown string — set as `task.plan_markdown` before calling `create_plan()`
- No JSON schema, no structured output — `generate_text`, fast

This keeps `PLAN_SYSTEM_INSTRUCTIONS` and `create_plan()` completely unchanged. The markdown draft is the authoritative blueprint `create_plan()` already expects.

---

#### `AgentOrchestrator.run_inline_change(goal, workspace_path, explore_context, likely_targets, plan_markdown, thread_id, budget?) → InlineChangeResult`

New method. Does not go through QUEUED → CONTEXT_READY → AWAITING_PLAN_APPROVAL.

Steps:
1. Create `TaskRecord` directly in `PLANNED` state
2. Call `retrieval_client.load_context(workspace_path, goal, hint_files=likely_targets)` → structured `RetrievalContext` (file outlines, symbol graph, planner_evidence). **Note:** `hint_files` is a new optional parameter on `RetrievalArtifactClient.load_context()` that scopes the snapshot query to the listed paths — requires a small addition to the retrieval client.
3. Merge explore context into retrieval context via `format_explore_as_retrieval_supplement()` — files already read during explore are not re-fetched
4. Call `create_plan(task, shadow_path, retrieval_context)` — **same call, same context shape as the full pipeline**; `task.plan_markdown` is already set as the blueprint
5. Call `ShadowWorkspaceManager.prepare_lightweight(workspace_path, plan.expected_files)` → temp dir with only target files
6. Run `ToolLoop` per step with `skip_verify=True`
7. Compute diff entries between temp files and real files
8. Return `InlineChangeResult { task_id, diff_entries, plan_document }`

On any unrecoverable error: mark `TaskRecord` as FAILED before raising, to prevent orphaned PLANNED tasks.

```python
@dataclass
class InlineChangeResult:
    task_id: str
    diff_entries: list[DiffEntry]  # {path, additions, deletions, temp_path}
    plan_document: dict
```

---

#### `ToolLoop.run(..., skip_verify: bool = False)`

One new flag. When `True`: after processing `emit_patch`, immediately return `PatchResult` without invoking the LLM again. The verify phase (Phase 2) is skipped entirely — no `run_command`, no `verify_done` call. Zero changes to schema, system prompt, or any other behaviour.

---

#### `ShadowWorkspaceManager.prepare_lightweight(workspace_path, target_files) → ShadowWorkspace`

Copies only the listed files into a temp directory, preserving relative paths. Returns a `ShadowWorkspace` with the same interface as `prepare()` — same contract, smaller footprint. `run_command` is not expected to work inside this shadow (only target files are present).

---

#### `TestToolRegistry`

Wraps `run_command` from `tools/shell.py` with the same allowlist. Available only in the post-patch test-on-demand phase — not accessible during the explore phase. Activated explicitly when the user's message signals a test request (e.g. "fix X and verify it passes").

**Test-on-demand flow:**
1. `run_inline_change()` completes with `skip_verify=True`
2. Chat agent detects test was requested
3. Patch files temporarily in place in real workspace
4. `TestToolRegistry.run_command(testing_strategy_command, cwd=workspace_path)`
5. Restore original files regardless of result
6. Test result streamed to chat alongside diff card

The `testing_strategy` field from the `PlanStep` (populated by `create_plan()`) is the source of the command — no additional LLM call needed.

---

#### `format_explore_as_retrieval_supplement(explore_context) → dict`

Utility function. Extracts:
- `file_contents` from `read_file` tool results
- `planner_evidence` entries from `search_code` and `search_semantic` results

Output dict is merged into the `retrieval_client.load_context()` result so already-read files are not fetched twice.

---

#### Planning Prompt Addition — `initial_explore_context`

`build_planning_step_payload()` in `planning/prompts.py` gains a new optional section. When `initial_explore_context` is present in the context dict passed to the planner:

```python
"initial_explore_context": [
    {"tool": "read_file", "args": {"path": "auth/middleware.py"}, "result": "..."},
    {"tool": "search_code", "args": {"pattern": "def get_user"}, "result": "..."},
    ...
]
```

`PLANNING_SYSTEM_PROMPT` gains a corresponding instruction block:

```
PRE-GATHERED EXPLORE CONTEXT
If initial_explore_context is present, these files and symbols were already
examined before planning began. Treat them as pre-gathered evidence.
Do NOT re-explore these files. Build your plan on top of these findings.
Direct your tool budget toward files and symbols not yet examined.
```

This applies to both large_change (where the full PlanningAgent loop runs) and ensures the planner's tool budget goes toward genuinely unknown territory.

---

### TypeScript / VS Code

#### `diff_card` and `task_card` message types

`diff_card` is already in the `ChatMessage.type` enum (`["text", "plan_card", "diff_card", "diff_summary"]`). This spec defines the `metadata` shape it carries for inline changes, and adds `task_card` as a new type:

```typescript
// diff_card metadata (inline change):
metadata: {
  taskId: string,
  files: Array<{ path: string, additions: number, deletions: number, tempPath: string }>,
  isInlineChange: boolean  // false for full-task diffs
}

// New type added to ChatMessage.type enum:
"task_card"
// task_card metadata:
metadata: { taskId: string }
```

`diff_card` rendered in `ChatPanel` as:
- File list with `+N / -N` per file
- **Apply** button → `controller.applyInlineChange(taskId)`
- **Discard** button → `controller.discardInlineChange(taskId)`
- **Maximize** (per file) → `vscode.diff(tempFile, realFile)` via `DiffService` — opens VS Code's native diff editor for detailed inspection; does NOT open the ReviewPanel WebView

`taskId` on the diff card refers to the inline `TaskRecord` created by `run_inline_change()` in `PLANNED` state. This ID is used exclusively to locate temp files for promote/discard — it does not represent a reviewable task in the ReviewPanel.

---

#### `CrucibleController.applyInlineChange(taskId)` / `discardInlineChange(taskId)`

- **Apply:** `POST /v1/chat/inline-changes/{taskId}/promote` → writes temp files to real workspace, updates `ChatThread.touched_files`, adds "Changes applied to N files" agent message to thread
- **Discard:** `DELETE /v1/chat/inline-changes/{taskId}` → deletes temp dir, adds "Change discarded" agent message to thread

New routes added to `api/routes.py`. No request body on either. Promote returns `{ task_id, promoted_files: list[str] }`. Delete returns `{ task_id }`.

---

#### `task_card` message type (large_change)

New `"task_card"` value in `ChatMessage.type` enum. Carries `taskId` in metadata. The controller's existing polling loop detects status transitions on that task and sends targeted update messages to the chat thread:

| Task status | Chat thread update |
|---|---|
| `CONTEXT_READY` | Planning tool call events stream as chat messages |
| `AWAITING_PLAN_APPROVAL` | Plan card added — user approves/rejects inline |
| `EXECUTING` | `operation_success` / `operation_error` events stream as messages |
| `READY_FOR_REVIEW` | Diff card added |
| `SUCCEEDED` / `FAILED` | Status message added |

---

## Streaming Architecture

### One Mechanism Everywhere: `EventBroadcaster`

The existing `PatchEventBroadcaster` is the foundation. It is **generalized and renamed** to `EventBroadcaster`, and its scope is extended beyond task-execution phases to cover every event source in the framework: chat agent explore phase, intent classification, QA responses, inline change execution, and large-change task status transitions.

**Current limitation:** `PatchEventBroadcaster` is keyed by `task_id` and only receives events from phases that have an active task record (`EXECUTING`, `VALIDATING`, etc.). The chat agent and the explore phase have no task_id and therefore no broadcaster channel. This gap is what produces the generator/yield anti-pattern in `handle_message()`.

**Fix:** Rename `PatchEventBroadcaster` to `EventBroadcaster`. Change the key parameter from `task_id: str` to `channel_id: str` everywhere. The broadcaster logic is unchanged — replay buffer, subscribe/unsubscribe, broadcast are identical. Any string can now be a channel: a task UUID, a chat message UUID, or any other identifier.

```python
# agentd/orchestrator/broadcaster.py — rename only
class EventBroadcaster:  # was PatchEventBroadcaster
    def subscribe(self, channel_id: str) -> asyncio.Queue[dict[str, Any]]: ...
    def unsubscribe(self, channel_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None: ...
    def broadcast(self, channel_id: str, event: dict[str, Any]) -> None: ...
    def clear_replay(self, channel_id: str) -> None: ...
```

`PatchEventBroadcaster` is kept as a backward-compatible alias (`PatchEventBroadcaster = EventBroadcaster`) so existing call sites in `engine.py`, `tools/loop.py`, and `planning/loop.py` compile without change.

### Unified event envelope

All events in the system use the same JSON shape:

```json
{"type": "<event_type>", "payload": {<event_data>}}
```

**Current ToolLoop events are flat** (`{"type": "operation_success", "op_type": "search_replace", "path": "foo.py"}`). These must migrate to nested payload so the frontend can parse all events with a single type union. The migration is one pass through `tools/loop.py` and the corresponding TypeScript `PatchStreamEvent` type.

Event catalogue (after migration):

| Source | `type` | `payload` fields |
|---|---|---|
| ChatAgent | `chat_agent_thinking` | `message: str` |
| ChatAgent | `explore_tool_call` | `tool: str, args: dict` |
| ChatAgent | `intent_classified` | `intent: str, rationale: str, likely_targets: list[str]` |
| ChatAgent | `chat_response` | `chunk: str` |
| ChatAgent | `chat_done` | `{}` |
| ChatAgent | `task_card` | `task_id: str` |
| ToolLoop | `tool_call` | `tool: str, args: dict` |
| ToolLoop | `tool_result` | `tool: str, output: str, is_error: bool` |
| ToolLoop | `operation_success` | `op_type: str, path: str` |
| ToolLoop | `operation_error` | `op_type: str, path: str, error: str` |
| ToolLoop | `diff_ready` | `task_id: str, diff_entries: list[DiffEntry]` |
| ToolLoop/Orchestrator | `done` | `{}` |
| Orchestrator | `task_status_changed` | `task_id: str, status: str` |

### `ChatAgent.handle_message()` — regular async coroutine, not a generator

`handle_message()` is no longer an async generator. It accepts a `channel_id` (generated per message by the route), uses the shared `EventBroadcaster`, and calls `broadcast(channel_id, event)` directly.

```python
# agentd/chat/agent.py — new signature
async def handle_message(self, thread_id: str, message: str, channel_id: str) -> None:
    self._broadcaster.broadcast(channel_id, {"type": "chat_agent_thinking", "payload": {"message": "Exploring workspace…"}})

    for _ in range(self._max_explore_calls):
        step = await self._transport.generate_json(...)
        if step.get("action") == "done":
            break
        tool_name = step.get("tool", "")
        args = step.get("args") or {}
        self._broadcaster.broadcast(channel_id, {"type": "explore_tool_call", "payload": {"tool": tool_name, "args": args}})
        tool_output = await self._registry.execute(tool_name, args)
        context.append(...)

    classification = await self._classifier.classify(message, context=context, history=history)
    self._broadcaster.broadcast(channel_id, {"type": "intent_classified", "payload": {...}})

    if classification.intent == IntentType.QA:
        response_text = await self._transport.generate_text(...)
        self._broadcaster.broadcast(channel_id, {"type": "chat_response", "payload": {"chunk": response_text}})

    elif classification.intent == IntentType.SMALL_CHANGE:
        plan_md = await self._draft_plan_markdown(message, context, classification.likely_targets)
        # run_inline_change is awaited directly — handle_message is already a background task
        await self._orchestrator.run_inline_change(
            goal=message,
            workspace_path=self._workspace_path,
            explore_context=context,
            likely_targets=classification.likely_targets,
            plan_markdown=plan_md,
            channel_id=channel_id,
        )

    elif classification.intent == IntentType.LARGE_CHANGE:
        task_id = await self._submit_large_change(message, context, thread_id=thread_id, channel_id=channel_id)
        self._broadcaster.broadcast(channel_id, {"type": "task_card", "payload": {"task_id": task_id}})

    self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
```

`EventBroadcaster` is injected into `ChatAgent.__init__()` as `broadcaster: EventBroadcaster`. The same instance is shared with `AgentOrchestrator` so inline change ToolLoop events and chat events all go through one broadcaster on their respective channel IDs.

### Chat message route — same pattern as `/stream-patch`

```python
# agentd/api/routes.py
@router.post("/v1/chat/threads/{thread_id}/message")
async def send_message(thread_id: str, body: SendMessageRequest) -> StreamingResponse:
    channel_id = f"chat:{uuid4().hex}"
    queue = broadcaster.subscribe(channel_id)
    asyncio.create_task(chat_agent.handle_message(thread_id, body.message, channel_id))

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "chat_done":
                    break
        finally:
            broadcaster.unsubscribe(channel_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

Identical pattern to `/stream-patch`. No special-casing.

### ToolLoop — `broadcast_key` for inline changes

`ToolLoop.__init__()` gains an optional `broadcast_key: str | None = None`. When set, all `self._broadcaster.broadcast(self._task_id, ...)` calls use `broadcast_key` instead. When `None` (default), `task_id` is used — existing call sites are unaffected.

For inline changes: `run_inline_change()` creates a ToolLoop with `broadcast_key=channel_id`. ToolLoop events (tool calls, patch operations, `diff_ready`) flow to the chat channel, not the task channel. The task still has its own `task_id` used for the promote/discard API.

### Event flow — small_change (end to end)

```
POST /v1/chat/threads/{id}/message
    │
    channel_id = "chat:abc123"
    broadcaster.subscribe(channel_id) → queue
    asyncio.create_task(handle_message(..., channel_id))
    return StreamingResponse(queue)
    │
    ├── broadcast("chat_agent_thinking")
    ├── broadcast("explore_tool_call") × N           ← real-time as each tool fires
    ├── broadcast("intent_classified")
    ├── broadcast("chat_agent_thinking", "Drafting plan…")
    │
    └── run_inline_change(..., channel_id="chat:abc123")
            │
            ├── ToolLoop step 1 [broadcast_key="chat:abc123"]
            │     ├── broadcast("tool_call")          ← real-time
            │     ├── broadcast("tool_result")
            │     └── broadcast("operation_success")
            ├── ToolLoop step 2 ...
            └── broadcast("diff_ready", {task_id, diff_entries})
                    │
    broadcast("chat_done")
                    │
                    ▼
    Frontend: diff card shown in chat thread
```

### Event flow — large_change

The task's own broadcaster channel (`task_id`) remains the primary stream for the ReviewPanel (`/stream-patch`). When the task originates from chat, the orchestrator forwards these status transitions to the `channel_id` stored on the task record:

| Status reached | Event broadcast to `channel_id` |
|---|---|
| `AWAITING_PLAN_APPROVAL` | `{"type": "task_status_changed", "payload": {"task_id": ..., "status": "AWAITING_PLAN_APPROVAL", "plan_markdown": ...}}` |
| `EXECUTING` | `{"type": "task_status_changed", "payload": {"task_id": ..., "status": "EXECUTING"}}` |
| `READY_FOR_REVIEW` | `{"type": "task_status_changed", "payload": {"task_id": ..., "status": "READY_FOR_REVIEW"}}` |
| `SUCCEEDED` / `FAILED` | `{"type": "task_status_changed", "payload": {"task_id": ..., "status": ...}}` |

The `channel_id` is stored as `TaskRecord.chat_channel_id: str | None = None`. The orchestrator checks this field at each status transition and broadcasts to it if set.

### `TaskRecord` additions

```python
is_inline_change: bool = False       # True for run_inline_change() tasks
chat_channel_id: str | None = None   # broadcaster channel for chat-originated tasks
```

---

## Context Plumbing — Small Change

```
explore_context (list of tool results)
    │
    ├──→ format_explore_as_retrieval_supplement()
    │         → file_contents (from read_file calls)
    │         → planner_evidence (from search_code / search_semantic)
    │                   │
    │                   ▼
    │    retrieval_client.load_context(workspace_path, goal, hint_files=likely_targets)
    │         → full RetrievalContext (file outlines, symbol graph, diagnostics)
    │                   │
    │                   ▼
    │         merge: explore supplement fills in already-read files,
    │                retrieval client provides everything else
    │                   │
    │                   ▼
    │    create_plan(task, shadow_path, retrieval_context)
    │         task.plan_markdown = draft (from _draft_plan_markdown)
    │         → PlanDocument with full implementation_details,
    │           edge_cases, testing_strategy per step
    │
    └──→ plan_markdown draft (from _draft_plan_markdown)
              set as task.plan_markdown
              used by create_plan() as MANDATORY AUTHORITATIVE BLUEPRINT
              (PLAN_SYSTEM_INSTRUCTIONS unchanged)
```

---

## Context Plumbing — Large Change

`TaskCreateRequest` gains one optional field:

```python
# ExploreToolResult matches the shape already produced by ChatAgent's explore phase:
# {"tool": str, "result": str, "is_error": bool}
initial_explore_context: list[dict[str, object]] | None = None
```

`run_task()` checks for this field. When present:
- Included in `plan_context_payload` passed to `PlanningAgent.generate_plan()` as `initial_explore_context`
- `build_planning_step_payload()` serialises it into the user payload
- Planning system prompt tells the planner to treat it as pre-gathered evidence

No change to the planning loop logic itself. `PlanningLoop.run()` already accepts `initial_context` — this field flows through it.

---

## Error Handling

| Failure point | Behaviour |
|---|---|
| `_draft_plan_markdown()` throws | `showError` in chat, abort — user retries |
| `retrieval_client.load_context()` throws | Fall back to explore context only, warn in chat ("Using limited context"), continue |
| `create_plan()` throws / invalid schema | Error in chat, temp files discarded, TaskRecord marked FAILED |
| `prepare_lightweight()` throws | Error in chat, no shadow created, abort cleanly |
| ToolLoop step fails (retries exhausted) | Partial diff card with warning: "Applied N of M steps — review before accepting." User accepts partial or discards |
| `applyInlineChange` (promote) throws | Error in chat, temp files kept — user can retry |
| `discardInlineChange` throws | Log warning, force-delete temp dir — discard always succeeds |
| `run_command` (test-on-demand) exits non-zero | Test result shown in chat (`✗ 2 failed`) alongside diff card — user decides whether to apply |
| VS Code closed mid-flight | Orphaned temp dirs swept on next session init (older than 24h) |
| TaskRecord left in PLANNED state on crash | `run_inline_change()` marks FAILED in a `finally` block before re-raising |

---

## Testing Approach

- Python: new `tests/test_orchestrator_inline_change.py` using `ScriptedReasoningEngine` + `InMemoryTaskStore` + `tmp_path` shadow. Tests: happy path, step failure with partial diff, `skip_verify` flag behaviour, TaskRecord cleanup on error.
- TypeScript: extend `controller.test.ts` stub backend with `applyInlineChange` / `discardInlineChange`. Test diff card rendering and Apply/Discard controller methods.
- No new integration tests needed — `ToolLoop`, `PatchEngine`, `create_plan()` already have integration coverage. New tests focus on the glue code and new entry points.
