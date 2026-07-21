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

**Revision note:** this spec went through an Opus architecture review before any
implementation plan was written from it. The first draft had five Critical gaps
(verified against the code, not taken on faith) — this version folds in the reviewed,
root-cause fixes for all of them rather than leaving them as open risks. Each section
below reflects the corrected design.

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
| `ACTIVE` (merges `DECIDE`+`EDIT`; replaces both) | `tool_call`, `answer`, `clarify`, `edit`, `submit_changes`, plus `propose_mode` **only when the task subsystem flag is on** (see "Task subsystem reachability" below) | Default (toggle OFF); also entered for the rest of any turn where `propose_mode` resolves to `"implement"` |

`EXPLAIN` is deleted. Its only purpose — giving the user a detailed, readable
description of the model's approach, because the `propose_mode` plan-sketch card was
too shallow to read — is fixed at the source (see UI section) rather than worked
around with a separate phase. A user who wants to react to the plan now types
feedback into the ModeGate card's existing "Chat about this approach…" field (already
present in `ModeGate.tsx`, already supersedes the pending gate and re-enters `PLAN`
with the feedback appended) — this is today's existing feedback/regenerate mechanic,
unchanged. (This trades a dedicated elaboration generation for "the user can just ask
again" — the capability is preserved, but the mechanism is re-asking, not a fresh
EXPLAIN-style elaboration call; worth knowing, not worth blocking on.)

`propose_mode` is `PLAN`-default. In `ACTIVE`, the model never asks permission to
*edit* — that path is gone entirely. Inside `PLAN`, `propose_mode`'s option set
simplifies: no more `edit | explain`, just an `"implement"` primary option (labeled by
the model, e.g. "Implement this plan"), plus `create_task`/`resume` when the task
subsystem flag is on. There is no "keep iterating" button — typing into the card's
feedback field already achieves that by re-entering `PLAN`.

**Wire value for the primary option: `"implement"`, not a relabeled `"edit"`.**
`_VALID_MODES` currently treats `mode` as "which phase to enter" (`edit → EDIT`).
Under the merged model, picking this option isn't entering a new phase the way `edit`
used to — it's *exiting* `PLAN` back to the pre-existing default `ACTIVE`. Reusing the
literal `"edit"` here would collide with the unrelated `edit` *action type* the model
already emits mid-turn (`_PHASE_TYPES["ACTIVE"]` has its own `"edit"` string) —
an avoidable footgun for anyone reading `resolve_mode` next to the dispatch logic.
`_VALID_MODES` becomes `frozenset({"implement", "create_task", "resume"})`;
`resolve_mode`'s dispatch becomes `if mode == "implement": phase = "ACTIVE"`.

**Task subsystem reachability.** Today `create_task`/`resume` ride `propose_mode`,
reachable from the sole `DECIDE` phase every turn started in. Making `propose_mode`
`PLAN`-only would make task creation reachable *only* when the user has deliberately
turned Plan Mode on — a real regression for task-subsystem-on deployments (CLAUDE.md:
"Inline `edit` is now the PRIMARY path... not a small-change-only path" — the task
path is meant to stay an equally-available alternative, not one gated behind an extra
opt-in). Fix: `_PHASE_TYPES["ACTIVE"]` conditionally includes `propose_mode` when
`task_subsystem_enabled` is true (computed once at `ControllerLoop` construction, same
place that flag already flows in). When reachable from `ACTIVE`, `propose_mode`'s
allowed modes are restricted to `{create_task, resume}` — never `"implement"`, since
`ACTIVE` is already the implementing phase (`_propose_mode_correction`'s
`allowed_modes` argument is already parameterized per call site, so this is a matter
of passing a different set, not new plumbing). When the task subsystem is off (the
default), `ACTIVE`'s action set is unchanged from what's stated in the table above.

`PLAN` must be explicit to the model, not just inferred from a restricted action set.
Today's `DECIDE` needs no such framing — it's simply the SM's only starting state,
never a state the user deliberately chose. Under this design, `PLAN` **is** a
deliberate user choice (the sticky toggle), and the model should know that, so its
behavior matches intent (e.g. leaning into discussion/planning rather than rushing to
wrap up). This framing is a **per-turn payload addition, not a system-prompt
addition** — see the Refactor Map's C4 entry for why and exactly where.

