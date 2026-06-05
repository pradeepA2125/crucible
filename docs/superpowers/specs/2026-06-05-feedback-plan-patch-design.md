# Feedback Plan-Patch — Design

**Date:** 2026-06-05
**Branch:** `feat/agentic-planning-delta-replan`
**Status:** Design (pre-implementation)

## Motivation

When the user gives feedback on a plan, the feedback round currently re-emits the
**entire** `plan_markdown` via `emit_plan` (~3–4k output tokens). On weak local models
(qwen3.6 / TurboQuant) this is the single biggest reliability failure: at the large
prompt sizes feedback rounds reach (~65–70k tokens), emitting a full ~4k-token plan
degrades. Observed failure modes, all at the emit step:

- `emit_plan` with **empty `plan_markdown`** → fatal `PlanningBudgetExceededError` (round 3 original).
- **`type=''`** malformed response (caught by `_MAX_MALFORMED` retry).
- **`emit_plan` wrapped as a `tool_call`** (`tool="emit_plan"`) → "unknown tool" → model wanders.

The model *knows the content* — it mangles the *envelope* because the output is huge.
The fix is to make the feedback response **small**: emit a targeted patch against the
existing plan instead of rewriting it. Output shrinks from ~4k tokens to a few hundred,
which collapses both the decode cost (~5 min → ~20s) and the degradation surface.

This pairs with the existing **conversation-history replay** (feedback rounds already
replay the prior planning conversation, keeping the KV prefix warm): replay makes the
*input* cheap, plan-patch makes the *output* cheap.

## Goals

- A feedback round can emit a **small, targeted patch** to the current plan rather than a full re-emit.
- Cost proportional to the **actual change**, not to step or plan size (5 tiny edits ≈ 5 tiny ops).
- **No new patch grammar** — reuse the execution phase's `search_replace` op and `PatchEngine`.
- **No position/line-number dependence** — avoids weak-model line arithmetic, number staleness, and the KV-cache hazards of embedding/refreshing line-numbered plans.
- Preserve the KV-cache prefix discipline (append-only; nothing before `conversation_history` changes per turn).
- Full `emit_plan` remains available; the model chooses by change scale.

## Non-goals

