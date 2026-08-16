# Chat Rewind — Design

**Date:** 2026-08-16
**Status:** Approved, ready for implementation planning
**Scope:** `services/agentd-py` (chat + memory), `apps/editor-client`, `apps/vscode-extension`

## 1. Problem

Chat controller edits are **instant-promoted** to the real workspace: `TurnEditSession.accept()`
calls `promote_files` the moment an edit is accepted, and `close()` `rmtree`s the turn shadow at
turn end. Once the edit gate resolves, there is no way back. A user who watches the agent take a
wrong path across two or three turns has no way to return the workspace — or the conversation —
to the state before it started.

This matters more here than in a hosted product, because the primary targets are local and weaker
models (TurboQuant, Ollama), which need conversational do-overs routinely.

**Nothing in the repo does this today.** The checkpoint machinery in `orchestrator/engine.py`
(`_create_shadow_checkpoint`, `_restore_shadow_checkpoint`, `pre_execution_checkpoint`) is
task-subsystem-only, snapshots the **shadow** rather than the real workspace, is keyed and pruned
per task, and is cleared at task terminal. The task subsystem is OFF by default. Every occurrence
of "checkpoint" under `agentd/chat/` is the word inside a prompt string (the todo-reconcile hint),
not a mechanism.

Reverse-applying the `unified_diff` already persisted on each `diff_card` was evaluated and
rejected: `patch/diffing.py` caps diffs at 400 lines / 24,000 chars, so reverse-application would
silently produce wrong content on exactly the large edits where rewind matters most.

## 2. Decisions

| Decision | Choice |
|---|---|
| Restore scope | Conversation **and** files, always together — no per-rewind sub-choice |
| Granularity | One checkpoint per user message (turn) |
| Discard model | Permanent truncation, behind a confirm dialog. No undo, no branching |
| File blast radius | Only files the agent touched. Never the user's manual edits elsewhere |
| Anchor message | Removed, and its text prefills the composer for edit-or-resend |
| Memory | Memories written by rewound turns are retired; the compaction anchor is restored |
| Live task in span | Refuse the rewind |
| Exec sessions in span | Allowed, never killed, named in the confirm dialog |
| Command side effects | Not undone. Stated plainly in the confirm dialog |

The task/session split is deliberate. Claude Code's rewind blocks on nothing and tears down
nothing — background shells keep running, and command side effects are documented as not undone.
Exec sessions are exactly background shells, so they follow that precedent. A task is not a passive
side effect: it holds its own shadow workspace and promotes files into the real workspace when it
finishes, so a task still running after a rewind will re-write the files just reverted. That is an
active writer racing the rewind, and it is refused.

## 3. Anchor: `ChatMessage.id`

`ChatMessage` gains:

```python
id: str | None = None
```

`ChatThreadStore.append_message` stamps a `uuid4().hex` on every new message.

**The field must be nullable, never a `default_factory`.** `ChatMessage.model_validate(m)` runs
against raw dicts loaded from `messages_json`, so a factory would mint a *fresh random id on every
read* for every message persisted before this feature. The UI would offer rewind anchors that match
no checkpoint and 404 differently on each poll. Nullable means pre-existing messages carry
`id: None`, the affordance does not render on them, and rewind works from the next message onward.

Mirrored in three places, per the `PendingGate.kind` / `PlanStepSchema.targets` footgun class:

1. `services/agentd-py/agentd/chat/models.py`
2. `apps/editor-client/src/contracts/task-contracts.ts` (Zod)
3. `apps/vscode-extension/webview-ui/src/types.ts` (local mirror)

## 4. Storage

One new table in the existing `chat.sqlite3`, migrated with the same `PRAGMA table_info` /
`CREATE TABLE IF NOT EXISTS` discipline as the rest of `ChatThreadStore._migrate`:

```sql
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
```

`files_json` holds `[{path, existed, oversize}]`. File bytes live on disk:

```
<workspace>/.crucible/state/rewind/<thread_id>/<seq>/files/<relpath>
```

### Why the controller blobs are snapshotted whole

`controller_history_json` is a flat list of assistant-action / tool-result pairs with **no
alignment to transcript messages** — a single transcript message can correspond to many history
entries, and mid-turn gates add more. "Truncate the history to match the transcript" therefore has
no correct implementation. Restoring a verbatim earlier copy sidesteps alignment entirely, and
picks up the todo ledger, the active skill, and the pinned retrieval seed for free.

Compaction already bounds history to roughly `0.65 × window_tokens`, so one copy per turn is
tens-to-hundreds of KB — acceptable against a per-thread retention cap.