## Sticky toggle mechanism

**Correction to an earlier premise:** `step_review` is not actually persisted
anywhere today — `InputArea.tsx`'s `stepReview` is plain React `useState(true)`,
reset on every webview reload. `plan_mode` reusing "the step_review pattern" is true
only for the *per-message wire shape* (a boolean sent alongside `sendMessage`); the
`globalState` persistence tier this feature needs is genuinely new plumbing, not
existing-pattern reuse. Scoping that plumbing explicitly:

1. **Extension-side storage (new).** A `crucible.chat.planMode` key on
   `context.globalState` (same tier as provider/model settings), with two helpers —
   `getPlanMode(): boolean` (default `false`) and `setPlanMode(v: boolean): Promise<void>`.
   This is the **single writer** for this value; every path that changes it (manual
   toggle, auto-exit-on-implement) goes through this one function, so there is no
   separate "auto-flip" code path that can drift out of sync with the manual one.
2. **Hydration on webview open.** The existing `webviewReady` handshake in
   `chat-panel.ts` gains one more push: `{ type: "planModeState", enabled: getPlanMode() }`.
   The webview stores this in its top-level app state (same tier as `liveStatus`) and
   passes it down to the composer as a controlled prop, replacing what would otherwise
   be local-only `useState`.
3. **Toggle → extension.** Clicking the composer's Plan Mode checkbox posts
   `{ type: "setPlanMode", enabled }`; the extension calls `setPlanMode` (step 1) and
   the webview's controlled state updates from that same round-trip.
4. **Auto-exit on "Implement this plan."** `ModeGate.tsx`'s `handlePick` already posts
   `{ type: "modeDecision", threadId, mode }` on click. When `mode === "implement"`,
   it *also* posts `{ type: "setPlanMode", enabled: false }` in the same handler — a
   second message through the identical path as step 3, not a special case the
   extension has to infer from the mode decision. This is what avoids a
   checkbox-reads-on-while-wire-sends-false desync: there's exactly one write path and
   one place the webview's mirror gets refreshed from.
5. **Per-message wire field.** `controller.ts`'s `sendChatMessage` reads the current
   (extension-sourced) plan-mode value and adds `planMode` alongside `stepReview` in
   the outgoing message. Threaded through `chat-panel.ts`'s `onMessage` call the same
   way `stepReview` already is.
6. **editor-client contract.** `sendChatMessage`'s options gain `planMode?: boolean`;
   the HTTP client maps it to a `plan_mode` field on the request body when present
   (same conditional-spread convention `stepReview` uses today).
7. **Backend route + controller.** The chat-message route gains
   `plan_mode: bool | None = None` on its request model, passed through to
   `handle_message(..., plan_mode=plan_mode)`. Inside, the starting phase for
   `_run_loop` is `"PLAN" if plan_mode else "ACTIVE"` — this is the only
   phase-inference logic needed; everything above it exists purely to get that one
   boolean to the backend reliably and keep the UI in sync.

## Refactor map (controller_prompts.py / controller_loop.py / controller.py / controller_phase.py)

**Clean renames, no behavior change:**
- `_PHASE_TYPES` (`controller_prompts.py`): merge `DECIDE`+`EDIT` entries into one
  `ACTIVE` entry (plus the conditional `propose_mode` addition above); delete the
  `EXPLAIN` entry.
- `_decide_state_change_correction` (`controller_loop.py`): `phase != "DECIDE"` check
  becomes `phase != "PLAN"` — same job (reject state-mutating tool calls in the
  read-only phase), phase renamed.
- Every `self._sm.phase` read used purely for logging/turn-trace/edit-entry-clearing
  bookkeeping renames with zero behavior risk.
- `PROPOSE_MODE_CORRECTION`'s valid-modes text (`controller_loop.py`) becomes
  `implement | create_task | resume` (drops `explain`, renames `edit`→`implement` to
  match the I5 decision above).

