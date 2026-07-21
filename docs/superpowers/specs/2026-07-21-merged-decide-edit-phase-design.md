# Merged DECIDE/EDIT Controller Phase — Design

## Background

This addresses open finding D from `docs/superpowers/2026-07-17-chat-controller-live-smoke-findings.md`:
every chat turn in the reactive controller (`agentd/chat/controller_loop.py` +
`controller_phase.py`) starts fresh in a restricted `DECIDE` phase, which forbids
edit-capable actions until the model emits `propose_mode` and the user picks a mode
via the ModeGate. This forces a full mode re-negotiation on every plain follow-up
message (e.g. "continue"), even mid multi-step work — live-smoke observed the model,
correctly remembering it was mid-edit, trying to call `run_command` straight from a
fresh `DECIDE` turn and getting rejected by the phase schema, requiring an explicit
"propose mode... pick edit" nudge to recover.

This was flagged as a design question, not a bug, because the fix isn't a patch —
it's an inversion of the default: instead of "editing is the opt-in, gated behind a
mode pick," editing becomes the default, and a restricted "plan first" mode becomes
the opt-in. This mirrors how this CLI's own Plan Mode already works (default agentic,
explicit opt-in restriction) rather than inventing a new pattern.

## Goal

Collapse the phase model so most turns can act immediately (answer, edit, run
commands, submit changes) without a mode-negotiation round-trip, while preserving an
opt-in, sticky "Plan Mode" for users who want to discuss/refine before any changes
happen — and do so without regressing any of the existing per-phase teaching/gating
logic (skill-check, todo-list steering, run_command restriction, clarify resume).

## Phase model

Three phases collapse to two:

| Phase | Allowed action types | Entered when |
|---|---|---|
| `PLAN` (was `DECIDE`) | `tool_call`, `answer`, `clarify`, `propose_mode` | The composer's sticky Plan Mode toggle is ON for this message |
| `ACTIVE` (merges `DECIDE`+`EDIT`; replaces both) | `tool_call`, `answer`, `clarify`, `edit`, `submit_changes` (`run_command` rides `tool_call`) | Default (toggle OFF); also entered for the rest of any turn where `propose_mode` resolves to "Implement this plan" |

`EXPLAIN` is deleted. Its only purpose — giving the user a detailed, readable
description of the model's approach, because the `propose_mode` plan-sketch card was
too shallow to read — is fixed at the source (see UI section) rather than worked
around with a separate phase. A user who wants to react to the plan now types
feedback into the ModeGate card's existing "Chat about this approach…" field (already
present in `ModeGate.tsx`, already supersedes the pending gate and re-enters `PLAN`
with the feedback appended) — this is today's existing feedback/regenerate mechanic,
unchanged.

`propose_mode` becomes `PLAN`-only. It is removed from `ACTIVE`'s action-type list
entirely — in `ACTIVE` the model never asks permission to edit, it just acts or
answers, matching the request. Inside `PLAN`, `propose_mode`'s option set simplifies:
no more `edit | explain`, just an "Implement this plan" primary option (still labeled
by the model), plus `create_task`/`resume` when the task subsystem flag is enabled
(unchanged, orthogonal to this change). There is no "keep iterating" button — typing
into the card's feedback field already achieves that by re-entering `PLAN`.

`PLAN` must be explicit to the model, not just inferred from a restricted action set.
Today's `DECIDE` needs no such framing — it's simply the SM's only starting state,
never a state the user deliberately chose. Under this design, `PLAN` **is** a
deliberate user choice (the sticky toggle), and the model should know that, so its
behavior matches intent (e.g. leaning into discussion/planning rather than rushing to
wrap up). `PLAN`'s system-prompt teaching block gets an explicit line stating Plan
Mode is active by the user's own choice, distinct from `ACTIVE`'s teaching (which
needs no such framing, since acting is simply the default).

## Sticky toggle mechanism

The Plan Mode toggle must persist across threads (a global/user preference, not
per-thread), and the backend needs to know it per-message to pick a starting phase.
Rather than inventing new backend-persisted-preference machinery, this reuses the
existing `step_review` pattern:

- **The extension owns persistence.** The composer's Plan Mode checkbox is stored in
  VS Code's `globalState` (same tier as provider/model settings) — survives across
  threads, workspaces, and reloads.
- **Sent per-message.** `POST /v1/chat/threads/{id}/message` gains a `plan_mode: bool`
  field, mirroring `step_review`. `ChatController._run_loop` reads it to pick the
  starting phase for that call — no new phase-inference logic needed.
- **Exiting Plan Mode is a client-side side effect of clicking "Implement this
  plan."** The button's existing `modeDecision` POST handler also flips the
  extension's `globalState` boolean off in the same client-side action. No new
  backend signal, breadcrumb, or SSE event is needed for this — the next message
  (this thread or any other) simply sends `plan_mode=false`.

