# Clarify as an interactive Class-A gate — design

**Date:** 2026-06-26
**Status:** Approved (design); implementation pending
**Area:** Reactive chat controller (`CRUCIBLE_CHAT_CONTROLLER=1`)

## Problem

The controller's `clarify` action currently renders as a plain agent chat message — a
question the user must read and answer by typing a free-form reply. The user gets no
signal about what the agent *thinks* the likely answers are, and answering requires
composing a full sentence even when the agent already has a short list of candidates in
mind.

The system already has a proven pattern for interactive, durable, reload-surviving
decision points: the **Class-A live gate** (`ModeGate`, driven by `propose_mode`). The
goal is to give `clarify` the same treatment.

## Goal

Turn `clarify` into a live interactive card that:

1. Shows the agent's question.
2. Offers model-authored candidate answers as one-click options.
3. Always appends a free-text "Something else…" escape hatch as the last option.
4. On selection (option click **or** free-text submit): resolves the gate, writes **one
   combined breadcrumb** (`❓ {question} → {answer}`), and **auto-resumes** the agent loop
   with the chosen answer injected as the user's reply.

## Non-goals

- Changing `clarify` semantics in the classifier-based legacy `ChatAgent` path. This is
  controller-only (`CRUCIBLE_CHAT_CONTROLLER=1`).
- Nested/multi-question clarifies. One question, a flat option list.
- Multi-select answers. Exactly one answer resolves the gate.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| On select | **Auto-resume the agent** — resolving re-enters the loop with the answer as the user reply (mirrors `ModeGate` edit/explain re-entry). Mid-EDIT clarifies resume in EDIT. |
| Breadcrumb | **One combined breadcrumb**: `❓ {question} → {answer}`. |
| Options source | **Model-authored** (`options` array on the clarify action). |
| Free-text escape | **Always present**, UI-appended — the model never authors it. |
| EDIT-resume mechanism | The gate payload carries `resume_phase`; this **replaces** the `_edit_clarify_pending` side map. |

## Design

The implementation mirrors the existing `propose_mode → ModeGate → /mode-decision →
resolve_mode` flow at every layer.

### 1. Schema & action (backend)

`chat/controller_prompts.py` — the `clarify` action schema gains an optional `options`
array:

```python
"clarify": {
    "required": ["question"],
    "properties": {
        "question": _STR,
        "options": {"type": "array", "items": _STR},
    },
}
```

Teaching update (the `clarify` variant block): instruct the model to emit 2–4 short
candidate answers in `options` (what it thinks the user likely means), and to **never**
add a free-text/"something else" option itself — the UI appends that automatically. Zero
options remains valid and degrades to a free-text-only card (backward compatible).

`chat/controller_loop.py` already returns `ControllerOutcome(kind="clarify", …)` at
L431-434. It carries the question in `text` and now also passes `options` through in
`payload` (e.g. `payload={"question": q, "options": opts}`). The existing empty-question
correction (L136-137) is unchanged.

### 2. Render as a gate, not a chat message (backend)

Today `_finish` (`chat/controller.py:433`) routes `clarify` through the `answer` branch,
emitting a `chat_response` bubble. Replace this with a dedicated branch calling a new
`_present_clarify_choice` — a clone of `_present_mode_choice` (`controller.py:517`):

1. Persist exploration pills + thinking as a durable record (reload survival).
2. `set_controller_gate(thread_id, PendingGate(kind="clarify", payload={question,
   options, resume_phase}))`.
3. Broadcast `chat_done` to end the stream.

`resume_phase` is `"EDIT"` when the clarify fired while `sm.phase == "EDIT"`, else `None`.
This carries the resume target **in the gate**, replacing the `_edit_clarify_pending` set
(`controller.py:137`, set/cleared at L408-411 and read at L280-285). Those sites are
removed; the EDIT-resume decision moves to `resolve_clarify` reading `resume_phase`.

The question is **not** echoed as a chat bubble — it lives in the card. The combined
breadcrumb captures it durably at resolve time.

Ledger semantics are unchanged: `clarify` stays non-terminal (`controller.py:397-403`),
so an in-progress todo list survives the gate and the re-entry.

### 3. `resolve_clarify` (backend)

New method on `ChatController`, mirroring `resolve_mode` (`controller.py:726`):

```python
async def resolve_clarify(self, thread_id, answer, *, channel_id, goal): ...
```

1. **Idempotency guard** — read the thread's gate; no-op unless `gate.kind == "clarify"`.
   The read→clear pair has no `await` between it (sqlite is sync), so concurrent posts
   can't both re-enter.