**C1 — `ControllerPhaseSM` construction + `TurnEditSession` laziness.**
Today, `TurnEditSession` is built eagerly in `_run_loop` only when `phase == "EDIT"`,
and the loop asserts `self._edit is not None` on every edit dispatch. Under the merged
model `ACTIVE` is the *default* — a plain "continue" message would run with
`edit=None` and crash on the first edit action, which is the exact scenario this
whole change targets. Fix: build the session **lazily inside the loop**, not eagerly
in `_run_loop`.
- `ControllerLoop.__init__` takes an edit-session **factory**
  (`Callable[[], TurnEditSession] | None`) instead of a constructed session.
  `controller.py` builds this closure whenever `self._orchestrator is not None`,
  regardless of phase — closures are free; the session object (and its shadow) is not.
- `ControllerLoop` keeps `self._edit: TurnEditSession | None = None` plus the factory.
  The `edit`/`run_command` dispatch sites replace `assert self._edit is not None` with
  "construct via the factory on first use if not already built."
- `close()` stays conditional on `self._edit is not None` exactly as today, so a pure
  Q&A `ACTIVE` turn that never edits never touches the shadow at all — matching
  `TurnEditSession`'s own "shadow created lazily on first edit" contract, just pushed
  one level up so the session object itself is deferred too.
- `ControllerPhaseSM.__init__` takes the starting phase explicitly:
  `def __init__(self, start: str = "ACTIVE") -> None`, validating `start in ("PLAN", "ACTIVE")`.
  The no-arg default is `ACTIVE` (see I1 below for why and the audit this requires).
  `enter_edit_mode`/`enter_explain_mode` are removed. There are exactly two legal
  `start=` values, and each has exactly one non-default construction site: `"ACTIVE"`
  for a plain fresh turn or `resolve_mode`'s `"implement"` dispatch (this C1 entry),
  and `"PLAN"` solely for `resolve_clarify`'s PLAN-raised-clarify resume (C5 below) —
  `PLAN` is never transitioned into from a live `ACTIVE` turn, only ever constructed
  fresh as a turn's starting phase. Every entry is a fresh `ControllerPhaseSM(start=...)`
  instance, never a mutation of an existing one — see NEW-I6 below for the literal
  `_run_loop` line that turns the `phase` parameter each caller passes into this
  `start=` argument.
- **`_PHASE_TYPES["ACTIVE"]`'s `edit`/`submit_changes` entries additionally depend on
  an edit-session factory being available**, not just on being in `ACTIVE`. Today this
  is safe implicitly: `EDIT` is only ever entered via `resolve_mode`'s guarded dispatch
  (`if mode == "edit" and self._orchestrator is None: raise RuntimeError(...)`, checked
  *before* any `_run_loop(phase="EDIT")` call), so a no-orchestrator test harness never
  sees an edit-capable phase. Under the merged model, `ACTIVE` is the default for
  *every* plain message regardless of orchestrator presence — a no-orchestrator harness
  would advertise `edit`/`submit_changes` as schema-legal and then crash fulfilling them
  (the factory is `None`). Fix: compute `ACTIVE`'s effective action-type set once at
  `ControllerLoop` construction, dropping `edit`/`submit_changes` when
  `edit_session_factory is None` — the same conditional-inclusion mechanism already used
  for `task_subsystem_enabled`'s `propose_mode` addition, just subtracting instead of
  adding. Real production wiring always constructs a real orchestrator (`main.py`), so
  this only matters for test harnesses that construct a `ChatController` without one.