## Refactor map (controller_prompts.py / controller_loop.py / controller.py / controller_phase.py)

**Clean renames, no behavior change:**
- `_PHASE_TYPES` (`controller_prompts.py`): merge `DECIDE`+`EDIT` entries into one
  `ACTIVE` entry; delete the `EXPLAIN` entry.
- `ControllerPhaseSM` (`controller_phase.py`): two states; `enter_edit_mode`/
  `enter_explain_mode` collapse into one `enter_active_mode` (or equivalent); default
  constructor phase depends on the caller-supplied starting phase rather than always
  being `DECIDE`.
- `_decide_state_change_correction` (`controller_loop.py`): `phase != "DECIDE"` check
  becomes `phase != "PLAN"` — same job (reject state-mutating tool calls in the
  read-only phase), phase renamed.
- `resolve_mode` (`controller.py`): `phase = "EDIT" if mode == "edit" else "EXPLAIN"`
  becomes a single `phase = "ACTIVE"` (only one thing to transition into).
- Every `self._sm.phase` read used purely for logging/turn-trace/edit-entry-clearing
  bookkeeping renames with zero behavior risk.
- `resume_phase` values used by clarify-resume (`"EDIT"` → `"ACTIVE"`).
- `PROPOSE_MODE_CORRECTION`'s valid-modes text (`controller_loop.py`) drops `explain`
  from the enumerated valid modes.

**Genuine merges (not renames) — the parts to build carefully:**
- **The `edit_entry`/`decide_entry` first-move hints** (`controller_prompts.py`,
  currently a strict `if phase=="EDIT" / else DECIDE` binary) become one merged
  entry-hint for `ACTIVE`'s iteration 0. Today, a fresh `EDIT`-phase turn only ever
  starts post-`propose_mode`, with `DECIDE` having already run the skill-check on an
  earlier iteration of the same turn — under the merged phase, a turn starting fresh
  in `ACTIVE` (toggle off, brand-new thread) needs **both** the skill-check ("before
  locating code, answering, or proposing anything...") and the "first action, nothing
  started — decide todo-list-vs-direct-edit" guidance on the same iteration 0. The
  skill-check wording's reference to "proposing" needs adjusting since `propose_mode`
  isn't reachable from `ACTIVE` at all. `PLAN`'s version of this hint (skill-check +
  "search before answering cold" + propose-when-ready) stays essentially as today's
  `DECIDE` hint.
- A couple of "Do NOT propose_mode again" reminders inside the old `EDIT` hint text
  are now dead (the schema itself prevents it in `ACTIVE`) — delete rather than merge.

## UI

`ModeGate.tsx`'s plan-sketch display (`planSketch` block, currently a plain
`whitespace-pre-wrap` div) needs to become scrollable/expandable so a long, detailed
plan is actually readable before the user decides — this is what makes dropping
`EXPLAIN` safe (its whole reason for existing was working around this card being too
shallow to read).

## Edge cases

- A thread with an in-flight `EDIT`-era todo ledger (from before this ships) must
  keep working — the ledger's pending/in_progress state and the `submit_changes`
  block are keyed off ledger contents, not the phase name, so this should be
  unaffected; cover with an explicit test.
- Task subsystem OFF (default): `create_task`/`resume` stay stripped from the
  offered `propose_mode` options, same mechanism as today, just filtering a smaller
  base option set (no `explain` to also filter out).
- A clarify raised mid-`ACTIVE` still needs `resume_phase="ACTIVE"` (renamed from
  `"EDIT"`) in its gate payload for `resolve_clarify` to resume correctly.

## Testing

Existing phase-name-coupled tests need updating rather than rewriting from scratch,
since most of the underlying behavior they assert still holds:
- `test_controller_explain.py` — deleted (subject no longer exists).
- `test_controller_decide_no_run_command.py`, `test_controller_propose_mode_validation.py`,
  `test_controller_loop_dedup_clears_on_edit.py`, and others referencing `"DECIDE"`/
  `"EDIT"` phase strings directly — renamed/re-asserted against `"PLAN"`/`"ACTIVE"`.
- New coverage needed: a fresh `ACTIVE`-phase turn (toggle off, brand-new thread) gets
  both halves of the merged entry-hint on iteration 0; `plan_mode=true` starts a turn
  in `PLAN`; a `propose_mode`→"Implement this plan" resolution lands the turn in
  `ACTIVE` for its remainder.

## Out of scope

- The exact prompt wording of the merged entry-hint (drafted during implementation,
  not pinned here).
- Any change to the task subsystem's `create_task`/`resume` mode-offering logic
  itself (only the surrounding `explain` removal touches it).
- Items B and E from the 2026-07-17 findings doc (different repo; unroot-caused
  runtime issue) — unrelated to this change.