- Changing the **first** plan emit (it stays a full `emit_plan` — it happens at low context, where degradation isn't the problem).
- Patching the JSON `PlanDocument` (this is about the **markdown** plan at the approval gate; the markdown→JSON step is unchanged).
- The delta-replan / `emit_revision` execution path (separate; unchanged).

## Chosen op: `search_replace`

Execution-phase ops available: `search_replace`, `replace_range`, `apply_diff`,
`create_file`, `delete_file` (+ node ops for code). We use **`search_replace`**.

Engine semantics (`PatchEngine._apply_search_replace`, `patch/engine.py:424`):
- `search` text must **exist** and be **unique** (`>1` occurrence → hard error "must be unique"; `0` → "not found"). Replaces the single occurrence.
- Pure text; **no line numbers**.

Why this op over the alternatives we considered:

| Option | Verdict |
|--------|---------|
| Full `emit_plan` re-emit | The thing that degrades. Keep only as the large-rewrite escape hatch. |
| `replace_range` (line-based) | Needs accurate line numbers → weak-model arithmetic; multi-op line shifts; numbers go stale after patches; forces embedding/refreshing a line-numbered plan (KV hazard). Rejected. |
| `replace_step` (whole-step, header-anchored) | Right anchor model, wrong granularity: scattered tiny edits across N steps rewrite N whole steps. Rejected. |
| **`search_replace`** | **Cost ∝ change; no line numbers; content-anchored; engine-enforced uniqueness; already an execution op the model emits competently.** Chosen. |

Properties that make it safe:
- **Engine-enforced uniqueness** → an ambiguous match is a *clear, catchable error*, never a silent wrong-edit.
- **Order-independent** across ops (each validated against current file state) except the rare case where one op's `replace` clobbers another's `search` — handled by the same non-fatal correction path.
- **DRY** — reuses the op + `PatchEngine` the model already uses for code.

## Design

### New action type: `emit_plan_patch`

Added to `PLANNING_STEP_RESPONSE_SCHEMA` (`planning/prompts.py`), available **only on
feedback rounds** (gate the `type` enum so the first plan can't choose it):

```json
{
  "type": "emit_plan_patch",
  "thought": "...",
  "ops": [
    {"op": "search_replace", "search": "<exact unique snippet>", "replace": "<new text>", "reason": "..."}
  ]
}
```

The model chooses `emit_plan_patch` (small/scattered edits) vs `emit_plan` (large
rewrite) by scale — a prompt instruction, not a hard gate.

### The plan as a shadow file

- On entering a feedback round, the backend writes the current `plan_markdown` to
  **`<shadow_root>/plan.md`** (shadow root, **not** artifacts — artifacts may be
  outside the agent's readable scope; shadow root is where `read_file`/`PatchEngine` operate).
- `emit_plan_patch.ops` are applied by `PatchEngine` to `plan.md`.
- The patched file is read back and becomes the new `plan_markdown` (stored, shown, snapshotted).

### Getting the current plan to the model (no line numbers, no re-read)

The model needs the **current** plan text to copy exact, unique `search` snippets from.
Provided **append-only**:

- **Round 2** (first feedback): the prior plan is already in the replayed
  `conversation_history` (the `emit_plan` turn). The model copies `search` snippets
  from it. Plan text stored there is **clean** (byte-identical to `plan.md`) — no
  line-numbering transform.
- **Round 3+**: after a prior patch, `plan.md` is now v2. The **appended feedback turn
  carries the current (v2) clean plan**, rendered by the backend from the live
  `plan.md`. The model copies `search` snippets from the current version.

This is the resolution to the KV-cache hazard discussed in design: we **never mutate an
earlier history entry** to refresh the plan (that would re-prefill the whole history).
The current plan enters only via **new appended turns**, so the prefix up to the prior
tail stays byte-identical and the cache stays warm. Because `search_replace` is
content-anchored, **no line numbers** appear anywhere — eliminating the staleness and
arithmetic problems entirely.

**Invariant:** the plan text the model sees in context == `plan.md` content, byte for
byte, so a copied `search` snippet always matches the file.

### Per-round flow

```
Feedback received (continue_task, feedback branch)
  ├─ write current plan_markdown → <shadow_root>/plan.md
  ├─ seed planning loop with replayed conversation_history + appended feedback turn
  │     (feedback turn embeds the current clean plan on round 3+)
  ├─ model emits:
  │     emit_plan_patch{ops:[search_replace...]}   (small edits)   OR
  │     emit_plan{plan_markdown}                    (large rewrite)
  ├─ if emit_plan_patch:
  │     ├─ apply ops via PatchEngine._apply_search_replace on plan.md
  │     ├─ on apply error (not-found / not-unique):
  │     │     inject the engine's error message as a correction, CONTINUE the loop
  │     │     (non-fatal; model adds context to disambiguate, or switches to emit_plan)
  │     └─ on success: read plan.md back → new plan_markdown; emit diff event
  └─ pause at AWAITING_PLAN_APPROVAL with the updated plan (+ diff shown)
```

### Failure handling

- **No fallback ladder.** `emit_plan` is always a legal choice, so the model self-selects
  it for large changes; no automatic "after N patch fails, force emit_plan" mechanism.
- **Apply failures are non-fatal.** A failed `search_replace` (not found / not unique)
  injects the engine's clear message into history and continues the loop — the same
  error-injection the loop already does for tool errors. This explicitly avoids
  reintroducing the fatal crash class (round 3's empty-`plan_markdown`).

## KV-cache invariants (must hold)

1. **Append-only history.** The current plan reaches the model only via newly appended
   turns (the `emit_plan` turn already in history, or the appended feedback turn). Never
   rewrite an earlier entry to refresh the plan.
2. **No per-turn-varying field before `conversation_history`.** Unchanged from the
   existing discipline (`build_planning_step_payload`: `budget_status` last, etc.).
3. **Clean plan == `plan.md`.** No line-numbering or other transform that could diverge
   the in-context plan from the file being patched.
4. **Gated schema stays trailing.** `emit_plan_patch` is gated by mutating the response
   schema's `type` enum per round (`planning_response_schema(allow_plan_patch=...)`). On
   TurboQuant this is KV-safe **only** because the schema is appended at the END of the
   user content (after `conversation_history`) — changing it re-prefills just the trailing
   schema, not the history. Keep it trailing, and constant **within** a round. Provider
   notes: Gemini/OpenAI pass the schema as a request param (unaffected); Anthropic
   stringifies it into the system prompt, so per-round gating would break that provider's
   cache — there, gate `emit_plan_patch` via the prompt with a constant schema instead.

## Interaction with existing systems

- **Conversation-history replay** (this branch): the feedback round already replays the
  prior planning conversation and pins `initial_context`. Plan-patch is the output-side
  complement. The `emit_plan` history turn (appended on emit) is the round-2 source of
  the plan text.
- **Retrieval-skip gate** (this branch): unchanged — feedback rounds with a pinned
  context still skip `load_context`.
- **`emit_revision`** (delta-replan): conceptually the precedent (step-keyed plan edits),
  but operates on the JSON `PlanDocument` in the execution path — left untouched.
- **`PatchEngine`**: reused as-is; `plan.md` is just another file under the shadow root.

## Edge cases

- **Ambiguous `search`** (>1 match): engine errors → non-fatal correction → model adds
  surrounding context.
- **`search` not found** (model copied stale/garbled text): engine errors → correction;
  on round 3+ the current plan is in the feedback turn, reducing this.
- **Overlapping ops** (one op's `replace` removes another's `search`): later op errors →
  correction. Document that ops should target disjoint regions.
- **Adding a whole new step**: expressed as a `search_replace` that finds the end of an
  adjacent step and appends the new step (no separate "add" op needed for v1).
- **Empty `ops`**: treat as malformed → correction.

## Test plan

- `PatchEngine._apply_search_replace` on a markdown `plan.md`: single op, multiple
  disjoint ops, not-found error, not-unique error. (Reuses existing engine tests'
  patterns.)
- Planning loop: `emit_plan_patch` parsed and applied; resulting `plan_markdown` reflects
  the ops; apply error injects a correction and the loop continues (non-fatal).
- Schema gating: `emit_plan_patch` absent from the first-plan `type` enum, present on
  feedback rounds.
- Engine/integration: a feedback round emits `emit_plan_patch`, `plan.md` is written and
  patched, the task returns to `AWAITING_PLAN_APPROVAL` with the updated plan; a diff
  event is broadcast.
- KV invariant: the feedback-turn plan render is append-only; no earlier history entry is
  mutated across two feedback rounds (assert prefix stability, mirroring
  `test_plan_feedback_history.py`).

## Open decisions (resolved)

- **Op:** `search_replace` (not `replace_range`, not whole-step). ✔
- **Plan file location:** shadow root. ✔
- **Line numbers:** none. ✔
- **Choice of patch vs full emit:** model decides by scale (prompt), `emit_plan_patch`
  gated to feedback rounds. ✔
- **Fallback ladder:** none; apply failures non-fatal (inject + continue). ✔
- **Current-plan delivery:** append-only (emit turn / feedback turn), clean text. ✔

## Out of scope

- First-plan emit, JSON `PlanDocument` patching, delta-replan/`emit_revision`,
  VS Code diff-card UX for the plan diff (backend emits the diff event; client rendering
  is a separate change).
```
