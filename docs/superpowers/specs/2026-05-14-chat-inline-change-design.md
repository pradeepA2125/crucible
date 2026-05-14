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

- Replacing the review panel (it remains available as "Maximize" from the diff card)
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
- **Maximize** (per file) → `vscode.diff(tempFile, realFile)` via `DiffService`

---

#### `AiEditorController.applyInlineChange(taskId)` / `discardInlineChange(taskId)`

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