**C1b — the merged entry-hint signal (the hardest single piece of this change).**
Today, `build_controller_step_payload` has a strict `if phase == "EDIT": ... else: # DECIDE`
binary, and each side sets a different iteration-0 signal: `plan_context["edit_entry"]`
(`EDIT` phase + no ledger items + no edit applied + not a resume — "first action after
inline-edit was chosen, nothing started yet") drives the todo-list-vs-direct-edit
guidance, while `plan_context["decide_entry"]` (`DECIDE` phase + `iteration == 0`)
drives the skill-check + "search before answering cold" guidance. Critically, **a fresh
`EDIT`-phase turn today only ever starts *after* `propose_mode`**, with `DECIDE` having
already run the skill-check on an earlier iteration of that same turn — the two hints
never both need to fire on the same iteration for the same turn. Under the merged
model, a turn starting cold in `ACTIVE` (toggle off, brand-new thread, no prior
`propose_mode`) needs **both** hints on iteration 0, since there's no earlier `PLAN`
iteration to have already run the skill-check.

Fix: replace the two separate signals with one combined flag —
```python
plan_context["active_entry"] = (
    self._sm.phase == "ACTIVE" and iteration == 0
    and not self._ledger.items and not self._edit_applied
    and not plan_context.get("edit_is_resume"))
```
(this is `edit_entry`'s exact condition, with `phase == "EDIT"` broadened to
`phase == "ACTIVE" and iteration == 0` — the `iteration == 0` clause is what makes it
also cover the cold-start case `decide_entry` used to own). The payload builder's
`else: # PLAN` branch keeps its skill-check + entry-hint text essentially as today's
`DECIDE` branch does (unchanged). The `if phase == "ACTIVE":` branch's entry-hint text
becomes the **union** of both old hints' content — skill-check first (reworded, see
next paragraph), then the todo-list-vs-direct-edit guidance — fired together whenever
`active_entry` is true.

The skill-check text's own wording needs a small edit: it currently reads
"before locating code, answering, or proposing anything" and ends with "your FIRST
action MUST be tool_call read_skill(name)... there is no other 'first action' that
outranks it" — the "proposing" reference is dead in `ACTIVE` (`propose_mode` isn't
reachable there at all, except the I4 task-subsystem accommodation, which is itself
a form of "proposing" so the word isn't entirely wrong, but the phrase should read
"before locating code, answering, or editing" to match `ACTIVE`'s actual vocabulary).

**C2 — `answer` and the todo-ledger completion gate.**
`_PHASE_TYPES["EDIT"]` today does *not* include `answer` — only `submit_changes` is
reachable, and that's already blocked while ledger items are pending/in_progress.
Merging `DECIDE`'s `answer` into `ACTIVE` introduces a **second, ungated terminal**
that could let a model mid-edit end the turn without reconciling its todo list,
undermining the finding-#7 completion guarantee. The fix is **not** to mechanically
copy the `submit_changes` gate onto `answer` — that would block ordinary Q&A the
moment *any* stale ledger exists from an unrelated prior interrupted turn (the ledger
is rehydrated across loop-boundary re-entries, so a non-empty ledger can be present
even when this turn never touches it). The correct invariant matches intent exactly:
**block `answer` only when this turn itself has applied at least one edit AND the
ledger still has pending/in-progress items** — reusing the existing `self._edit_applied`
flag (already set the moment an edit lands this turn, `controller_loop.py:677`) rather
than the raw ledger state alone:
```python
if atype == "answer":
    if self._edit_applied and self._ledger.pending():
        # append a tool_result nudging reconciliation (mirrors the submit_changes
        # block's message style) and `continue` rather than returning
        ...
    ...
```
Test coverage needs both directions: `answer` blocked when an edit landed this turn
with pending items, and *not* blocked for pure Q&A with an unrelated stale ledger and
no edit applied this turn.

**C3 — Rewritten phase-neutral system-prompt prose.**
The static `CONTROLLER_SYSTEM_PROMPT` (built once per process, cache-stable) contains
prose that assumes the old gated model and is now simply false: the `edit` variant's
"(EDIT mode only, after the user picked \"edit\")" qualifier, the `submit_changes`
variant's "(EDIT mode, when all edits are done)", the `propose_mode` variant's framing
around "the request needs a change" (implying every change routes through it), and
both `_PROPOSE_MODE_MODES_ENABLED`/`_DISABLED` blocks (which currently teach
`edit | create_task | explain` as equally-weighted options). All four need rewriting
to reflect: editing is the default and needs no permission step in `ACTIVE`;
`propose_mode` is how `PLAN` hands a concrete plan back to the user (or, when the task
subsystem is on, how `ACTIVE` offers to escalate to a reviewed task); the mode
vocabulary is `implement | create_task | resume` with `explain` gone. This is a
genuine prompt-authoring task, not a mechanical find/replace — budget real iteration
for it during implementation (matching this codebase's established practice of
verifying prompt changes against a live weak model, not just unit-testing the string).

