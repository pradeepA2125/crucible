# Chat UI Redesign Implementation Plan — Rev 2.1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `media/chat.js` (vanilla JS) with a React 18 + TypeScript + Tailwind v4 webview delivering the hi-fi design in `docs/superpowers/design/chat-ui-hifi.html`.

**Architecture:** New standalone Vite app at `apps/vscode-extension/webview-ui/`. `chat-panel.ts` reads its compiled `dist/index.html` instead of inlining HTML. The extension keeps proxying ALL backend traffic (SSE, /live poll, decisions) — the webview talks only `postMessage`, exactly as today. Backend changes are **minimal and additive** — four SSE-emission changes, no API routes or state-machine changes — and each is scoped per feature in "Feature wire contracts" below; the frontend builds against *verified* payloads, never assumed ones.

**Tech Stack:** React 18, TypeScript 5, Tailwind CSS v4 (`@tailwindcss/vite`), Vite 6, `react-markdown`, `@testing-library/react`, `vitest`

**Spec:** `docs/superpowers/specs/2026-06-09-chat-ui-redesign-design.md`
**Visual source of truth:** `docs/superpowers/design/chat-ui-hifi.html` (supersedes the wireframes and the spec's colour table)

---

## Rev 2 — what changed vs Rev 1 and why

Rev 1 was written under compacted context and contained claims that do not survive contact with the code. Verified against `chat-panel.ts`, `chat.js`, `controller.ts`, `extension.ts`, `review-panel.ts`, `task-contracts.ts`, `chat/live_state.py`, `chat/storage.py`, and `orchestrator/engine.py`:

1. **`unified_diff` does not exist.** No payload anywhere carries diff text. `DiffEntry` is `{path, additions, deletions, temp_path}` (snake_case on the wire — SSE/live payloads are NOT case-mapped by `HttpBackendClient`). `engine._compute_diff_entries` computes a unified diff only to count +/− and discards it. → DiffCard v1 renders file rows + stats + "open in VS Code diff" (`viewDiffFile`, same as today). Inline diff text is a v2 item requiring a small backend addition.
2. **The tool-pill parser parsed a format that never occurs.** Thinking entries are flattened prose (`"read_file routes.py — thought…"`, `"planning: search_code — …"`); `isToolEntry`/`parseToolEntry("tool(args)\noutput")` would match almost nothing and tool output is never forwarded at all. → New structured `appendToolEvent`/`toolResult` extension→webview messages (Task 5). Extension-side only.
3. **MessageRow dispatched on `"role" in msg` first — but every persisted `ChatMessage` has BOTH `role` and `type`** (Zod: role required, type defaults `"text"`). plan_card/diff_card/task_card messages would have rendered as plain text forever. → Dispatch on `type` first, like chat.js.
4. **GateCard used hooks inside `if (kind === …)` branches** (Rules-of-Hooks violation) → split into four components.
5. **Live-slot React instance reuse:** a second gate of the same kind (multiple command approvals per task is the NORM) would reuse the mounted component and its stale `resolved` state → buttons permanently gone. → Key live cards by content signature; same for the live plan (feedback regen must reset local state).
6. **Deleting ReviewPanel removed the only accept/reject UI for large-change tasks.** `applyInlineChange` ≠ `acceptPatch`; `controller.acceptPatch()` only targets the legacy session task. → New **ReviewCard** in the live slot, driven by `live.status === "READY_FOR_REVIEW"`, with taskId-parameterized accept/reject (Task 11/13).
7. **ErrorCard was never rendered anywhere and its actions posted fake `/resume` slash-commands into chat.** → Live-state-driven ErrorCard (status FAILED/ABORTED) wired to a real `resumeTask` message. Thread `active_task_id` repoints on resume (`storage.set_active_task`), so the /live poll follows the child task automatically.
8. **`appendThinkingEntry` reducer overwrote the previous entry with the active chunk** (data loss). chat.js semantics: seal the streaming chunk as its own entry, then append the new one.
9. Smaller fixes: `reattach()` must reset `webview.options` (it IS settable on restored panels); `(h>>>0).toString(36)` not `String(h>>>0).toString(36)`; command-gate "exact" rule must shlex-join (port `shlexJoin` from chat.js — backend rule matching depends on it); ThinkingBlock must collapse when streaming ends; HistoryView gets `createdAt` (already on `ChatThreadSummary` — just pass it through) for day grouping; webview-ui is not an npm workspace so root scripts must invoke it explicitly.
10. **Visual layer upgraded** from the lo-fi wireframe styling (emoji, text arrows, flat borders, `#141414/#9d6ff0`) to the hi-fi token system, SVG icon sprite, and motion set.

Verified-correct and kept from Rev 1: the full `ExtensionMessage`/`WebviewMessage` unions (match `chat-panel.ts` exactly), gate payload shapes (match backend `pending_*` models), plan-card dedup by task+content signature, the Vite/Tailwind scaffold, CSP/nonce/asset-rewrite approach, and Task 1 (commit prerequisite).

**Rev 2.1** re-verified every feature against the actual backend broadcast sites (`tools/loop.py`, `planning/loop.py`, `chat/agent.py`, `orchestrator/engine.py`). Rev 2's claim that tool pills were "extension-side only" was wrong for two of the three tool sources. The section below is now the contract the frontend is built against.

---

## Feature wire contracts (verified against source — build against THIS, not assumptions)

### Channel primer

| Channel | Key | Carried by | What flows on it |
|---|---|---|---|
| **Task channel** | `task_id` | `GET /v1/tasks/{id}/stream-patch` SSE | engine lifecycle (`task_status_changed`, `done`), execution ToolLoop events (plan tasks), gate-request events, planning-loop mirror, `plan_card`/`chat_breadcrumb` mirror |
| **Chat channel** | `chat_channel_id` (UUID) | `POST /v1/chat/threads/{id}/message` SSE (one turn) | ChatAgent explore/classify/QA events, inline-change ToolLoop (via `broadcast_key`), planning-loop mirror (`planning/loop.py:92-95`), `plan_card`/`chat_breadcrumb`, `chat_done` |
| **/live poll** | thread id | `GET /v1/chat/threads/{id}/live` (extension polls 1s) | `{active_task_id, status, pending_gate, plan}` derived from persisted task state — durable across reload/resume |

Extension consumption: `controller.sendChatMessage` reads the chat SSE during a turn; `controller.streamTaskIntoChatThread` reads the task SSE after plan approval; `pollThreadLiveState` reads /live. SSE payloads are **not case-mapped** by `HttpBackendClient` — snake_case fields arrive verbatim.

### F1 — Tool pills with expandable Input/Output (hi-fi frame 2)

Three distinct tool sources; each has a different wire reality:

| Source | Call event (payload) | Result event (payload) | Gap |
|---|---|---|---|
| **Chat explore** | `explore_tool_call` `{tool, args, thought}` — `chat/agent.py:199` ✓ args | **none** — result goes only into internal `context` (`chat/agent.py:208`) | result event missing entirely |
| **Planning loop** | `planning_tool_call` `{tool, thought, iteration}` — `planning/loop.py:410` — **NO args** | `planning_tool_result` `{tool, output[:500], is_error, iteration}` — `planning/loop.py:424` ✓ | args missing on call |
| **Execution loop** | `tool_call` `{tool, thought[:300], iteration, phase, args}` — `tools/loop.py:906` ✓ args | `tool_result` `{tool, output[:500], is_error, iteration}` — `tools/loop.py:1011` ✓ | output cap 500 chars is tight for the Output panel |

Correlation: there is no call id on the wire; calls and results are strictly sequential within a loop (one tool in flight), so the extension pairs each result with the latest open call **per source**.

**Changes — Backend (Task 5a):**
1. `planning/loop.py:410` — add `"args": args` to the `planning_tool_call` payload.
2. `chat/agent.py` — broadcast `explore_tool_result` `{tool, output, is_error}` right after `self._registry.execute(...)` (both success and the `except` branch), output capped like the others.
3. `tools/loop.py:1011` + `planning/loop.py:424` — raise the broadcast output cap 500 → 2000 with a `"\n… truncated"` suffix when cut (full output still lands in `tool-trace.json` artifacts; the panel links nothing, 2000 chars ≈ the hi-fi panel's scrollable view).

**Changes — Contracts (Task 5b):** `task-contracts.ts` `StreamEvent`: add `args` to `planning_tool_call`, add `explore_tool_result`. Rebuild editor-client before extension typecheck (build-order rule).

**Changes — Extension (Task 5c):** forward all three call sources as structured `appendToolEvent` (id assigned by extension, `source` field) and all three result types as `appendToolResult` paired sequentially per source. Drop the flattened-prose `appendChatThinkingEntry` for tool calls.

**Webview:** `ToolPill` renders from `ToolEventView` (Task 6) — pill spinner until result arrives; Input section from `args`; Output section from `output`.

### F2 — Thinking block (status line + streamed thoughts)

Fully wired today; **no backend change**. `chat_agent_thinking {message}`, `chat_agent_thinking_chunk {chunk}`, `tool_thinking_chunk {chunk}` (execution, `tools/loop.py:346`), `planning_thinking_chunk {chunk, iteration}` (mirrored to chat channel). Extension already maps these to `appendThinkingEntry`/`appendThinkingChunk`. Webview: ThinkingBlock consumes entries/chunk as in Task 6.

### F3 — Step progress / work bar ("Step 2 of 4 — title", hi-fi frame 2)

**Nothing on the wire today.** No step-start broadcast exists; step index/total appear nowhere (`completed_step_ids` is persisted state, not an event; the only `total_steps` on the wire is the hardcoded `1` in inline-change `diff_ready`).

**Changes — Backend (Task 5a):** in `_execute_plan`'s step loop (`orchestrator/engine.py`, right where each step's execution begins), broadcast on the task channel:
```python
self.broadcaster.broadcast(task.task_id, {
    "type": "step_started",
    "payload": {"step_id": step.id, "step_title": step.title,
                "step_index": idx + 1, "total_steps": len(plan_steps)},
})
```
(`idx`/`plan_steps` per the loop's existing variables; count only code steps it actually iterates.)

**Contracts:** add `step_started` to `StreamEvent`.
**Extension:** in `streamTaskIntoChatThread`, on `step_started` → `this.ui.updateWorkbar({stepIndex, totalSteps, stepTitle})` (new ChatPanel method → `ExtensionMessage` `updateWorkbar`); cleared on `done`/`chat_done`.
**Webview:** ThreadView work bar renders `Step {n} of {m} — {title}` with shimmer hairline; falls back to latest `thinkingStatus` text when no step info (QA/inline turns).

### F4 — Step results in the transcript

What exists (no backend change needed):
- `patch_applied {step_id, phase, touched_files}` (`tools/loop.py:770`) → extension already appends a thinking entry; keep.
- `patch_failed {step_id, error}` (`tools/loop.py:567,606,659`) → currently DROPPED by the controller; forward as a thinking entry (error-tinted) — extension-only change.
- Step accept/discard breadcrumbs: `chat_breadcrumb` events + persisted `agent/text` messages with `metadata.breadcrumb` (`engine.py:1827-1829`) → already rendered as breadcrumb lines. Note: with `CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT=true` (default) no step breadcrumb is written — **optional** backend nicety (deferred, listed in v2): a `✓ Step completed` breadcrumb on auto-accept.
- Step review gate: `pending_step_review {step_id, step_title, diff_entries[{path,additions,deletions,temp_path}]}` via /live ✓ (StepGate, Task 10).

### F5 — Plan card (live + transcript versions)

Fully wired; no backend change. Live: `/live.plan {task_id, plan_markdown}` at `AWAITING_PLAN_APPROVAL`. Transcript: `plan_card` events (both channels) + persisted messages, deduped by task+content sig. Feedback regen: breadcrumb `↻` then new version (`engine.py:490-500`).

**Structured steps caveat:** the mockup draws parsed steps (title / target file / description on a timeline). At approval time only `plan_markdown` exists — the markdown→JSON `PlanDocument` conversion runs AFTER approval (`engine.py:501-504`, "Generating execution plan…"). v1 renders styled markdown (timeline look applied to list items via prose CSS); exposing the structured plan at the approval gate would mean moving an LLM conversion call before approval — not worth it, Deferred note only.

### F6 — Gates (command / scope / validation / step)

Fully wired via /live (`live_state.py:_GATE_FIELD`); payload shapes verified (`CommandApprovalRequest`, `ScopeExtensionRequest`, `pending_validation` dict, `StepReviewPayload`). SSE `*_requested` events remain pure pokes. No backend change.

### F7 — Diff display: small_change vs large_change are DIFFERENT mechanisms

Both paths compute entries with the same helper (`_compute_diff_entries` — real workspace vs shadow, `{path, additions, deletions, temp_path}`, `temp_path` = absolute shadow-file path, so `viewDiffFile` → native VS Code diff works identically for both). Everything else differs:

| | small_change (inline) | large_change (per step) |
|---|---|---|
| Wire | `diff_ready` event (chat channel, `engine.py:1033`) | `pending_step_review` via /live (+ `step_review_requested` SSE poke, `engine.py:1798`) |
| Transcript record | **persisted `diff_card` message**, `resolve_diff_card` patches `metadata.resolved` | **none** — gate is live-slot only; afterwards only a `✓/↩` breadcrumb (file list is not persisted) |
| When user decides | real workspace untouched until decision | gate shown only when `step_review_auto_accept=false`; decision is per step |
| Accept does | `applyInlineChange` → promote inline shadow → real workspace | gate resolves, then **`_partial_promote` immediately copies the step's files into the REAL workspace** (`engine.py:1420-1426` — runs for every completed step, gated or not) |
| Reject/Discard does | nothing applied; shadow discarded | `_unmerge_step_result` reverts the shadow; execution continues |

UI mapping: inline → `DiffCard` (Task 9, persisted + resolvable); per-step → `StepGate` (Task 10, live slot, file rows + Accept/Discard). The wireframe's frame-3 "Changes ready" card with Accept all/Reject is the **inline** card; the step gate reuses its file-row body but is a different component with different actions — do not merge them.

**No diff text on the wire for either path** (`_compute_diff_entries` discards the unified diff after counting, `engine.py:1067`) → v1 renders file rows + native VS Code diff; inline diff text stays a v2 backend item (one change covers both paths — same helper).

### F8 — Final review card (READY_FOR_REVIEW) — semantics corrected

Plumbing: no backend change — derived from `/live.status == "READY_FOR_REVIEW"` + `GET /tasks/{id}/result` (`modifiedFiles`, `shadowWorkspacePath`) — Task 13; actions via existing `POST /accept` / `POST /reject`.

**BUT the semantics are NOT "accept the changes":** because `_partial_promote` already wrote every completed step's files to the real workspace during execution, by READY_FOR_REVIEW the changes are **already applied**. The backend acknowledges this — `engine.py:710` TODO: "READY_FOR_REVIEW here is largely hollow… the final PROMOTE just re-copies the same files." And `/reject` (`routes.py:487-503`) only cleans the shadow and marks ABORTED — **it does NOT revert the real workspace**.

ReviewCard copy must be honest, and the card becomes a **run summary** — the recap of what the run actually did and whether it deviated from the approved plan:
- Header: "Task complete — changes applied"
- **Summary body (v1 sources, all available without backend changes):**
  - Files changed: `result.modifiedFiles` rows with view-diff buttons (shadow still live at this point)
  - Steps: `n of m steps completed` — m from `result.plan.steps`, n/titles from the extension's observed `step_started`/breadcrumbs
  - Deviations, tracked extension-side during the run (reset per task): scope extensions approved (count + files), commands approved & remembered, delta replans fired (`revision_needed`/`planning_complete(patched)` events), steps discarded at review, validation accepted-with-errors (from its breadcrumb). Rendered as breadcrumb-style lines under a "During the run" divider; omitted when empty.
  - Ephemeral caveat: after a webview reload only files/steps survive (derived from result); deviation lines need the run to have been observed. Durable run summary = v2 backend item (persist a `run_summary` on the task — execution_state already holds most of it server-side but none of it is exposed via TaskView/TaskResult).
- Primary: **"Finish"** → `acceptTask` (re-promote no-op + SUCCEEDED + shadow cleanup)
- Ghost: **"Close without finishing"** → `rejectTask` with subtitle "keeps the applied changes; marks the task aborted"
- Do NOT label these Accept/Reject — that promises a revert that doesn't exist. True final revert (checkpoint-based workspace restore on reject, or collapsing the hollow READY_FOR_REVIEW → SUCCEEDED) is a backend redesign — Deferred, pointing at the `engine.py:710` TODO.

### F9 — Error / resume card (FAILED / ABORTED)

Card presence + actions: no backend change — derived from `/live.status`; Resume/Re-plan via existing `POST /resume`; thread `active_task_id` repoints server-side (`chat/storage.py:86`, `routes.py:965-975`) so the /live poll follows the child. "Discard" in the mockup = local dismiss (the task is already terminal).

**Failure DETAIL ("step 3 of 4 — VerifyPhaseExhausted") has NO durable wire source** — `TaskRecord` has no `failure_reason`/`last_error` field; `TaskView.diagnostics` only carries validation diagnostics. Scope:
- **v1 (ephemeral, extension-side):** the controller remembers the last `step_started` + last `patch_failed`/`done{status}` from the stream and passes them as `detail` in `renderLiveError`. Accurate while the session that watched the failure is open; after a reload the card renders the generic "Task failed — resume or re-plan" plus whatever `getTask().diagnostics` holds.
- **v2 (durable, backend):** persist a `failure_summary {step_id, step_index, error_class, message}` on the task at the point of failure and expose it through `/live` — listed in Deferred.

### F12 — Stop button (work bar)

Two very different realities depending on what is running:

- **Chat-channel turns (QA / explore / inline change):** supported TODAY. The message SSE handler cancels the agent coroutine when the client disconnects (`routes.py:1033` `agent_task.cancel()` in the stream's `finally`). Stop = the extension aborts the in-flight `sendChatMessage` iteration (`AbortController` on the fetch), then re-enables input. Extension + webview change only.
- **Executing tasks (post-approval):** **NOT safely supported — do not wire Stop to `POST /cancel` here.** Verified: `/cancel` (`routes.py:432-444`) flips status to ABORTED and deletes the shadow workspace, but nothing inside `_execute_plan`/`ToolLoop` checks for abort — the engine coroutine keeps running against a freed shadow, and its stale in-memory task object will `transition()`+`save()` over ABORTED (same stale-object class of bug as the gate invariants in CLAUDE.md). Cooperative cancellation (an abort flag/event checked between tool-loop iterations and steps, with ABORTED-aware saves) is a real backend feature — Deferred to v2.
- **v1 behavior:** show the Stop button only while a chat-channel turn is streaming; hide it during task execution (the work bar still shows progress).

### F13 — Small client-derivable details (audited, no backend needed)

- Work-bar elapsed timer (`01:42`) — client-side timer started when the turn/execution begins.
- Plan card `v2` version badge — count of prior `plan_card` messages for the same task in the transcript + 1.
- Tool panel `62 lines` badge — derived from the result output text.
- Streaming text line with caret — the active thinking chunk (F2) rendered with the blink caret.
- User-bubble inline `code` styling — client-side backtick rendering in UserMessage (display only, no markdown engine).
- Command gate subtitle — payload has `step_id`; the mockup's "· verify phase" is NOT in the payload, show `step {step_id}` only.

### F10 — History view metadata

`ChatThreadSummary {threadId, workspacePath, title, createdAt}` — `createdAt` available today (day groups + relative time, zero backend change). Message counts and Running/Review/Done status chips: **not on the wire**, deferred to v2 (needs thread-summary query change; do not fake).

### F11 — QA streaming text

Fully wired: `chat_response {chunk}` → `appendChunk`; `chat_done` finalizes. No change.

### F14 — Silent phases: the work bar must never be label-less while a task is alive

Three real dead-air phases were identified (input disabled, nothing visibly moving): **JSON plan generation** after approval (`task_status_changed {PLANNED, "Generating execution plan…"}` fires once at `engine.py:501-504`, then `create_plan` runs — thinking chunks stream via `_on_plan_thinking` only for providers that stream thinking; constrained-JSON providers emit nothing), **the first `search_semantic`** (embedding weights load — the call event broadcasts BEFORE `registry.execute`, so the Task 5 spinner pill covers it; the synchronous auto-index fallback during `CONTEXT_READY` has no event at all), and **env profile / dependency installs** (`env_install_running` fires once, then minutes of silence during pip/npm).

Fix is fully extension/webview-side — the work bar (which already has shimmer + spinner + elapsed timer) gets a **phase label with three precedence tiers**:

1. `step_started` info — `Step n of m — title` (F3)
2. Transient event overrides, cleared by their completion counterpart:
   - `env_profile_building` → "Profiling workspace environment…" (until `env_profile_built`)
   - `env_install_running` → "Syncing dependencies: {command}…" (until `env_install_done`)
   - latest `planning_tool_call` → "Planning: {tool}…" (until its result)
3. `/live.status` fallback map (durable across reloads, no backend change):
   `QUEUED` → "Queued…" · `CONTEXT_READY` → "Planning — exploring the codebase…" · `PLANNED` → "Generating execution plan…" · `EXECUTING` (no step info) → "Executing…" · `VALIDATING` → "Running validation…" · `REPAIRING` → "Repairing validation errors…" · `PROMOTING` → "Applying changes…"

The elapsed timer runs through all tiers, so even a provider that streams nothing shows a moving clock + shimmer + an accurate phase label. (A heartbeat event for genuinely hung backends is a separate concern — not in scope.)

### Mockup element audit — every data-bearing element & action in `chat-ui-hifi.html`

| # | Mockup element / action | Source | Status |
|---|---|---|---|
| 1 | History: thread titles, active row | `listChatThreads` + `activeThreadId` | ✅ wired |
| 2 | History: day groups + relative time | `ChatThreadSummary.createdAt` (pass through, F10) | ✅ extension change |
| 3 | History: "· N messages" count | nowhere on wire | ⛔ **v2 backend** (F10) |
| 4 | History: Running/Review/Done chips | nowhere on wire (per-thread status) | ⛔ **v2 backend** (F10) |
| 5 | History: search, + New Chat | client / `newChat` | ✅ |
| 6 | User bubble (incl. inline code style) | `appendMessage` user / client styling | ✅ (F13) |
| 7 | Thinking block: live label, entries, chunk | F2 events | ✅ wired today |
| 8 | Tool pills: name + args Input panel | F1 — `tool_call` ✓ / `explore_tool_call` ✓ / `planning_tool_call` ⛔ no args | 🔧 **backend Task 5a** |
| 9 | Tool pills: Output panel | F1 — `tool_result`/`planning_tool_result` ✓ (cap 500) / explore ⛔ no result event | 🔧 **backend Task 5a** |
| 10 | Tool pill spinner (live) / ✓ / ✗ states | call/result pairing | ✅ after Task 5 |
| 11 | Tool panel "62 lines" badge | derived from output | ✅ client (F13) |
| 12 | Streaming text line + caret | active thinking chunk (F2) | ✅ presentation |
| 13 | Breadcrumbs (✓/✗/↻/task queued) | `chat_breadcrumb` + `task_card` + persisted msgs | ✅ wired today |
| 14 | Work bar "Step 2 of 4 — title" | nowhere on wire | 🔧 **backend Task 5a** (`step_started`, F3) |
| 15 | Work bar elapsed `01:42` | client timer | ✅ client (F13) |
| 16 | Work bar **Stop** — chat turns | SSE disconnect cancels agent (`routes.py:1033`) | ✅ extension change (F12) |
| 17 | Work bar **Stop** — executing tasks | `/cancel` is NOT loop-aware (unsafe mid-execution) | ⛔ **v2 backend** (F12) — hide in v1 |
| 18 | Plan card: markdown, Implement/Feedback, faded preview | F5 / `/live.plan` + decision routes | ✅ wired today |
| 19 | Plan card: structured step timeline | markdown only at approval (F5 caveat) | ⚠ v1 = styled markdown |
| 20 | Plan card: "v2" version badge | count prior plan_cards client-side | ✅ client (F13) |
| 21 | Plan card: "4 steps · 2 files" subtitle | best-effort markdown heuristic | ⚠ client, display-only |
| 22 | Command gate: command, radios, remember, actions | `pending_command_request` + decision route | ✅ wired today (F6) |
| 23 | Command gate "· verify phase" subtitle | not in payload | ⚠ show step id only (F13) |
| 24 | Scope / validation / step gates | `pending_*` + decision routes | ✅ wired today (F6) |
| 25 | Diff card (inline path): header, +N −M, file count, Accept/Reject | `diff_ready` / `diff_card` + apply/discard routes — a REAL pre-apply decision | ✅ wired today (F7) |
| 25b | Step diff (large path): per-step gate, Accept/Discard | `pending_step_review` via /live; accept ⇒ `_partial_promote` writes the REAL workspace immediately | ✅ wired today (F7) — different component, no transcript file-list record (breadcrumb only) |
| 26 | Diff card: file tabs + inline diff lines + line numbers | no diff text on wire (either path) | ⛔ **v2 backend** (F7) — v1 file rows + native diff |
| 27 | Error card presence + Resume / Re-plan / Discard | `/live.status` + `/resume` (active-task repoint ✓) | ✅ Task 13 (F9) |
| 28 | Error card detail "step 3 of 4 — VerifyPhaseExhausted" | no persisted failure detail | ⚠ v1 ephemeral from stream; durable = **v2 backend** (F9) |
| 29 | Empty state + suggestion chips (pre-fill input) | static / client | ✅ |
| 30 | ReviewCard (unified-panel requirement) | `/live.status` + `getTaskResult` + accept/reject routes — but changes are ALREADY applied per step; reject does NOT revert | ⚠ Task 13 (F8) — honest "Finish / Close" copy; true final revert = **v2 backend** (`engine.py:710` TODO) |
| 31 | Copy buttons, ⌘↵ hint, send button, focus states | client | ✅ |

**Bottom line:** 3 audit rows need the v1 backend emissions already scoped in Task 5a (rows 8, 9, 14); 5 are honestly deferred to v2 backend work (rows 3, 4, 17, 26, durable-28); 3 degrade gracefully in v1 (rows 19, 21, ephemeral-28); everything else is wired today or pure client.

---

## UX interaction rules — input availability & one-shot actions

The webview must make it impossible for the user to disrupt an in-flight flow. Rules are **derived from `/live` status wherever possible** (not just in-memory turn state) so they survive webview reloads — same Class-A philosophy as the gate cards. The controller forwards the live status via a new `liveStatus` message each poll (deduped in the existing signature).

### Rule 1 — Input box availability (precedence top → bottom; first match wins)

| State (local ∥ live.status) | Input | Placeholder | Extra |
|---|---|---|---|
| Local chat turn streaming | **disabled** | "Agent is working…" | Stop button visible (F12) |
| `AWAITING_PLAN_APPROVAL` | **disabled** | "Review the plan — Implement or Give feedback" | only the live plan card is interactive |
| `AWAITING_{COMMAND,SCOPE,STEP,VALIDATION}_DECISION` | **disabled** | "Waiting for your decision on the card above" | only the gate card is interactive |
| `QUEUED / CONTEXT_READY / PLANNED / EXECUTING / VALIDATING / REPAIRING / PROMOTING` | **disabled** | "Task is running…" (+ step n/m when known) | work bar shows progress; no Stop in v1 |
| `READY_FOR_REVIEW` | **enabled** | normal | ReviewCard pending is non-blocking — user may ask questions; Finish/Close stays in the live slot |
| `FAILED / ABORTED / SUCCEEDED` / no active task | **enabled** | normal | ErrorCard actions remain available |

The existing `setInputEnabled` round-trip remains the signal for local turn streaming; the live-status rows make the disabled state durable across reloads (today a reload mid-execution silently re-enables the input — a real current-flow bug this fixes).

### Rule 2 — Every decision action is one-shot

On first click, the **entire action row** of the card swaps to an optimistic resolved/busy label (`✓ Implementing…`, `✓ Allowed once`, `✓ Finishing…`) — no button in that row can fire twice. Component-local state covers the gap until the /live poll clears or remounts the card; a genuinely NEW decision of the same kind remounts via the LiveSlot content key (Task 11). Backend 409s from racing clicks stay swallowed as benign (`isBenignConflict`).

Specifics:
- Plan card: `Implement` and `Give feedback` are mutually exclusive — opening feedback hides Implement; `Send` resolves the row (`↻ Regenerating…`); `Cancel` restores both.
- Gate cards: all 2–3 buttons disable together on any click.
- Diff card (inline): `Accept all`/`Reject` resolve together; view-diff buttons stay active (read-only).
- ReviewCard / ErrorCard: same row-level resolution; `Resume`/`Re-plan` also clear the local dismissed flag.
- Send button + Enter are no-ops while input is disabled or text is empty (trim).

### Rule 3 — Navigation cannot orphan a streaming turn

While a **local SSE loop is appending to the panel** (chat turn OR `streamTaskIntoChatThread`), thread navigation is locked: `‹` back, history rows, and `+ New Chat` are disabled with a tooltip ("A turn is in progress"). Without this, the in-flight loop keeps appending into whichever thread is displayed — cross-thread transcript bleed (an existing chat.js bug this fixes). The /live-status disabled states (task executing, gates) do NOT lock navigation — other threads have their own live state and the poll re-derives everything on switch. v2 may relax Rule 3 by tagging streamed messages with their thread id.

### Rule 4 — Read-only affordances are always safe

Copy buttons, expand/collapse (cards, pills, thinking), diff viewing, and search work in every state — they never post decisions. View-diff on a stale card whose shadow was cleaned up shows the existing "Diff is unavailable" warning rather than erroring.

### Step-review default is a UX decision (not an env knob)

With `CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT=true` (current default) the only conscious approval on the large path is plan approval — every step lands silently and Finish is a formality (F8). Decision: **review-by-default**.
- **v1:** `start-backend.sh` exports `CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT=false` (one line, parity with how scope `ask`+`any` is already forced there). Step gates surface in the live slot per F6.
- **v2 (deferred):** a per-task "Review each step" toggle in the composer — requires plumbing the flag through `POST /threads/{id}/message` into `create_task_from_chat` (today the message body is just `{message}`; the extension cannot inject it).

---

## File Map

**Create:**
- `apps/vscode-extension/webview-ui/package.json`, `vite.config.ts`, `vitest.config.ts`, `tsconfig.json`, `index.html`
- `apps/vscode-extension/webview-ui/src/index.css` — hi-fi tokens + keyframes
- `apps/vscode-extension/webview-ui/src/vscodeApi.ts`
- `apps/vscode-extension/webview-ui/src/types.ts`
- `apps/vscode-extension/webview-ui/src/main.tsx`, `src/App.tsx`
- `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts`
- `apps/vscode-extension/webview-ui/src/components/Icon.tsx` — SVG sprite (from hi-fi mockup)
- `apps/vscode-extension/webview-ui/src/components/HistoryView.tsx`
- `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx`
- `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`
- `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`
- `apps/vscode-extension/webview-ui/src/components/EmptyState.tsx`
- `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/UserMessage.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/QAMessage.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/PlanCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/gates/CommandGate.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/gates/ScopeGate.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/gates/ValidationGate.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/gates/StepGate.tsx`
- `apps/vscode-extension/webview-ui/src/components/messages/ReviewCard.tsx` — **new** (final accept/reject)
- `apps/vscode-extension/webview-ui/src/components/messages/ErrorCard.tsx`
- `apps/vscode-extension/webview-ui/src/components/shared/ThinkingBlock.tsx`
- `apps/vscode-extension/webview-ui/src/components/shared/ToolPill.tsx`
- `apps/vscode-extension/webview-ui/src/test/` — setup + component/hook tests

**Modify (backend — additive SSE emissions only, see Feature wire contracts):**
- `services/agentd-py/agentd/planning/loop.py` — add `args` to `planning_tool_call`; raise result cap to 2000
- `services/agentd-py/agentd/chat/agent.py` — new `explore_tool_result` broadcast after each explore tool execution
- `services/agentd-py/agentd/tools/loop.py` — raise `tool_result` cap to 2000 (+ truncation suffix)
- `services/agentd-py/agentd/orchestrator/engine.py` — new `step_started` broadcast at the top of each step in `_execute_plan`
- `services/agentd-py/tests/` — extend the existing broadcast-assertion tests for the four emissions

**Modify (contracts):**
- `apps/editor-client/src/contracts/task-contracts.ts` — `StreamEvent`: `args` on `planning_tool_call`, new `explore_tool_result`, new `step_started` (rebuild editor-client before extension typecheck)

**Modify (extension):**
- `apps/vscode-extension/src/chat-panel.ts` — `buildHtml()` reads `webview-ui/dist`; `reattach()` resets `webview.options`; new methods `appendToolEvent`, `appendToolResult`, `updateWorkbar`, `renderLiveReview`, `clearLiveReview`, `renderLiveError`, `clearLiveError`; new inbound branches `acceptTask`, `rejectTask`, `resumeTask`
- `apps/vscode-extension/src/controller.ts` — structured tool forwarding; live review/error derivation in `pollThreadLiveState`; `acceptTaskPatch(taskId)` / `rejectTaskPatch(taskId, reason)` / `resumeTaskById(taskId, stage)`; remove `updatePanel`/`showStepReview`/`buildViewModel`/`patchEvents`
- `apps/vscode-extension/src/extension.ts` — remove `ReviewPanel`; wire new handlers
- `apps/vscode-extension/src/types.ts` — drop `ReviewPanelViewModel` if now unused
- `apps/vscode-extension/test/controller.test.ts` — stub `ControllerUI` updates (lines ~186, ~201)
- `apps/vscode-extension/package.json` — `webview:build` + `prebuild` scripts; drop `marked`
- root `package.json` — extend `build`/`test`/`typecheck` to invoke webview-ui via `--prefix`

**Delete:**
- `apps/vscode-extension/media/chat.js`
- `apps/vscode-extension/media/marked.umd.js`
- `apps/vscode-extension/src/review-panel.ts`

---

## Task 1: Commit current uncommitted changes (prerequisite)

Unchanged from Rev 1. Group the dirty files in `git status` by concern (`fix(live-state)`, `feat(chat)`, docs, scripts…), commit each group with `git add <specific files>` (never `-A`), and finish with a clean tree (untracked scratch files may remain).

---

## Task 2: Scaffold webview-ui package

Unchanged from Rev 1 (package.json / vite.config.ts / tsconfig.json / index.html / `npm install`). Keep `base: "./"`, fixed asset filenames, `dist/` output. Note: webview-ui is deliberately NOT added to root `workspaces` (`apps/*` doesn't match nested paths and we don't want hoisting under the extension); root scripts call it via `--prefix` (Task 15).

Skip the "verify build" step until `main.tsx` exists (Task 12).

---

## Task 3: CSS tokens (hi-fi), Icon sprite, vscodeApi, types

**Files:** `src/index.css`, `src/components/Icon.tsx`, `src/vscodeApi.ts`, `src/types.ts`

- [ ] **Step 1: Create src/index.css — hi-fi token system**

Tokens come from `chat-ui-hifi.html`, not the spec's older table:

```css
@import "tailwindcss";

@theme {
  /* surfaces — layered elevation, violet-cool */
  --color-panel: #131316;
  --color-surface: #19191d;
  --color-surface-2: #1f1f24;
  --color-surface-3: #26262c;
  /* borders */
  --color-border: #26262c;
  --color-border-strong: #32323a;
  /* text ramp */
  --color-text: #ececf1;
  --color-text-2: #a0a0aa;
  --color-text-3: #62626e;
  --color-text-4: #41414b;
  /* accent — violet ramp */
  --color-accent: #a78bfa;
  --color-accent-deep: #8b5cf6;
  --color-accent-hot: #7c3aed;
  --color-accent-ink: #c4b5fd;
  /* semantic */
  --color-green: #4ade80;
  --color-red: #f87171;
  --color-amber: #fbbf24;
  --color-code: #7dd3fc;
}
```

Plus plain CSS custom properties for the alpha variants (Tailwind arbitrary values reference them):

```css
:root {
  --accent-bg: rgba(139,92,246,.10);
  --accent-bg-2: rgba(139,92,246,.17);
  --accent-brd: rgba(139,92,246,.32);
  --accent-glow: rgba(139,92,246,.22);
  --green-bg: rgba(74,222,128,.09);
  --green-brd: rgba(74,222,128,.25);
  --red-bg: rgba(248,113,113,.07);
  --red-brd: rgba(248,113,113,.26);
  --amber-bg: rgba(251,191,36,.09);
  --hairline: rgba(255,255,255,.045);
}
```

Body/base rules: `background: var(--color-panel)`, `font-family: var(--vscode-font-family, …)`, `font-size: var(--vscode-font-size, 13px)`, `#root { height: 100vh; display: flex; flex-direction: column; }`, code/mono on `var(--vscode-editor-font-family)`, thin scrollbars per the mockup.

**Theme decision (recorded):** v1 ships the fixed dark palette — the design is dark-by-identity; fonts/sizes still follow VS Code vars. Light-theme users get a dark panel (same as several popular dark-first webviews). Mapping surfaces to `--vscode-*` with the violet ramp kept as fixed brand is a v2 item (Deferred).

Keyframes (copy from `chat-ui-hifi.html`): `spin`, `pulse`, `blink`, `shimmer`, `rise`, `breathe`. Utility classes `.anim-rise`, `.shimmer-bg` (the streaming-pill gradient), `.workbar-line` (the animated top hairline).

- [ ] **Step 2: Create src/components/Icon.tsx**

Port the 19-symbol SVG sprite from `chat-ui-hifi.html` (`i-spark`, `i-search`, `i-plus`, `i-clock`, `i-chev-r/l/d`, `i-check`, `i-x`, `i-copy`, `i-file`, `i-term`, `i-list`, `i-diff`, `i-warn`, `i-send`, `i-stop`, `i-retry`, `i-bolt`, `i-bug`) as a single `<Icon name size />` component rendering inline `<svg>` paths (no `<use>` — keeps it tree-shakeable and CSP-trivial). **No emoji anywhere in components.**

- [ ] **Step 3: Create src/vscodeApi.ts** — unchanged from Rev 1 (`acquireVsCodeApi` guard + test stub).

- [ ] **Step 4: Create src/types.ts — corrected to the real wire shapes**

```typescript
// ── Wire shape of a persisted chat message (mirrors editor-client ChatMessageSchema).
// EVERY message has role + type; cards are discriminated by `type`, not by `role`.
export interface ChatMsg {
  role: "user" | "agent";
  content: string;
  type: "text" | "plan_card" | "diff_card" | "diff_summary" | "task_card"
      | "scope_card" | "validation_card" | "command_card";
  taskId?: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
}

// Diff entries arrive snake_case (SSE + /live payloads are not case-mapped).
export interface DiffEntry {
  path: string;
  additions: number;
  deletions: number;
  temp_path?: string;
}

export interface Diagnostic { level: string; message: string; source?: string }

export interface ThreadSummary {
  threadId: string;
  title: string;
  createdAt: string;   // ChatThreadSummary already carries it; controller now passes it through
}

// ── Structured tool events (NEW protocol, Task 5) ────────────────────────────
export interface ToolEventView {
  id: number;                 // monotonically increasing per turn (extension-assigned)
  tool: string;
  args: Record<string, unknown>;
  thought?: string;
  source: "explore" | "execution" | "planning";
  output?: string;            // filled by the matching toolResult
  isError?: boolean;
  done: boolean;
}

// ── Live slot views ──────────────────────────────────────────────────────────
export interface LiveGateView {
  kind: "command" | "scope" | "validation" | "step";
  taskId: string;
  payload: Record<string, unknown>;  // pending_* payload, snake_case (see gate components)
}

export interface LivePlanView { taskId: string; planMarkdown: string }

export interface LiveReviewView {
  taskId: string;
  modifiedFiles: string[];
  shadowWorkspacePath: string | null;
  // run summary (F8): derived from result.plan + extension-observed events
  stepsCompleted: number | null;
  stepsTotal: number | null;
  deviations: string[];
}

export interface LiveErrorView {
  taskId: string;
  status: "FAILED" | "ABORTED";
  detail?: string;
}

export interface WorkbarInfo {
  stepIndex?: number;       // tier 1 (F3)
  totalSteps?: number;
  stepTitle?: string;
  phaseLabel?: string;      // tier 2 — transient event override (F14)
}

// ── Extension → Webview ──────────────────────────────────────────────────────
export type ExtensionMessage =
  | { type: "appendMessage"; message: ChatMsg }
  | { type: "appendChunk"; chunk: string }
  | { type: "appendThinkingEntry"; text: string }
  | { type: "appendThinkingChunk"; chunk: string }
  | { type: "appendToolEvent"; event: Omit<ToolEventView, "output" | "isError" | "done"> }
  | { type: "appendToolResult"; id: number; output: string; isError: boolean }
  | { type: "updateWorkbar"; info: WorkbarInfo | null }
  | { type: "finalizeAgentMessage" }
  | { type: "showThinking"; message: string }
  | { type: "updateThinking"; message: string }
  | { type: "hideThinking" }
  | { type: "setInputEnabled"; enabled: boolean }
  | { type: "renderThreadList"; threads: ThreadSummary[]; activeThreadId: string }
  | { type: "clearThread" }
  | { type: "renderLiveGate"; gate: LiveGateView }
  | { type: "clearLiveGate" }
  | { type: "renderLivePlan"; plan: LivePlanView }
  | { type: "clearLivePlan" }
  | { type: "renderLiveReview"; review: LiveReviewView }
  | { type: "clearLiveReview" }
  | { type: "renderLiveError"; error: LiveErrorView }
  | { type: "clearLiveError" }
  | { type: "liveStatus"; status: string | null }   // /live.status each poll — drives input availability (UX Rule 1)
  | { type: "resolveInlineChangeCard"; taskId: string; resolution: "applied" | "discarded" }
  | { type: "thread_title_updated"; payload: { thread_id: string; title: string } };

// ── Webview → Extension ──────────────────────────────────────────────────────
export type WebviewMessage =
  | { type: "webviewReady" }
  | { type: "sendMessage"; text: string }
  | { type: "implementPlan"; taskId: string }
  | { type: "planFeedback"; taskId: string; feedback: string }
  | { type: "newChat" }
  | { type: "switchThread"; threadId: string }
  | { type: "applyInlineChange"; taskId: string }
  | { type: "discardInlineChange"; taskId: string }
  | { type: "viewDiffFile"; path: string; shadowPath: string }
  | { type: "scopeDecision"; taskId: string; files: string[]; decision: "approve" | "reject"; remember: boolean }
  | { type: "validationDecision"; taskId: string; decision: "accept" | "reject" }
  | { type: "commandDecision"; taskId: string; approve: boolean; remember?: boolean; scope?: string; ruleValue?: string }
  | { type: "stepDecision"; taskId: string; decision: "accept" | "discard" }
  | { type: "acceptTask"; taskId: string }                                   // NEW
  | { type: "rejectTask"; taskId: string; reason: string }                   // NEW
  | { type: "resumeTask"; taskId: string; stage: "plan" | "execute" }        // NEW
  | { type: "stopTurn" };                                                    // NEW — abort the in-flight chat turn (F12)

// ── App state ─────────────────────────────────────────────────────────────────
export interface StreamingBubble {
  text: string;
  thinkingEntries: string[];
  activeThinkingChunk: string;
  toolEvents: ToolEventView[];
}

export interface AppState {
  view: "history" | "thread";
  threads: ThreadSummary[];
  activeThreadId: string;
  messages: ChatMsg[];
  streaming: StreamingBubble | null;
  thinkingStatus: string | null;
  inputEnabled: boolean;
  liveGate: LiveGateView | null;
  livePlan: LivePlanView | null;
  liveReview: LiveReviewView | null;
  liveError: LiveErrorView | null;
  workbar: WorkbarInfo | null;
  liveStatus: string | null;   // input availability per UX Rule 1
}
```

- [ ] **Step 5: Commit** — `feat(webview-ui): hi-fi tokens, icon sprite, vscodeApi, corrected protocol types`

---

## Task 4: App state reducer + bridge hook

**Files:** `src/hooks/useAppState.ts`, `src/test/useAppState.test.ts`, `src/test/setup.ts`, `vitest.config.ts`

Keep Rev 1's overall reducer structure with these corrections:

- [ ] **Step 1: planSig fix**

```typescript
function planSig(taskId: string, content: string): string {
  const s = `${taskId}::${content}`;
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}
```

- [ ] **Step 2: appendThinkingEntry — seal, don't overwrite**

```typescript
case "appendThinkingEntry": {
  const prev = ensureStreaming(state);
  const entries = prev.activeThinkingChunk
    ? [...prev.thinkingEntries, prev.activeThinkingChunk]   // seal the streamed chunk as its own entry
    : [...prev.thinkingEntries];
  return { ...state, streaming: { ...prev, thinkingEntries: [...entries, msg.text], activeThinkingChunk: "" } };
}
```

- [ ] **Step 3: structured tool events**

```typescript
case "appendToolEvent": {
  const prev = ensureStreaming(state);
  const ev: ToolEventView = { ...msg.event, done: false };
  return { ...state, streaming: { ...prev, toolEvents: [...prev.toolEvents, ev] } };
}
case "appendToolResult": {
  const prev = ensureStreaming(state);
  return {
    ...state,
    streaming: {
      ...prev,
      toolEvents: prev.toolEvents.map((t) =>
        t.id === msg.id ? { ...t, output: msg.output, isError: msg.isError, done: true } : t,
      ),
    },
  };
}
```

`sealStreaming` stores `toolEvents` into the finalized agent message's `metadata.tool_events` (so they survive in the transcript for the rest of the session; persisted history reloads won't have them — acceptable, matches today where thinking_log is the persisted record).

- [ ] **Step 4: plan_card dedup** — keep Rev 1's `_sig` approach (it mirrors chat.js), with the fixed `planSig`. **Dispatch by `m.type === "plan_card"`** (not role).

- [ ] **Step 5: live slot actions** — add `renderLiveReview`/`clearLiveReview`/`renderLiveError`/`clearLiveError` cases mirroring gate/plan.

- [ ] **Step 6: tests** — keep Rev 1's five tests; add: thinking-chunk-then-entry preserves both entries; appendToolEvent/appendToolResult pairing; plan_card arriving with `role:"agent"` AND `type:"plan_card"` (real wire shape) dedups and does NOT count as a text message.

- [ ] **Step 7: Commit**

---

## Task 5: Tool & progress event plumbing (backend → contracts → extension)

This implements wire contracts **F1, F3, F4** end-to-end. Three sub-stages, committed separately, each verifiable on its own.

### Task 5a — Backend emissions (additive, no API/state-machine changes)

**Files:** `planning/loop.py`, `chat/agent.py`, `tools/loop.py`, `orchestrator/engine.py`, tests

- [ ] **Step 1: `planning/loop.py:410`** — add `"args": args` to the `planning_tool_call` payload (args are already in scope; they're recorded into the tool trace two lines down).

- [ ] **Step 2: `chat/agent.py` explore loop (~line 208)** — after `self._registry.execute(tool_name, args)` (and in the `except` branch), broadcast:

```python
self._broadcaster.broadcast(channel_id, {
    "type": "explore_tool_result",
    "payload": {"tool": tool_name, "output": _cap(tool_output.output), "is_error": tool_output.is_error},
})
```

(`_cap` = 2000 chars + `"\n… truncated"` suffix; error branch sends `str(exc)`, `is_error=True`.)

- [ ] **Step 3: result caps** — `tools/loop.py:1011` and `planning/loop.py:424`: replace `output[:500]` with the same `_cap(...)` helper (2000 + suffix). Full output still lands in `tool-trace.json` artifacts — the SSE cap only bounds the UI panel.

- [ ] **Step 4: `orchestrator/engine.py` `_execute_plan` step loop** — at the top of each step's execution, broadcast `step_started` on the task channel:

```python
self.broadcaster.broadcast(task.task_id, {
    "type": "step_started",
    "payload": {"step_id": step.id, "step_title": step.title,
                "step_index": idx + 1, "total_steps": len(steps)},
})
```

Use the loop's actual index/collection variables; count the steps the loop iterates (after `completed_step_ids` filtering, index within the full plan for honest "N of M").

- [ ] **Step 5: tests** — extend the existing scripted-engine broadcast assertions: `planning_tool_call` carries `args`; explore turn emits paired `explore_tool_call`/`explore_tool_result`; `step_started` fires once per executed step with correct index/total. Run `pytest tests/test_planning_agent.py tests/test_tools_registry.py` + the chat agent test file; then the full suite.

- [ ] **Step 6: Commit** — `feat(events): args on planning_tool_call, explore_tool_result, step_started, 2k result caps`

### Task 5b — Contracts

- [ ] `task-contracts.ts` `StreamEvent`: add `args?: Record<string, unknown>` to `planning_tool_call`; add `| { type: "explore_tool_result"; payload: { tool: string; output: string; is_error: boolean } }`; add `| { type: "step_started"; payload: { step_id: string; step_title: string; step_index: number; total_steps: number } }`.
- [ ] `npm run -w @ai-editor/editor-client build && npm run -w @ai-editor/editor-client test` (extension types off compiled dist — build BEFORE extension typecheck).
- [ ] Commit — `feat(contracts): explore_tool_result, step_started, planning_tool_call args`

### Task 5c — Extension forwarding

**Files:** `src/chat-panel.ts`, `src/controller.ts`, `src/extension.ts`, `test/controller.test.ts`

- [ ] **Step 1: chat-panel.ts — new methods**

```typescript
appendToolEvent(event: { id: number; tool: string; args: Record<string, unknown>; thought?: string; source: string }): void {
  this.panel?.webview.postMessage({ type: "appendToolEvent", event });
}
appendToolResult(id: number, output: string, isError: boolean): void {
  this.panel?.webview.postMessage({ type: "appendToolResult", id, output, isError });
}
updateWorkbar(info: { stepIndex: number; totalSteps: number; stepTitle: string } | null): void {
  this.panel?.webview.postMessage({ type: "updateWorkbar", info });
}
```

Add matching members to `ControllerUI` and the `ui` object in `extension.ts`.

- [ ] **Step 2: controller.ts — forward structured events**

Per-turn counter `private toolEventSeq = 0` and per-source open-event ids `private openToolEvent: Partial<Record<"explore" | "planning" | "execution", number>> = {}`. In `sendChatMessage` and `streamTaskIntoChatThread`:

- `explore_tool_call` (source `explore`) / `tool_call` (source `execution`) / `planning_tool_call` (source `planning`) → `appendToolEvent({ id: ++this.toolEventSeq, tool, args: args ?? {}, thought, source })`, record `openToolEvent[source] = id`. **Replaces** the flattened `appendChatThinkingEntry` strings for tool calls.
- `explore_tool_result` / `tool_result` / `planning_tool_result` → `appendToolResult(openToolEvent[source], output, is_error)`, then clear that slot. Pairing is per-source sequential — each loop has one tool in flight (verified: calls/results interleave strictly in all three loops).
- `step_started` → `updateWorkbar({ stepIndex, totalSteps, stepTitle })`; clear on `done` / `chat_done` / terminal statuses. Also remember it in `private lastStepStarted` (error-card detail, F9 v1).
- **Phase labels (F14):** `env_profile_building`/`env_install_running` and the latest unresolved `planning_tool_call` also update the work bar via `updateWorkbar({ phaseLabel })` (tier-2 overrides), cleared by their completion events; the webview applies the tier-3 `/live.status` fallback map itself from `liveStatus` — so silent phases (JSON plan generation, embedding load, dep installs, auto-index) always show an accurate label + running timer.
- `patch_failed` → `appendChatThinkingEntry("✗ patch failed: " + error)` (currently dropped) and remember in `private lastPatchError` — `pollThreadLiveState` passes `detail: "${lastStepStarted.title} (step ${i} of ${n}) — ${lastPatchError}"` to `renderLiveError` when available (cleared when a new task starts).
- Non-tool entries (`patch applied`, env_profile lines, gate waits, `planning_complete`) stay as `appendChatThinkingEntry`.
- **Stop (F12, chat turns only):** add optional `signal?: AbortSignal` to `HttpBackendClient.sendChatMessage` (editor-client, non-breaking — pass to `fetch`); controller creates an `AbortController` per turn; `stopTurn` from the webview aborts it (server cancels the agent coroutine on disconnect, `routes.py:1033`), then `finalizeAgentMessage` + re-enable input. The webview shows Stop only while a chat turn is streaming — never during task execution (see F12).

- [ ] **Step 3: typecheck + extension tests; update the `ControllerUI` stub** in `test/controller.test.ts`.

- [ ] **Step 4: Commit** — `feat(extension): structured tool events + work bar forwarding`

---

## Task 6: Shared components — ThinkingBlock, ToolPill

**Visual reference:** frame 2 of `chat-ui-hifi.html` (`.think`, `.pill`, `.toolpanel`).

- [ ] **Step 1: ThinkingBlock.tsx**

Rev 1 structure with fixes:
- Collapse when streaming ends: `useEffect(() => { if (!streaming) setOpen(false); }, [streaming])`.
- Streaming header: pulse dot (`animate-pulse` violet, glow) + label in `accent-ink` on `--accent-bg` with `--accent-brd` border — `.think.live` in the mockup. Idle: surface bg, `text-3`, chevron rotates when open (`.tw`).
- Expanded detail: left border rail (`border-l-2 border-border-strong`), numbered entries, max-h-40, `.anim-rise`.

- [ ] **Step 2: ToolPill.tsx — driven by ToolEventView, no string parsing**

```typescript
interface Props { event: ToolEventView }
```

- Pill (collapsed): rounded-full, mono 10.5px, tool icon by name (`search_code`→i-search, `read_file`→i-file, `run_command`→i-term, `query_graph`→i-diff, default i-bolt), label = `event.tool`, then: spinner while `!event.done` (shimmer background, `--accent-brd` border — `.pill.live`), green check when done, red ✗ when `isError`.
- Click toggles the inline panel (`.toolpanel`): header (icon + tool name in accent-ink, status badge, "collapse" hint), **Input** section (mono key:value rows from `event.args`), **Output** section (mono, `max-h-24 overflow-y-auto`, render `event.output ?? "…running"`). `.anim-rise` on open.
- Multiple pills can be open at once (each owns its `open` state) — matches the spec.

- [ ] **Step 3: Commit**

---

## Task 7: UserMessage, QAMessage, AgentRow

**Visual reference:** frame 2 (`.ubub`, `.turn`, `.avatar`, `.crumb`, `.stream-line`).

- [ ] **Step 1: UserMessage** — right-aligned bubble, `max-w-[86%]`, gradient surface (`linear-gradient(180deg, var(--color-surface-2), var(--color-surface))`), border-strong, radius `12px 12px 4px 12px`, inset hairline highlight.

- [ ] **Step 2: QAMessage** — gradient violet avatar tile (20px, `i-spark`, glow) replaces the "AI" text chip; `react-markdown` body with code styled `--color-code` on surface-2; hover copy button (`i-copy` + "Copied ✓" flash), absolute top-right, `opacity-0 group-hover:opacity-100`.

- [ ] **Step 3: AgentRow** — avatar + column of: ThinkingBlock (thought entries), ToolPill row (`flex flex-wrap gap-1.5`, from `toolEvents` or `metadata.tool_events`), breadcrumb lines (green `i-check` + `text-2`, matching `.crumb` — breadcrumbs are `metadata.breadcrumb === true` text messages), streaming caret (1.5px violet bar, `animation: blink`). Copy button as in QAMessage. Props now take `toolEvents: ToolEventView[]` instead of parsing strings.

- [ ] **Step 4: Commit**

---

## Task 8: PlanCard

**Visual reference:** frame 3 (`.plan-card`, `.steps`, `.step-badge`, timeline connector) — note v1 renders the plan as markdown, not parsed steps; the timeline-step look applies to the markdown's list items via prose styles. Parsing plan markdown into step objects is OUT of scope (fragile).

Keep Rev 1's component logic with these changes:
- Header: `i-list` icon (accent), "Plan" semibold, optional step-count subtitle derived by counting `^#{2,3} |\n- ` matches (best-effort, display-only), chevron rotation on expand, accent border when open.
- Collapsed: faded preview `max-h-[102px]` + gradient overlay that fades out on expand (`max-height` transition, `.steps-fade` pattern).
- Actions: `Implement` = gradient violet primary (`from accent-deep to accent-hot`, glow shadow, `i-bolt`); `Give feedback` = ghost. Feedback mode reveals inline input row (`.fb-row` pattern, `.anim-rise`).
- `readOnly` renders no action bar (transcript plan_card versions).
- **The live-slot instance must reset its internal state when content changes** — handled by keying in LiveSlot (Task 11), not inside PlanCard.
- Keep Rev 1's tests; they remain valid (update selectors for icon-based header: query by text "Plan").

---

## Task 9: DiffCard (inline changes) — no fabricated inline diff

**Visual reference:** frame 3 (`.diff-card` header with `.dstats`), but body = file rows, not diff panes.

- [ ] **Step 1: DiffCard.tsx**

Props: `{ taskId, diffEntries: DiffEntry[], resolved?: "applied" | "discarded" | null, thinkingLog?: string[] }`.

- Header: `i-diff` icon, "Changes ready", aggregate `+N −M` stats (mono, green/red), file-count badge (violet pill), chevron.
- Body (expanded): one row per entry — file-type dot (ts=blue/py=amber by extension), mono basename, dimmed dir path, `+a −d` stats, and an `i-file` "view" button posting `viewDiffFile { path, shadowPath: entry.temp_path ?? "" }` (opens the native VS Code diff — same as today).
- Actions: `Accept all` (primary, `i-check`) → `applyInlineChange`; `Reject` (ghost) → `discardInlineChange`. Resolved state replaces actions with `✓ Applied` / `✗ Discarded` breadcrumb and tints the card border green/red.
- `resolved` prop comes from `metadata.resolved` (patched by `resolveInlineChangeCard`); also honor local optimistic state.

**v2 (deferred, requires backend):** add `unified_diff` per entry to `diff_ready` + `pending_step_review` payloads, then render the hi-fi tabbed diff panes with line numbers. Do NOT build the diff renderer against a field that doesn't exist.

- [ ] **Step 2: keep Rev 1's DiffCard tests**, adjusted: tabs test → file-row test.

---

## Task 10: Gate components (4 files) + ErrorCard + ReviewCard

**Visual reference:** frame 3 (`.gate-card`, `.cmdblock`, `.radios`) and frame 4 (`.err-card`).

- [ ] **Step 1: gates/CommandGate.tsx**

Payload (backend `CommandApprovalRequest`, snake_case): `{ decision_id, command, args, cwd, step_id }`.

Port chat.js's **full** command card semantics (chat.js lines 186–276), styled per the hi-fi mockup:
- mono command block with violet `$` prompt (`.cmdblock`), horizontal scroll
- custom radio group (`.radio`/`.rdot` styling): exact / prefix (with token-count number input) / binary
- **`shlexJoin` ported verbatim from chat.js** — exact and prefix rule values must be shlex-joined for backend rule matching
- live "auto-approves: …" preview line
- Actions: `Allow once` (primary) / `Allow & remember` (ghost) / `Reject` (danger ghost) → `commandDecision` payloads identical to chat.js (`approve`, `remember`, `scope`, `ruleValue`)
- subtitle in header: `step {step_id}`

- [ ] **Step 2: gates/ScopeGate.tsx** — payload `{ decision_id, files, reason, step_id }`; reason text + mono file list; Approve / Approve & remember / Reject → `scopeDecision`.

- [ ] **Step 3: gates/ValidationGate.tsx** — payload `{ task_id, summary, diagnostics: [{source, message, level}] }`; summary line + scrollable mono diagnostic list (level-colored); Accept / Reject → `validationDecision`.

- [ ] **Step 4: gates/StepGate.tsx** — payload `{ step_id, step_title, diff_entries }`; reuse DiffCard's file-row body (read-only, with view-diff buttons via `temp_path`); Accept / Discard → `stepDecision`.

All four: plain function components, hooks at top level only, local `resolved` state renders the `✓/✗` label in place of buttons (the /live poll clears the card moments later; the label covers the gap).

- [ ] **Step 5: ErrorCard.tsx** — red-tinted card (`--red-bg` fill, `--red-brd` border), `i-warn` header "Execution failed" + status subtitle, collapsible detail (if `detail` provided), actions:
  - `Resume` (primary, `i-retry`) → `resumeTask { taskId, stage: "execute" }`
  - `Re-plan` (ghost) → `resumeTask { taskId, stage: "plan" }`
  - `Dismiss` (danger ghost) → local-only hide (posts nothing; the card re-derives from /live until a new task starts, so also suppress re-render for the same taskId after dismiss via a `dismissedErrorTaskId` in App state)

- [ ] **Step 6: ReviewCard.tsx — the ReviewPanel replacement (critical; semantics per F8)**

Props = `LiveReviewView` (extended per F8: `modifiedFiles`, `shadowWorkspacePath`, `stepsCompleted`, `stepsTotal`, `deviations: string[]`). Card: `i-check` header **"Task complete — changes applied"** (NOT "ready for review" — `_partial_promote` already wrote each step's files to the real workspace, see F8), then the run summary:
- mono file rows (each posting `viewDiffFile` with `shadowPath = join(shadowWorkspacePath, path)` — the extension computes the join; webview just echoes fields, see Task 13)
- `n of m steps completed` line
- "During the run" divider + deviation breadcrumb lines (scope extensions, remembered commands, delta replans, discarded steps, validation-accepted) when non-empty — extension-tracked per F8, ephemeral after reload

Actions:
- `Finish` (primary) → `acceptTask { taskId }` (final promote is a re-copy no-op → SUCCEEDED + shadow cleanup)
- `Close without finishing` (ghost) → reveals inline reason input → `rejectTask { taskId, reason }` — subtitle: "keeps the applied changes" (reject does NOT revert the workspace, `routes.py:497-499`)

After action, show optimistic `✓ Finishing…` — the /live poll clears the card when status leaves READY_FOR_REVIEW. One-shot per UX Rule 2.

- [ ] **Step 7: keep/port Rev 1's GateCard tests** split across the four files; add ReviewCard + ErrorCard tests (postMessage payload assertions).

---

## Task 11: LiveSlot, HistoryView, InputArea, EmptyState

- [ ] **Step 1: LiveSlot.tsx — content-keyed, all five live card kinds**

```typescript
export function LiveSlot({ liveGate, livePlan, liveReview, liveError }: Props) {
  if (!liveGate && !livePlan && !liveReview && !liveError) return null;
  return (
    <div className="flex flex-col gap-2 px-3 py-2 flex-shrink-0">
      {liveGate && (
        <GateDispatch key={`${liveGate.taskId}:${liveGate.kind}:${sig(liveGate.payload)}`} {...liveGate} />
      )}
      {livePlan && (
        <PlanCard key={`${livePlan.taskId}:${sig(livePlan.planMarkdown)}`} content={livePlan.planMarkdown} taskId={livePlan.taskId} />
      )}
      {liveReview && <ReviewCard key={liveReview.taskId} {...liveReview} />}
      {liveError && <ErrorCard key={`${liveError.taskId}:${liveError.status}`} {...liveError} />}
    </div>
  );
}
```

**The `key` is load-bearing**: a second command gate (or a feedback-regenerated plan) must remount and discard the previous card's local `resolved` state. `sig()` = the djb2 helper from useAppState. `GateDispatch` maps `kind` → the four gate components.

- [ ] **Step 2: HistoryView.tsx** — Rev 1 structure restyled per frame 1 (`.hrow`, `.hgroup`, `.search`):
  - Day-group labels (Today / Yesterday / This week / older) computed from `createdAt`; relative timestamp line per row (`tabular-nums`).
  - Active row: surface bg + inset 2px violet left bar (`shadow-[inset_2px_0_0_var(--color-accent)]`); hover lifts chevron color/translate.
  - Header: gradient violet logo tile (`i-spark`) + "AI Editor" + `+ New Chat` violet-outline button.
  - Search filters client-side (keep Rev 1 logic), `i-search` icon, focus ring accent.
  - **Deferred to v2 (needs backend):** message counts and Running/Review/Done status chips shown in the mockup — `ChatThreadSummary` doesn't carry them and per-thread /live polling for the list is too chatty. Note it; don't fake it.

- [ ] **Step 3: InputArea.tsx** — Rev 1 logic plus: **send button** (24px gradient violet square, `i-send`, disabled-grey when input disabled — `.send`/`.send.off`), sans font (not mono), focus ring (`--accent-brd` + soft glow), Enter sends / Shift+Enter newline, auto-resize to 5 lines. Disabled state + placeholder come from a single `inputAvailability(state)` selector implementing **UX Rule 1's precedence table** (local streaming → gate → plan → executing statuses → enabled), fed by `state.liveStatus` + `state.inputEnabled`; send/Enter are no-ops while disabled or empty (Rule 2).

- [ ] **Step 4: EmptyState.tsx** — frame 4 styling: breathing gradient spark tile (`.spark`, `breathe` animation), "What are we building?" heading, subtitle, three suggestion chips with icons (`i-bolt`, `i-search`, `i-bug`). **Chips pre-fill the input** (spec behavior) — lift input text state up to ThreadView (`draft` state passed to InputArea) instead of posting `sendMessage` directly.

- [ ] **Step 5: Commit**

---

## Task 12: MessageRow, ThreadView, App, main

- [ ] **Step 1: MessageRow.tsx — dispatch on `type` FIRST**

```typescript
export function MessageRow({ msg }: { msg: ChatMsg }) {
  switch (msg.type) {
    case "plan_card": {
      const taskId = (msg.metadata?.taskId as string) ?? msg.taskId ?? "";
      return <PlanCard content={msg.content} taskId={taskId} readOnly />;
    }
    case "diff_card": {
      const taskId = (msg.taskId ?? (msg.metadata?.taskId as string)) || "";
      return (
        <DiffCard
          taskId={taskId}
          diffEntries={(msg.metadata?.diff_entries as DiffEntry[]) ?? []}
          resolved={(msg.metadata?.resolved as "applied" | "discarded" | undefined) ?? null}
          thinkingLog={msg.metadata?.thinking_log as string[] | undefined}
        />
      );
    }
    case "task_card":
      return <TaskCreatedRow taskId={msg.taskId ?? msg.content} />;
    case "scope_card":
    case "validation_card":
    case "command_card":
      // Legacy persisted gate messages (pre-Class-A threads). Render READ-ONLY
      // summaries — interactive gates live ONLY in the /live slot. Do not post
      // decisions from transcript cards.
      return <LegacyGateSummary msg={msg} />;
    default:
      break; // "text" / "diff_summary" fall through to role-based rendering
  }
  if (msg.role === "user") return <UserMessage content={msg.content} />;
  const isBreadcrumb = msg.metadata?.breadcrumb === true;
  if (!isBreadcrumb && msg.content) {
    return <QAMessage content={msg.content} thinkingLog={msg.metadata?.thinking_log as string[] | undefined} />;
  }
  return <AgentRow content={msg.content} breadcrumb={isBreadcrumb}
    thinkingLog={msg.metadata?.thinking_log as string[] | undefined}
    toolEvents={(msg.metadata?.tool_events as ToolEventView[]) ?? []} />;
}
```

`LegacyGateSummary` = small read-only card (icon + title + payload summary). This is a deliberate Class-A correction over chat.js, which still renders stale transcript gates with live buttons (decisions on them 409).

- [ ] **Step 2: ThreadView.tsx** — Rev 1 structure plus:
  - LiveSlot gets all four live props.
  - **UX Rule 3:** `‹` back and `+ New` are disabled (tooltip "A turn is in progress") while a local stream is appending; same flag disables history-row clicks in App. Card one-shot behavior (Rule 2) lives in each card component.
  - Auto-scroll effect also keyed on `streaming?.toolEvents.length` and `streaming?.thinkingEntries.length`.
  - Header per mockup: violet back chevron button (`i-chev-l`, hover accent-bg), truncated title, `i-plus` icon button.
  - **Work bar** (frame 2 `.workbar`): when `!inputEnabled`, render the slim status bar above the input — shimmer top hairline, spinner, client-side elapsed timer (F13), and the **Stop button only while a chat turn is streaming** (`stopTurn` message, F12). Label resolves by the **F14 three-tier precedence**: `Step {n} of {m} — {title}` (step info) → transient phase override (`phaseLabel`: env install, planning tool) → `/live.status` fallback map ("Generating execution plan…", "Planning — exploring the codebase…", …). QA/inline turns without any task fall back to the latest `thinkingStatus` text. The bar must never render without a label while work is in flight.
  - `draft` state for EmptyState chip pre-fill, passed to InputArea.

- [ ] **Step 3: App.tsx / main.tsx** — keep Rev 1.

- [ ] **Step 4: Build + all webview tests green; commit.**

---

## Task 13: Wire up chat-panel.ts + controller live review/error

- [ ] **Step 1: chat-panel.ts `buildHtml()`** — keep Rev 1's implementation (read `webview-ui/dist/index.html`, rewrite `./assets/*` to webview URIs, nonce all scripts, inject CSP). 

- [ ] **Step 2: `reattach()` — reset options (Rev 1 waved this off; it's settable):**

```typescript
reattach(restoredPanel: vscode.WebviewPanel): void {
  this.panel = restoredPanel;
  this.panel.webview.options = {
    enableScripts: true,
    localResourceRoots: [
      vscode.Uri.joinPath(this.extensionUri, "media"),
      vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist"),
    ],
  };
  this.panel.webview.html = this.buildHtml();
  this.registerHandlers();
}
```

Same `localResourceRoots` in `show()`.

- [ ] **Step 3: new inbound branches in `registerHandlers()`:** `acceptTask` → `onAcceptTask(taskId)`, `rejectTask` → `onRejectTask(taskId, reason)`, `resumeTask` → `onResumeTask(taskId, stage)`, `stopTurn` → `onStopTurn()` (new constructor callbacks). New outbound methods `renderLiveReview/clearLiveReview/renderLiveError/clearLiveError/sendLiveStatus`.

- [ ] **Step 4: controller.ts — live review/error derivation in `pollThreadLiveState()`**

After the gate/plan blocks:

```typescript
if (live.status === "READY_FOR_REVIEW" && live.activeTaskId) {
  try {
    const result = await this.clientForChat().getTaskResult(live.activeTaskId);
    this.ui.renderLiveReview({
      taskId: live.activeTaskId,
      modifiedFiles: result.modifiedFiles,
      shadowWorkspacePath: result.shadowWorkspacePath ?? null,
    });
  } catch { /* result not ready yet — next poll retries */ }
} else {
  this.ui.clearLiveReview();
}

if ((live.status === "FAILED" || live.status === "ABORTED") && live.activeTaskId) {
  this.ui.renderLiveError({ taskId: live.activeTaskId, status: live.status });
} else {
  this.ui.clearLiveError();
}
this.ui.sendLiveStatus(live.status ?? null);   // input availability — UX Rule 1
```

Include `status` in the poll's dedup `signature` (it already JSON-stringifies gate/plan/taskId — add `status: live.status`).

The review payload carries the run summary (F8): `stepsTotal` from `result.plan?.steps?.length`, `stepsCompleted` from the extension's observed `step_started` count, and `deviations: string[]` accumulated per task in a `private runDeviations: string[]` — pushed from the stream handlers (`scope_extension_requested` resolution breadcrumbs, command approved-and-remembered, `revision_needed`, step-discarded and validation-accepted breadcrumbs via `chat_breadcrumb` matching `↩`/`✗ Validation`/`✓ Scope`/`✓ Command`), reset when a new `task_card`/`step_started` for a different taskId arrives.

`viewDiffFile` from the ReviewCard sends `path` + empty shadowPath; extend `openInlineDiff` to fall back to `join(latestLiveReview.shadowWorkspacePath, relativePath)` when `shadowPath` is empty and a live review is active (store `latestLiveReview` next to `latestLiveState`).

- [ ] **Step 5: controller.ts — taskId-parameterized actions**

```typescript
async acceptTaskPatch(taskId: string): Promise<void> {
  try { await this.clientForChat().acceptPatch(taskId); this.ui.showInfo("Patch accepted — changes promoted to workspace."); }
  catch (error) { if (this.isBenignConflict(error)) return; this.ui.showError(`Failed to accept patch: ${formatError(error)}`); }
}
async rejectTaskPatch(taskId: string, reason: string): Promise<void> { /* same shape, client.rejectPatch(taskId, reason || "Rejected from chat") */ }
async resumeTaskById(taskId: string, stage: "plan" | "execute"): Promise<void> {
  try {
    const r = await this.clientForChat().resumeTask(taskId, { stage });
    this.ui.showInfo(`Resumed as ${r.taskId}`);
    this.lastLiveSignature = null;           // force next poll to re-render against the child task
    void this.pollThreadLiveState();         // thread active_task_id repoints server-side
  } catch (error) { this.ui.showError(`Failed to resume: ${formatError(error)}`); }
}
```

- [ ] **Step 6: extension.ts** — wire the three new ChatPanel callbacks to these methods.

- [ ] **Step 7: package.json scripts** (extension): keep Rev 1's `webview:build`/`prebuild`. Typecheck + tests + commit.

---

## Task 14: Remove ReviewPanel + delete old files

- [ ] **Step 1: controller.ts removals** — `updatePanel` + `showStepReview` from `ControllerUI`; `pushPanel()`/`buildViewModel()`/`patchEvents` and **all nine `pushPanel()` call sites** (initialize, startTask, attachToTask, providePlanFeedback, resumeTask×1, pullLatestTask, startPolling.onUpdate, openReviewPanel); the `step_review_requested` branch in `startStream` (live gate covers it); keep `buildReviewFileEntries` (still used by `openDiffForFile`). `openReviewPanel()` → `this.ui.openChatPanel()`.

- [ ] **Step 2: extension.ts removals** — `ReviewPanel` import + instantiation; `updatePanel`/`showStepReview` from the `ui` object; `panel.show()` in `startTask`/`attachToTask`/`openReviewPanel` command handlers (repoint `aiEditor.openReviewPanel` to open chat for muscle-memory compat, and remove it from `package.json` `contributes.commands` + `activationEvents` in a follow-up if desired); `panel.dispose()`.

- [ ] **Step 3: test stubs** — `test/controller.test.ts` lines ~186/~201: remove `updatePanel`/`showStepReview`, add the new UI members (appendToolEvent, appendToolResult, renderLiveReview, clearLiveReview, renderLiveError, clearLiveError).

- [ ] **Step 4: delete files** — `git rm apps/vscode-extension/media/chat.js apps/vscode-extension/media/marked.umd.js apps/vscode-extension/src/review-panel.ts`. Remove `"marked": "^18.0.3"` from extension deps (only referenced by chat-panel's old buildHtml + the media file; the React app uses react-markdown), `npm install` to refresh the lockfile. Drop `ReviewPanelViewModel` from `src/types.ts` if nothing references it.

- [ ] **Step 5: full typecheck + tests + commit.**

---

## Task 15: Root scripts + integration smoke test

- [ ] **Step 0: step-review default flip (UX decision)** — in `scripts/stress/start-backend.sh`, export `CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT=false` alongside the existing scope-policy exports, so chat-driven large tasks pause at every step gate by default (see "Step-review default is a UX decision").

- [ ] **Step 1: root package.json** — webview-ui is not a workspace; extend explicitly:

```json
"test": "npm run -w @ai-editor/editor-client test && npm run -w @ai-editor/vscode-extension test && npm --prefix apps/vscode-extension/webview-ui test",
"typecheck": "<existing> && npm --prefix apps/vscode-extension/webview-ui run typecheck"
```

(`build` already reaches webview-ui via the extension's `prebuild`.)

- [ ] **Step 2:** `npm run build && npm run test` — all three packages green.

- [ ] **Step 3: dev-host smoke test** (backend running via `start-backend.sh`):

```bash
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/shadow-forge-stress"
```

Walk the full matrix:
- History view: day groups, search, new chat, thread switch
- QA turn: thinking block streams → collapses; markdown answer; copy button
- small_change: tool pills with expandable Input/Output; diff card; Accept all → resolved state
- large_change: plan card in live slot → Implement → command gate (radio scopes + remember) → step gate → **ReviewCard accept at READY_FOR_REVIEW** (the path that used to require the Review panel)
- Plan feedback: regenerated plan **remounts** the live card (key change) with fresh buttons
- Kill the backend mid-execution → ErrorCard renders from /live; Resume creates child task and the live slot follows it
- Reload the webview (Developer: Reload Window) mid-gate → gate card re-derives from /live
- **UX rules:** input disabled with the correct placeholder at every Rule-1 state, including after a reload mid-execution; double-click Implement / Allow once / Finish → single POST (row resolves on first click); back/history/+New locked while a turn streams; step gates appear by default (auto-accept flipped off in Step 0)
- **Silent phases (F14):** approve a plan and watch the JSON-generation window — work bar must show "Generating execution plan…" + running timer, never blank; trigger a dep install (touch a manifest) → "Syncing dependencies…" persists until `env_install_done`; first `search_semantic` shows a spinning pill while embeddings load
- ReviewCard shows the run summary: files, `n of m steps`, and deviation lines after a run that included a scope extension or a discarded step

- [ ] **Step 4: final fixes commit.**

---

## Deferred to v2 (each needs a backend addition — do NOT fake client-side)

- **Cooperative task abort (F12):** an abort flag/event checked between ToolLoop iterations and plan steps, ABORTED-aware saves (no stale-object clobber), shadow cleanup only after the engine coroutine acknowledges — then the Stop button can appear during task execution. Today's `/cancel` is only safe for queued/stuck/terminal tasks.
- **Durable failure detail (F9):** persist `failure_summary {step_id, step_index, error_class, message}` on the task at failure time and expose via `/live` — until then the error card's detail is ephemeral (extension memory of `step_started`/`patch_failed`).
- **Final-review redesign (F8):** READY_FOR_REVIEW is hollow today (`engine.py:710` TODO) — changes are already partial-promoted per step, the final promote re-copies, and reject doesn't revert. Either collapse accept → SUCCEEDED directly, or implement a true final reject (checkpoint-based real-workspace restore). Until then the ReviewCard uses "Finish / Close without finishing" copy.
- Inline diff text in DiffCard/StepGate (add `unified_diff` per entry to `diff_ready` + `pending_step_review` — `_compute_diff_entries` already computes it and throws it away, `engine.py:1067`; one change covers both paths)
- Persisted transcript record of step-review diffs (today only a `✓/↩` breadcrumb survives — the reviewed file list is not stored as a chat message, unlike the inline `diff_card`)
- History list message counts + Running/Review/Done status chips (extend thread summaries; see F10)
- Structured plan steps at the approval gate (would require moving the markdown→JSON plan conversion before approval; F5 caveat)
- `✓ Step completed` breadcrumb when `step_review_auto_accept` is on (today breadcrumbs only fire on explicit review decisions, `engine.py:1827`)
- **Durable run summary (F8):** persist a `run_summary` on the task (steps completed, scope extensions, delta replans, discarded steps, validation outcome — `execution_state` holds most of it server-side but none is exposed via TaskView/TaskResult) so the ReviewCard summary survives reloads instead of relying on extension-observed events
- **Per-task "Review each step" toggle in the composer:** needs the flag plumbed through `POST /threads/{id}/message` → `create_task_from_chat` (v1 flips the default via `start-backend.sh` env only)
- Token/cost per turn, @mentions, model selector (spec roadmap)
- Light-theme adaptation: map surface tokens to `--vscode-*` vars while keeping the violet ramp as fixed brand (v1 is dark-first by decision, Task 3)

## Future note (NOT for this run — recorded only)

The execution tool loop has no lightweight way to adjust the plan mid-step when it concludes the current approach needs updating: its only escape hatches are a scope-extension request (file-level only) or `revision_needed` → delta replan (full `PlanningAgent.revise()` exploration — costly, budgeted at `max_delta_replans=3`). A cheaper middle path (e.g. in-place step amendment without re-exploration) is a backend/agent design topic for a future brainstorm — explicitly out of scope for this UI rebuild.
