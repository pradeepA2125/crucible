# Chat Interface Design

**Date**: 2026-05-11
**Status**: Draft

---

## Goal

Replace the task-submission form model with a persistent chat thread per workspace. The chat agent wraps all task execution — Q&A, small inline edits, and large multi-file features — routing each message to the appropriate execution path without the user needing to think about which mode they're in.

---

## Motivation

The current model is a supervised batch job: submit goal → approve plan → watch execution → accept patch. It works well for large features but is too heavy for small changes and doesn't support natural back-and-forth. Claude Code, Cursor, and Windsurf all converge on a chat-first interaction model. This design adopts that model while keeping the plan approval gate — a deliberate differentiator for complex tasks where the user wants to verify intent before execution.

---

## Architecture Overview

A new `ChatAgent` class sits above `PlanningAgent` and `ToolLoop`. It owns the conversation thread and routes every incoming message to one of three execution paths based on intent classification. The task lifecycle, shadow workspace, and plan approval machinery are unchanged — they are invoked by `ChatAgent` when needed, not replaced.

```
User message
     │
     ▼
ChatAgent.handle_message()
     │
     ├─ classify_intent()   ← reads likely target files, judges scope
     │
     ├─ Q&A ──────────────► respond inline, no task created
     │
     ├─ Small change ──────► ToolLoop (no plan) → temp patch → VS Code diff → accept/reject
     │
     └─ Large / /plan ─────► PlanningAgent → plan card in chat → ToolLoop → shadow → accept
```

---

## Intent Classification

`ChatAgent` performs a lightweight explore pass before routing:

1. Searches for likely target files from the message (filename mentions, symbol names, feature hints)
2. Reads those files briefly (first N lines or symbol context)
3. Judges scope: number of files likely touched, whether interfaces change, whether new files are needed

**Classification output** — `IntentType`:
- `qa` — no file changes needed; question, explanation, or discussion
- `small_change` — 1-2 files, localised edit, no interface changes, no new files
- `large_change` — 3+ files, interface or schema changes, new files, or ambiguous scope

**`/plan` override** — any message starting with `/plan` forces `large_change` routing regardless of scope.

**Misclassification recovery** — if a `small_change` task hits `revision_needed` (scope is larger than expected), `ChatAgent` escalates to `large_change` routing: it spawns a full shadow workspace, runs `PlanningAgent`, and presents a plan card. The user is notified inline ("This is larger than expected — generating a full plan").

---

## Execution Path 1: Q&A

- No task record created, no workspace touched
- `ChatAgent` makes a single LLM call with codebase context (retrieval snapshot + recent thread history)
- Response streamed as a chat message
- If the user follows up with a code change request, that is a new message routed normally

---

## Execution Path 2: Small Change (Inline Edit)

Designed for single-file or two-file edits where the user wants an immediate result without a planning phase.

**Execution:**
1. `ChatAgent` creates a lightweight `TaskRecord` (for history and auditability) but skips `PlanningAgent`
2. `ToolLoop` runs directly: explore → emit patch → verify phase
3. Patch is applied to a **temp buffer** (not the real file, not a full shadow workspace clone)
4. VS Code extension opens a native side-by-side diff view (`vscode.diff`) between the original and the patched buffer
5. A "diff card" appears in the chat thread with **Accept** / **Reject** actions
6. **Accept** → patch written to real file; task marked `SUCCEEDED`
7. **Reject** → temp buffer discarded; task marked `ABORTED`; user can follow up in thread

**Why no shadow workspace:** Shadow workspace overhead (full directory clone, checkout, promotion) is disproportionate for single-file edits. A temp buffer + native VS Code diff gives equivalent reversibility with near-zero latency. The verify phase still runs (linters/tests) — against the temp buffer — before showing the diff.

**Scope guard:** If `ToolLoop` attempts to write more than 2 files, `ChatAgent` intercepts, discards the temp buffer, and escalates to `large_change` path with a message to the user.

---

## Execution Path 3: Large Change (Full Task)

The existing task lifecycle runs unchanged, surfaced through the chat thread instead of a separate panel.

**Execution:**
1. `ChatAgent` calls `PlanningAgent.generate_plan()` — explore-then-commit ReAct loop
2. When plan is ready, a **plan card** appears in the chat thread (rendered markdown + file list)
3. User replies in the thread to approve, reject, or give feedback — natural language, no buttons required
4. `ChatAgent` watches for the reply and classifies it:

| Reply pattern | Action |
|---------------|--------|
| "looks good", "go ahead", "yes", "approved", "lgtm" | approve (null feedback) |
| "change X, everything else is good. go ahead." | extract feedback → replan → auto-approve |
| "change X" (no approval signal) | extract feedback → replan → present new plan card |
| "stop", "cancel", "abort" | abort task |

5. After approval, `ToolLoop` executes with full shadow workspace
6. On completion, a **diff summary card** appears in the thread (files changed, lines added/removed)
7. User accepts or rejects from the thread; promotes or discards shadow workspace