**C4 — `PLAN`'s "you're here by choice" framing: payload hint, not system prompt.**
Phase-specific teaching lives in `build_controller_step_payload`'s per-turn
`if phase == "EDIT": ... else: # DECIDE` block (rebuilt fresh every turn into
`payload["instruction"]`) — **not** the cache-stable system prompt. Putting PLAN-only
framing in the system prompt would either leak onto `ACTIVE` turns or force per-phase
system prompts, breaking the KV-cache prefix stability this codebase already went out
of its way to establish (see the "Controller zero KV reuse" finding). The fix: add the
framing at the top of the renamed `else: # PLAN` branch, prepended on **every**
iteration of a `PLAN` turn (not just iteration 0) — a mid-turn reminder that there's
no rush is exactly where it earns its keep, and it costs nothing cache-wise since that
block is already rebuilt per-turn regardless.

**C5 — `clarify` raised in `PLAN` must resume back into `PLAN`.**
`resolve_clarify` currently special-cases only `resume_phase == "EDIT"`; anything else
(including a `PLAN`-phase clarify) falls through to `None` → the SM's default. Once
that default is `ACTIVE`, answering a clarifying question raised during `PLAN` would
silently escalate the turn into edit-capable territory without the user ever picking
`"implement"`. Fix: generalize `resume_phase` from an EDIT-only special case to
carrying the SM's actual phase at raise time (`sm.phase if sm.phase in ("PLAN", "ACTIVE") else None`,
both at the write site and the `resolve_clarify` read site), so a resume can construct
`ControllerPhaseSM(start="PLAN")` — as noted in C1, this is the only place `"PLAN"` is
ever passed as `start=` outside a turn's own toggle-derived default.
`edit_is_resume=(resume_phase == "EDIT")` at both call sites renames to
`(resume_phase == "ACTIVE")` — easy to miss since it's a derived boolean, not the
string itself.

**NEW-I6 — the literal `_run_loop` line that turns the `phase` parameter into the SM's
`start=` argument, and the caller that currently drops it on the floor.** Three
different callers feed `_run_loop`'s `phase: str | None` parameter with different
intents: `handle_message` (must become the plan_mode-derived starting phase),
`resolve_mode`'s `"implement"` dispatch (always `"ACTIVE"`), and `resolve_clarify`
(`"PLAN"`, `"ACTIVE"`, or `None` defensively). `_run_loop`'s construction becomes:
```python
sm = ControllerPhaseSM(start=phase if phase in ("PLAN", "ACTIVE") else "ACTIVE")
```
replacing today's `sm = ControllerPhaseSM(); if phase == "EDIT": sm.enter_edit_mode(); elif phase == "EXPLAIN": sm.enter_explain_mode()`.
**`handle_message` currently hardcodes `resume_phase = None` and passes that straight
through as `phase`** (with a comment explaining this was fine because clarify-resume
used to be driven entirely by `resolve_clarify`, never a plain message). That local
must be replaced by the plan_mode computation (`"PLAN" if plan_mode else "ACTIVE"`)
rather than staying a stray `None` — leaving it as `None` would silently force every
plain message into the `"ACTIVE"` default (via the fallback in the line above)
regardless of the sticky toggle's actual value, defeating the entire feature. This is
the one seam where C1, C5, and the sticky-toggle plumbing actually meet in code; an
implementer wiring the three sections above independently could easily leave
`handle_message`'s old `None` in place and never notice the toggle has no effect.

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
- A pre-deploy persisted `propose_mode` gate offering the old `edit|explain` modes
  could be resolved post-deploy against the new dispatch (`explain` branch removed,
  `edit` renamed to `implement`) — a stale in-flight gate from before the deploy would
  fail to resolve. Low-probability (gates are short-lived), worth a one-line guard
  (unrecognized legacy mode → treat as `implement`) rather than a hard error.
- Task subsystem OFF (default): `create_task`/`resume` stay stripped from the offered
  `propose_mode` options, same mechanism as today.