## 5. Capture — copy-on-first-write

New module `agentd/chat/rewind.py`, exposing `RewindStore`.

**Opening a checkpoint.** `ChatController.handle_message` calls
`open_checkpoint(thread_id, anchor_message_id, turn_id)`, which allocates `seq = max(seq) + 1` for
the thread and writes the row.

The four `controller_*` blobs are snapshotted from the thread object `handle_message` **already read
at the top of the function** — i.e. pre-turn values, before `_run_loop` mutates them.

`memory_anchor_md` does **not** come from the thread: the anchor lives in `memory.sqlite3`, read via
`MemoryStore.get_anchor(thread_id)` (the controller's `run_id` is the `thread_id`). It is
best-effort and nullable — memory may be disabled, or no compaction may have happened yet, in which
case the column stays `NULL`.

**Capturing files.** `TurnEditSession.__init__` takes an optional
`checkpoint_cb: Callable[[list[str]], Awaitable[None]] | None`. `apply()` invokes it with the
touched paths **before** `_ensure_shadow`, so copies are taken from the real workspace while it is
still the clean before-state — the invariant `edit_session.py`'s own module docstring asserts
("`shadow == real` at every patch boundary, so real is the clean before for the next patch").

The callback keeps `TurnEditSession` dependency-light: it takes a function, not a store, matching
the shape of its existing collaborators.

`capture(thread_id, paths)`:

- copies only paths not already captured in the current (highest-seq) checkpoint
- records `existed: bool` — a newly created file records `existed: false` and copies nothing
- skips files over `CRUCIBLE_REWIND_MAX_FILE_BYTES` (default 10 MB), recording `oversize: true`
- is a **silent no-op** when the thread has no checkpoint (e.g. the `_promote_orphaned_edit`
  restart-recovery path). Degrade, never raise.

**Continuation turns.** `resolve_mode` → `"implement"` and `resolve_clarify` re-enter `_run_loop`
without a new user message. They write into the thread's **highest** seq — same anchor, so no
open/closed bookkeeping and no extra column is required.

**Rejected edits.** A capture taken for an edit the user then rejects costs one wasted copy whose
content equals current real content, so restoring it later is a no-op. Harmless.

## 6. Restore

`RewindStore.restore(thread_id, target_seq)`:

1. Load all checkpoints with `seq >= target_seq`, ascending.
2. Fold into `path → entry` with **first-seen wins**. The oldest recorded pre-state is the correct
   one; this is what makes multi-turn rewind correct without storing full snapshots.
3. For each entry: `existed` → copy the stored bytes back over the real path (creating parent
   dirs); `not existed` → delete the real file if present. Directories are left in place.
4. Truncate `messages_json` so that **the anchor message itself and everything after it are
   removed** (the anchor's text is returned as `prefill_text`). Restore the four `controller_*`
   blobs from the target row, and clear `pending_controller_gate`.
5. Restore the memory compaction anchor. Non-null `memory_anchor_md` → `upsert_anchor`; **null →
   `clear_anchor(thread_id)`**, a small new `MemoryStore` method that deletes the row. Leaving a
   newer anchor in place when the checkpoint recorded none would reintroduce exactly the leak §8
   closes.
6. Delete rows and on-disk dirs for `seq >= target_seq`. Permanent, by decision.

`prefill_text` is the anchor message's persisted `content` — the short display text, **not** the
`@`-mention-expanded `turn_message`, which was turn-scoped and deliberately never stored.

Per-file failures are **collected and reported, never aborted on**. A rewind that half-worked must
say so; stopping silently partway is the worst outcome for a destructive operation.

`oversize` entries are reported as not-restored in the result, since nothing was stored for them.

Retention: `CRUCIBLE_REWIND_RETENTION_TURNS` (default 50) prunes oldest checkpoints per thread.

## 7. Routes and contracts

A rewind is destructive, so the confirm dialog cannot be built from a call that already performed
it. Two routes:

```
GET  /v1/chat/threads/{id}/rewind-preview?message_id=
  -> {messages, files, sessions: [{id, command}], commands_run, blocked_by_task: str | null}

POST /v1/chat/threads/{id}/rewind  {message_id}
  -> {removed_messages, restored_files, deleted_files,
      failed: [{path, error}], retired_memories, prefill_text}
```

Preview field definitions, so the dialog's numbers mean one thing:

- `messages` — how many transcript messages will be removed, anchor included.
- `files` — count of **distinct** paths across the folded span (§6 step 2), not the sum per turn.
- `commands_run` — count of persisted `tool_events` in the span whose tool is `run_command` or an
  exec-session start. Drives the "these are not undone" line.
- `sessions` — exec sessions started in the span that are **still alive**; they are named, never
  killed.
- `blocked_by_task` — the offending task id when the span holds a non-terminal task, else null.
  Non-null means `POST` will 409, so the UI disables confirm and explains.

Refusals:

- **409** — a turn is in flight for the thread (`ChatController._active_turns`)
- **409** — the truncated span references a task that is not terminal. Found by collecting
  `task_id` off messages in the span and reading status from the task store `build_router` already
  closes over
- **404** — unknown or null `message_id`

`apps/editor-client` gains `previewRewind` / `rewindThread` on `BackendTaskClient`, with Zod
`RewindPreview` / `RewindResult` and the usual snake_case → camelCase mapping.

## 8. Memory

```python
def retire_since(self, source_ref: str, cutoff_iso: str) -> int:
    """UPDATE memories SET valid_to = ?
       WHERE source_ref = ? AND valid_to IS NULL AND created_at >= ?"""
```

`source_ref` is the consolidator's `run_id`, which for the controller **is the `thread_id`**, so a
single predicate catches both thread- and workspace-scoped memories written by rewound turns —
**no schema change**. `RecallEngine` already filters `valid_to IS NULL` before scoring, so
retirement takes effect with no changes on the recall path and no vec/FTS index surgery.

The `cutoff_iso` passed is the **target checkpoint's `created_at`** — i.e. the moment the rewound
span began.

Best-effort in a `try/except`: memory must never fail a rewind, the same discipline
`prepare_turn` already follows. On failure, `retired_memories` reports `0` and the error is logged
with `exc_info`; the file and transcript restore still stand.

**`remember()` gap.** `Consolidator.write_explicit` is called with `run_id=""`, so explicitly
remembered facts would escape retirement. Thread `run_id=thread_id` through `MemoryToolSource` —
two call sites.

**Compaction anchor.** The anchor is keyed by `run_id` = `thread_id` and injected as
`[MEMORY] Summary of earlier conversation…`, so without intervention it keeps summarizing turns
that no longer exist — the same contradiction retirement exists to remove, arriving by a different
door. Since the checkpoint row already snapshots four blobs, `memory_anchor_md` is a fifth, restored
via `upsert_anchor` on rewind.

Stale `compaction_segments` rows are **accepted and left alone**: they feed only the summarizer and
the consolidator's A+link render, both downstream of the anchor that is now restored.

## 9. Frontend

- **`MessageRow.tsx`** — a hover-revealed `↺` button on user messages, rendered only when
  `msg.id != null && !turnActive`. Messages predating the feature carry no id and do not offer it.
- **Confirm modal** — built on the existing `.surface-card` / `.scrim` primitives from the webview
  design system. It states the counts from `rewind-preview`, and states plainly what rewind does
  *not* do: commands the agent ran are not undone, and live exec sessions keep running (named
  individually).
- **On confirm** — call `rewindThread`, then **re-fetch the thread and replace the transcript
  wholesale** rather than mutating it client-side; the server is authoritative about what survived.
  Then post `composerPrefill` so `InputArea` loads the anchor's original text as an editable draft.

Host plumbing follows the existing pattern: webview message → `chat-panel.ts` → `controller.ts`
(which stays vscode-API-free) → editor-client.

## 10. Testing

Python, TDD:

- `RewindStore` units — first-seen-wins across three turns; created-file deletion; oversize skip;
  no-checkpoint no-op; partial-failure reporting.
- Route tests for each refusal (409 turn active, 409 live task, 404 bad id) and the happy path
  including `retired_memories`.
- One integration test on a real `tmp_path` workspace with the real `PatchEngine` and a real
  `ChatThreadStore`, asserting file bytes on disk before and after. **Not `InMemoryTaskStore`** —
  it returns the same object reference and masks exactly the state-divergence bugs this feature
  can produce.

TypeScript, vitest: button visibility rules (`id === null` → hidden, `turnActive` → hidden), the
confirm modal, transcript replacement + prefill in `useAppState`, and an editor-client contract
test for both new calls.

## 11. Out of scope

Branching; undo-of-rewind; a conversation-only / code-only split; mid-turn granularity; restoring
the user's own manual edits; undoing command side effects; any git integration.

## 12. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CRUCIBLE_REWIND_RETENTION_TURNS` | `50` | Checkpoints retained per thread |
| `CRUCIBLE_REWIND_MAX_FILE_BYTES` | `10000000` | Files above this are not captured (`oversize`) |