**`/plan` mode** — explicit user control. Forces this path regardless of scope. Useful when the user wants to review the plan even for a small change.

---

## Plan Approval: Button + Optional NLP

The plan card has two interaction paths:

**Primary — "Implement Plan" button**
Single click → immediate approval, null feedback, execution begins. No NLP required. This is the happy path for users who are satisfied with the plan.

**Secondary — feedback text field**
If the user types anything, `ChatAgent` classifies the text:

| Text pattern | Action |
|--------------|--------|
| Contains approval signal ("go ahead", "lgtm", "ship it") at end | extract feedback → replan → auto-approve |
| No approval signal | extract feedback → replan → present new plan card with "Implement Plan" again |
| "stop", "cancel", "abort" | abort task |

```python
class PlanReplyIntent(BaseModel):
    action: Literal["approve_after_feedback", "feedback_only", "abort"]
    feedback: str  # always present when text is submitted
```

NLP is **only invoked when the user submits text** — the button bypasses it entirely. This eliminates ambiguity on the happy path and confines NLP to the case where it's actually needed (parsing mixed feedback + intent from free text).

Internally, both paths call the same `POST /v1/tasks/{id}/plan/feedback` endpoint — `ChatAgent` translates the button click to `feedback=null` and the NLP result to the appropriate feedback string.

---

## Workspace Continuity

- Each accepted task (small or large) updates the real workspace
- `ChatAgent` maintains a session-level `touched_files` list — files modified since the chat session started
- This is injected into every subsequent task's planning context as `prior_session_files`
- The `PlanningAgent` uses this to avoid re-reading unchanged files and to understand what has already been done in this session

---

## VS Code UX

### Chat Panel
- New sidebar WebView panel (same side as the existing review panel)
- Persistent thread rendered as a conversation: user bubbles on the right, agent bubbles on the left
- Special bubble types: **plan card**, **diff card**, **inline diff link**

### Plan Card
- Rendered markdown of the plan
- File list with intent (new / existing / modified)
- **"Implement Plan" button** — primary action, single click to approve and execute
- **Feedback text field** — below the button; submitting text triggers NLP classification (feedback only, or feedback + approve in one message)

### Diff Card (small change)
- "N lines changed in `path/to/file.py`" with a link that opens the VS Code native diff
- Accept / Reject buttons inline in the card
- Verify phase output (lint/test result) shown as a collapsible section

### Chat Input
- Single input at the bottom of the panel
- `/plan` prefix forces large-change routing
- `@file` mentions attach file context explicitly (Phase 5 extension — not in this spec)

---

## API Changes

### New endpoints
- `POST /v1/chat/message` — send a user message; returns `stream` of `ChatEvent`
- `GET /v1/chat/history?workspace=<path>` — load thread history for a workspace

### New event types (SSE)
- `chat_agent_thinking` — classification in progress
- `chat_response` — Q&A text chunk
- `intent_classified` — routing decision (type + rationale)
- `inline_diff_ready` — temp buffer ready; payload includes file path and diff stats
- `plan_card` — plan markdown ready for approval
- `diff_summary_card` — task complete; files changed summary

### Existing endpoints unchanged
- All `/v1/tasks/*` routes remain — `ChatAgent` calls them internally
- The review panel continues to work for tasks submitted via the existing task form

---

## Data Model

### `ChatThread`
```python
class ChatMessage(BaseModel):
    role: Literal["user", "agent"]
    content: str
    type: Literal["text", "plan_card", "diff_card", "diff_summary"] = "text"
    task_id: str | None = None
    timestamp: datetime

class ChatThread(BaseModel):
    thread_id: str
    workspace_path: str
    messages: list[ChatMessage] = []
    touched_files: list[str] = []  # files modified this session
```

### Storage
- SQLite table `chat_threads` — one row per workspace, `messages` stored as JSON
- Thread loaded on VS Code activation, persisted on every message

---

## Open Questions

1. **Interruption during execution** — if the user sends a message while a task is running, the first version queues it (no cancellation). Interruption/cancellation is a follow-on feature.

2. **Small change threshold** — "1-2 files" is the intent; the classifier judges this from the explore pass. Edge cases (e.g. a 2-file change that touches a public interface) may get escalated mid-execution via the scope guard.

3. **Multi-workspace** — one thread per workspace root. If the user has multiple workspace windows open, each has its own thread. No cross-workspace context.

4. **Existing review panel** — remains available for tasks submitted via the task form (backward compatibility). Long-term it may be retired in favour of the chat diff card.

5. **Thread length management** — very long threads (100+ messages) will need summarisation to avoid context window overflow on the classification LLM call. Deferred to a follow-on.

---

## What This Spec Does Not Cover

- `@file`, `@symbol`, `@url` context attachments (Phase 5 extension)
- Agent autonomy modes (`auto-plan`, `autonomous`) — separate feature, integrates with this chat model
- Web search tool — separate feature, available to `ChatAgent` as a tool once built
- GitHub / PR integration — separate feature