- Task subsystem ON: `propose_mode` becomes reachable from `ACTIVE` too, restricted to
  `{create_task, resume}` — see "Task subsystem reachability" above. This is a genuine
  addition to the phase model, not a side effect to leave undocumented.
- A `clarify` raised mid-`ACTIVE` needs `resume_phase="ACTIVE"` (renamed from
  `"EDIT"`); a `clarify` raised mid-`PLAN` needs `resume_phase="PLAN"` (new — see C5).
- Memory harness / compaction, skills-catalog injection, and the MCP/exec-session
  gates are all phase-independent today (exec/MCP are already "available in DECIDE
  and EDIT" per the existing code) — no interaction with this change; confirmed by
  tracing `prepare_turn`'s inputs (keyed on `run_id`/`history`, not phase).

## Testing

Existing phase-name-coupled tests need updating rather than rewriting from scratch,
since most of the underlying behavior they assert still holds:
- `test_controller_explain.py` — deleted (subject no longer exists); also remove the
  `explain` branch's coverage in `resolve_mode`'s dispatch tests and any seed-history
  injection test tied to it.
- **The 43 bare `ControllerPhaseSM()` construction sites across `agentd/` and
  `tests/` need a mechanical audit, not a blanket rename.** For each site, check the
  surrounding assertions for one of two shapes: **(A)** the test's point is exercising
  the *restricted* phase (asserts a rejection, checks `allowed_types()`, or otherwise
  depends on the old "bare = DECIDE" behavior) — these need an explicit
  `ControllerPhaseSM(start="PLAN")`, since a bare construction now means `ACTIVE` and
  would silently invert the test's meaning. **(B)** the SM is only there to satisfy a
  constructor argument and the test never asserts on phase-gated behavior — these are
  safe to leave as bare `ControllerPhaseSM()`. Grep each of the 43 sites' surrounding
  lines for `allowed_types()`, `pytest.raises`, or assertion text containing
  "rejected"/"not available"/`"DECIDE"` as the practical way to classify A vs B.
  `test_controller_decide_no_run_command.py` is a confirmed Shape-A example.
- New regression test pinning the default: `ControllerPhaseSM().phase == "ACTIVE"` and
  `"edit" in ControllerPhaseSM().allowed_types()` — catches an accidental future flip
  independent of the 43-site audit.
- New coverage for the actual behavioral win this change exists for: a fresh
  `ACTIVE`-phase turn (toggle off, brand-new thread, no prior `propose_mode`) can
  successfully `edit` **and** `run_command` with no round-trip — this is the concrete
  case that crashes today under C1 and is the whole point of the change.
- Both halves of the merged entry-hint fire together on `ACTIVE` iteration 0
  (skill-check + todo-list-vs-direct-edit guidance) — i.e. `plan_context["active_entry"]`
  (C1b) is true and both hint texts are present in the payload.
- `handle_message` with `plan_mode=true` actually starts the turn's SM in `PLAN` (not
  just that the field is accepted) — this is the regression test for NEW-I6's "stray
  `None`" failure mode, where the toggle is silently ignored.
- `plan_mode=true` starts a turn in `PLAN`; a `propose_mode`→`"implement"` resolution
  lands the turn in `ACTIVE` for its remainder.
- A no-orchestrator `ChatController` (test-harness construction) does not advertise
  `edit`/`submit_changes` in `ACTIVE`'s action set (M6/C1's factory-gated action set).
- C2's two directions: `answer` blocked when this-turn edit + pending items; `answer`
  allowed for pure Q&A despite an unrelated stale non-empty ledger.
- C5's `PLAN`-clarify-resume: answering a clarify raised during `PLAN` re-enters
  `PLAN`, not `ACTIVE`.
- Task subsystem ON: `propose_mode` reachable from `ACTIVE`, and its allowed-modes
  set there excludes `"implement"`.

## Out of scope

- The exact prompt wording of the merged entry-hint and the C3 system-prompt rewrite
  (drafted and iterated during implementation against a real weak model, not pinned
  verbatim here).
- Items B and E from the 2026-07-17 findings doc (different repo; unroot-caused
  runtime issue) — unrelated to this change.