2. **Empty-answer guard** — a blank `answer` is rejected/no-op (the card shouldn't submit
   empty, but defend against it; mirrors the loop's empty-question correction).
3. Read `question` + `resume_phase` from `gate.payload` **before** clearing.
4. `set_controller_gate(thread_id, None)`.
5. Write **one** breadcrumb via `_write_breadcrumb`: `❓ {question} → {answer}`
   (persisted + broadcast, so it survives reload).
6. Re-enter the loop as a fresh streamed turn (new `turn_id`, incremental pills, debug
   artifacts): inject `{"role": "user", "content": answer}` into `seed_history` and run in
   `resume_phase` (EDIT) or DECIDE. Identical machinery to `resolve_mode`'s edit/explain
   re-entry.

`answer` is the option's text or the user's typed free-text — both are plain strings to
the agent.

### 4. Route (backend)

`POST /chat/threads/{thread_id}/clarify-decision` with body `{answer}` — a near-clone of
`post_mode_decision` (`api/routes.py:1234`):

- Read `goal` from the thread's last user message (don't trust the client).
- 409 if a turn is already in flight (`_active_turns`).
- `clear_replay`, `launch_turn(resolve_clarify(...))`, detached subscribe-relay
  `StreamingResponse` that closes on `chat_done`/`done`.

### 5. Frontend

- **`ClarifyGate.tsx`** (new, sibling of `ModeGate.tsx`): `CardShell` header = the
  question; one `BtnGhost`/`BtnPrimary` per model option; an always-present last row
  "Something else…" with a text input + submit. Picking an option →
  `vscode.postMessage({type: "clarifyDecision", threadId, answer: optionText})`;
  free-text submit → same message with the typed value. One-shot `resolved` guard like
  `ModeGate`. Tolerant `parseOptions` for missing/malformed entries.
- **`LiveSlot.tsx:33`** — add `case "clarify": return <ClarifyGate taskId={…}
  payload={gate.payload} />`.
- **`inputAvailability.ts:67`** — treat `kind === "clarify"` like `"mode"`: disable the
  main composer (the card's free-text is the input path).
- **`chat-panel.ts:142`** — handle `clarifyDecision` → POST `/clarify-decision` (clone of
  the `modeDecision` handler).
- **`controller.ts`** — wire the clarify-decision dispatch to stream chat events (mirror
  the mode-decision streaming path).

### 6. Contract touch

- Python `PendingGate.kind` gains `"clarify"`.
- The editor-client Zod schema for `/live` `pending_gate.kind` must add `"clarify"` to its
  enum, or `getThreadLiveState` Zod-throws on every 1s poll (the `.min(1)`-class footgun
  documented in CLAUDE.md). Grep the exact enum location before editing
  (`editor-client/src/contracts/task-contracts.ts`).

### 7. Testing

**Python:**
- Loop emits `clarify` with `options` → `ControllerOutcome.payload` carries them.
- `_present_clarify_choice` sets a `PendingGate(kind="clarify")` with question/options/
  resume_phase and does **not** emit a `chat_response`.
- `resolve_clarify` writes the combined `❓ q → a` breadcrumb and re-enters the loop;
  DECIDE variant **and** the EDIT-resume variant (mid-edit clarify resumes in EDIT).
- Gate idempotency: a second `resolve_clarify` no-ops.
- Empty-answer guard no-ops.

**Frontend (vitest, webview-ui jsdom config):**
- `ClarifyGate` renders the question, each option button, and the free-text row.
- Clicking an option posts `clarifyDecision` with that option's text.
- Submitting free-text posts `clarifyDecision` with the typed value.
- One-shot guard: a second click is ignored.

## Risks & subtleties

- **EDIT-resume** is the trickiest piece: a clarify raised mid-feature must resume in EDIT,
  not restart in DECIDE. Folding `resume_phase` into the gate payload removes the
  `_edit_clarify_pending` side-channel and keeps the resume target colocated with the
  gate.
- **Empty free-text** must not produce an empty re-entry — guarded in both the card
  (don't submit blank) and `resolve_clarify` (no-op on blank).
- **Backward compatibility**: a clarify with zero `options` still works — the card shows
  only the free-text row, matching today's "type your answer" behavior but as a gate.

## Scope

~2 backend files (`controller.py`, `controller_prompts.py`, `controller_loop.py`) + 1
route (`routes.py`) + 1 model touch (`PendingGate`) + 4 frontend files + 1 contract enum +
tests. One implementation plan; no decomposition needed.
