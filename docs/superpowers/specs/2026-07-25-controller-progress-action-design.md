# Controller `progress` action — non-terminal mid-turn narration — design

**Date:** 2026-07-25
**Status:** approved (brainstorm 2026-07-25)
**Depends on:** branch `investigate/skill-handoff-gap` (uncommitted, worktree
`.claude/worktrees/agent-aa9fd20186f12b845/services/agentd-py/`) — this feature builds on the
answer-intent-divergence guard introduced there and must land on/after it, NOT directly on
`feat/merged-decide-edit-phase`.
**Context:** `docs/superpowers/2026-07-24-controller-skill-handoff-gap-findings.md`, Finding 3.

## Problem

The controller's action space (`tool_call | answer | clarify | propose_mode | edit |
submit_changes`) gives the model exactly one way to say anything to the user — `answer` — and
`answer` is terminal. During a long multi-iteration turn (up to `CRUCIBLE_CONTROLLER_MAX_ITERS`,
default 500) the model accumulates narration pressure ("plan saved, now executing task 1…") and
its only outlet ends the turn. Finding 3 caught this live: a response whose `thought` named the
correct next tool (`write_todos`) but whose `type` was `answer`, stranding the session until a
human nudge. The divergence guard on `investigate/skill-handoff-gap`
(`_answer_intent_divergence_correction`, `controller_loop.py:201` in the worktree) *rejects* that
shape but offers the model nowhere legitimate to put the narration. This design adds the outlet:
a non-terminal, user-visible `progress` action. Guard (stick) + outlet (carrot) together.

Secondary benefit: live user visibility during long turns. Today the `thought` field surfaces
only as a 200-char tool-pill caption and `chat_agent_thinking` says a generic "Thinking…".

## Decision summary (from brainstorm)

- **Approach A** — a new non-terminal `progress` action type. Rejected alternatives: a
  `final: false` flag on `answer` (boolean multi-mode action — prohibited style, and a weak-model
  ambiguity trap); presentational streaming of the existing `thought` field (no outlet, doesn't
  address the attractor).
- **Durable** — progress notes persist as transcript messages, not live-only.
- **Guardrails: cap + dedup** — mechanical, via the existing malformed-correction chain; prompt-
  only teaching already failed for this exact pattern (Finding 3 reproduced live despite prose
  WRONG/RIGHT examples).

## Design

### Schema & prompts (`agentd/chat/controller_prompts.py`)

- `"progress"` added to the `type` enum and to `_PHASE_TYPES` for **both** `ACTIVE` and `PLAN`
  (long plan explorations narrate too).
- Variant shape: `{type: "progress", thought, note}`. `note` is required non-empty (enforced in
  the per-type required/properties map alongside `answer`'s); dispatch caps it at 500 chars.
- Teaching block added to `CONTROLLER_SYSTEM_PROMPT`: one sentence of capability, one worked
  example, and the boundary — `progress` does NOT end the turn; `answer` does; never use `answer`
  for status. Framed per the no-superiority-framing rule: state when each shines
  (`progress` = a short user-visible status note mid-work; `answer` = the finished deliverable),
  no comparative steering.
- Tight-`oneOf` variant (providers with `supports_oneof_grammar`) gains the matching branch.

### Loop dispatch (`agentd/chat/controller_loop.py`)

New branch beside `tool_call`, handling a `progress` response:

1. Validate `note` non-empty (empty → existing empty-field correction path).
2. **Guardrail — consecutive cap:** if the immediately preceding accepted action this turn was
   also `progress` (no real action between), reject through the `_MAX_MALFORMED`-bounded
   correction chain with "you already posted a note; take the action you described." No new
   retry primitive.
3. **Guardrail — dedup:** exact `note` text already emitted this turn → same rejection path
   (mirrors the `emit_patch` dedup discipline). Turn-scoped set, cleared per `run()`.
4. Persist via a new `progress_note_cb: Callable[[str], Awaitable[None]] | None` constructor
   param (same threading pattern as `EditRecordCb`), wired from `ChatController._run_loop`.
5. Broadcast a `chat_progress` SSE event `{note}` on the chat channel.
6. Append the observation to history: `Progress noted: "<note>" — now continue with the actual
   work.` Continue the loop.
7. Accounting: counts toward `max_iters`; does **not** reset `consecutive_malformed` (a note is
   not evidence of progress); does not touch the todo ledger or the edit session.

`_answer_intent_divergence_correction`'s rejection text is updated to name the escape hatch:
narrate via `progress`, then take the action.

### Persistence (`agentd/chat/controller.py`)

`progress_note_cb` implementation mirrors `_write_breadcrumb`: persist an `agent/text`
`ChatMessage` with `metadata.progress = true`, broadcast to the chat channel. It is a durable
transcript message — reload mid-turn keeps the story (Class-A convention: durable record + live
poke).

### Contracts (`apps/editor-client`)

- `StreamEvent` union gains `chat_progress` (`{note: string}`).
- No `/live` change: a progress note is a message, not live-slot state — the breadcrumb precedent.
  **`controller.ts` `lastLiveSignature` is therefore untouched** (the dedup-signature invariant
  does not apply to transcript messages).

### Frontend (`apps/vscode-extension` + `webview-ui`)

- Live: `chat_progress` renders immediately as a de-emphasized agent line (breadcrumb-adjacent
  styling, existing violet-cool tokens; no new component if the breadcrumb renderer can take a
  variant class).
- Reload: the persisted message renders from history via `metadata.progress` (mirror the
  `breadcrumb: true` handling in the message renderer).

## Error handling

- Persist-callback failure: best-effort — log, continue the loop (a narration write must never
  kill a turn; same discipline as memory `prepare_turn`).
- Cap/dedup rejections consume the shared `_MAX_MALFORMED` budget (3); exhaustion ends the turn
  through the existing `ControllerLoopExhausted` → graceful `⚠️` answer path. No special case.

## Testing (TDD)

Scripted-engine tests through the real `ControllerLoop.run()`:

1. `progress` dispatch: note persisted (spy cb), `chat_progress` broadcast, loop continues to a
   subsequent real action, history carries the observation line.
2. Consecutive cap: `progress` → `progress` rejected with the cap correction; `progress` →
   `tool_call` → `progress` accepted.
3. Dedup: identical `note` twice in one turn rejected; differing notes accepted.
4. Divergence-guard redirect: guard rejection text names `progress`; the Finding-3 smoking-gun
   transcript refit as guard-fires → model retries with `progress` → loop continues.
5. `PLAN`-phase progress accepted.
6. Empty `note` → empty-field correction.
7. No-cb-wired negative control: dispatch still continues the loop (broadcast only).

Frontend: vitest for the `chat_progress` event mapping + reload render from
`metadata.progress`; editor-client Zod round-trip for the new event.

## Out of scope (YAGNI)

- Streaming/updating a note in place (each note is append-only).
- Rendering the raw `thought` field (approach C — explicitly not chosen).
- Any change to `answer` semantics; it stays strictly terminal.
- Rate limiting beyond cap + dedup (revisit only if live dogfooding shows note spam).
