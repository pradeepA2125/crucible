# Live smoke — controller `progress` action — 2026-07-30

Acceptance gate for the non-terminal `progress` action (plan Task 8; resolves spec Open Item A in
`docs/superpowers/specs/2026-07-25-controller-progress-action-design.md`).

**Setup:** TurboQuant `qwen3.6:35b-a3b-q4_K_M`, `CRUCIBLE_CHAT_CONTROLLER=1`,
`CRUCIBLE_SKILLS_ENABLED=1`, 14 superpowers skills installed, scratch workspace
`workspaces/progress-smoke`, backend at HEAD `2e982c9` (all final-review fixes applied).

## Control run first — task size is the variable that matters

A small prompt ("create `calc.py` with add/multiply, then `test_calc.py`, then run the tests")
produced **zero** `chat_progress`. The model simply did the work: `tool_call → tool_call → edit →
edit → tool_call`. No narration pressure exists at that size, so a small task cannot exercise this
feature. This matched the human partner's prediction and is why the real run below is large and
skill-loaded (the shape where the friction was originally observed on a Kafka-clone build).

## Phase 1 — brainstorm

Prompt: build a Kafka-style commit log library (segmented log, offset index, producer/consumer
with group offset tracking, full pytest suite), explicitly multi-phase.

Action sequence from `agentd.log`:

```
iter0  tool_call (read_skill → brainstorming)
iter1  progress   ACCEPTED
iter2  progress   REJECTED (repeat guard 1/3)
iter3  progress   REJECTED (repeat guard 2/3)
iter4  tool_call (list_directory)     ← correction steered it back to acting
iter5  clarify
```

The accepted note:

> Starting the brainstorming process. First, exploring the existing workspace context to
> understand what we're working with.

The turn ended on a legitimate clarify gate — `brainstorming`'s own checklist says explore context,
then ask clarifying questions one at a time. The model asked *"What's the primary use case for this
commit log library?"* with four sensible options. Correct behavior, not a failure.

## Phase 2 — implementation (clarify answered: "message broker component, build it phase by phase")

```
progress → tool_call ×4 → edit APPLIED (commitlog/__init__.py, commitlog/core.py)
```

Real files landed on disk. `progress` fired again in the implementation phase, and the turn
continued through it into actual edits.

## Findings

1. **`progress` fires on a real weak model, and it is genuinely non-terminal.** The turn continued
   through iterations 2-5 after the note in phase 1, and through four tool calls into an applied
   edit in phase 2. This is the feature's core promise, confirmed live.

2. **The repeat guard is load-bearing, not theoretical.** The model attempted three consecutive
   `progress` responses on the very first real turn. The cap rejected #2 and #3, and the correction
   text (*"A progress note does NOT count as doing the work. Take the actual next action now"*)
   successfully redirected it to `list_directory`. Without this guard `progress` would have become
   a narration attractor immediately — vindicating the decision to make the guardrails mechanical
   rather than prompt-only.

3. **`answer` was never misused for narration.** The original failure this feature exists to fix —
   a terminal `answer` mid-execution describing work not yet done — did not reproduce in either
   phase.

4. **Transcript ordering fix verified against the real database**, not just tests. Persisted
   `messages_json` read back as: `[0] user, [1] pills (sealed), [2] progress note, [3][4] later
   pills`. The note sits chronologically *between* pill segments. Pre-fix it would have been dumped
   after the final answer (final-review Important 1).

5. **The reserved-tool-name guard fired live, on `edit`.** At iters 3 and 4 of phase 2 the model
   emitted `{"type":"tool_call","tool":"edit",...}` and received the targeted correction
   (*"'edit' is not a callable tool … 'edit' is a top-level response TYPE"*), then self-corrected
   and emitted a proper `edit` at iter 5. This is direct evidence that the model **does** generalize
   `tool_call` onto top-level action types, which is exactly why final-review Finding 3 (adding
   `progress` to `_RESERVED_ACTION_TOOL_NAMES`) was necessary rather than speculative.

## Unrelated issue found during the run (pre-existing, NOT caused by this feature)

The backend wedged mid-run: `/health` did not answer within 60s while the process was alive and the
controller loop was still advancing (~2m20s per TQP call). It required `kill -9`.

This has a user-visible consequence worth fixing separately: `ChatController.openChat` calls
`getConfig()` first and, on failure, routes to `promptSetup()` and **returns without opening
anything**. So an unreachable or wedged backend presents to the user as "the chat command does
nothing" — no error, no toast, no signal. Reproduced in a VS Code extension development host.

## Verdict

The mechanism works end to end on a real, deliberately weak model, and both mechanical guardrails
were observed firing and correcting behavior. Not yet exercised: a long multi-phase run to
completion (the run was cut short to install the build), so how *frequently* the model narrates
across a full build remains unmeasured.
