# Verify Phase Refactor — Design Spec

**Date:** 2026-05-10
**Status:** Approved

## Problem

The tool loop's verify phase is gated on `step.test_command` being non-null. When null, the
engine short-circuits immediately and returns `verified=True` with empty `test_output` — no
checks run at all. This produces silent false-positives.

Two root causes:

1. **Planner sets `test_command` based on what it has *seen*, not what it is *modifying*.**
   The planning agent reads both the source file and its companion test file during exploration.
   Current rules let it set `test_command` on a step that only touches the source file, because
   the test file appeared in a tool result. This is wrong — if the test file is not a target of
   the step, running tests mid-execution (before the test file is updated) is either vacuous or
   misleading.

2. **Short-circuit makes null mean "skip", not "discover".**
   The engine never enters the verify phase when `test_command` is null, even though the execution
   agent has live workspace access and could discover and run the right command itself.

### Observed failure (task-426b4c95)

Planning agent read `task-state.ts` and `task-state.test.ts`, wrote the correct vitest command in
`testing_strategy`, but left `test_command: null` on both steps. Both steps short-circuited to
`verified=True` with no checks run. The patch was correct by coincidence; verification was absent.

## Design

### Principle

The planning agent predicts *what* to change. The execution agent, operating with live workspace
access in the verify phase, decides *whether and how* to verify it. `test_command` is a hint, not
a gate.

---

### Change 1 — Planning prompt (`planning/prompts.py`)

**Old rule:** Set `test_command` when the test file appeared in a tool call result this session.

**New rule:** Set `test_command` only when a test file is an explicit **target** of this step
(`intent: "existing"` or `"new"`). If a step only touches source files and the test file is
updated in a separate step, leave `test_command` null.

Updated BEFORE-EMIT checklist item:

> `test_command` (if set) must point to a file listed in `targets` for this step — not merely a
> file that was read during exploration.

The "never invent a test path" guardrail is unchanged.

**Rationale:** This eliminates the split-step ambiguity. A step that modifies only `task-state.ts`
cannot run meaningful tests before `task-state.test.ts` is updated to import the new functions.
Leaving `test_command` null is correct and intentional — the execution agent will handle
verification in both steps using `testing_strategy` and live workspace knowledge.

---

### Change 2 — Tool loop short-circuit removal (`tools/loop.py`)

Remove the block:

```python
if not step.test_command:
    logger.info("No test_command — short-circuit verify, marking verified=True", ...)
    return VerifyResult(..., verified=True, test_output="", ...)
```

After a successful `emit_patch` application the loop **always** transitions to verify phase,
unconditionally.

The patch-apply context message injected into history at phase transition changes from:

```
Patch applied successfully.
VERIFY PHASE: run linters then tests.
test_command hint: {step.test_command}
...
```

To:

```
Patch applied successfully. Entering VERIFY PHASE.
Touched files: {comma-separated list of files patched this step}
testing_strategy: {step.testing_strategy or 'not specified'}
test_command hint: {step.test_command or 'none — discover from testing_strategy and touched files'}
Run linters then tests. Emit verify_done when all pass, or emit_patch to correct failures.
```

The agent now has explicit signal even when `test_command` is null.

---

### Change 3 — Tool prompt verify rules (`reasoning/tool_prompts.py`)

Remove line:

```
- If this step has no test_command hint, emit verify_done(verified=true) immediately
```

Replace the verify phase rules block with:

```
Rules:
- Use testing_strategy and touched files to determine what to run
- Run static analysis first (fast): ruff, mypy, tsc --noEmit, cargo check
- Then run tests: pytest, vitest, cargo test, npm test
- If test_command hint is provided, prefer it — but verify the binary exists first
- If test_command is null, infer from testing_strategy and touched file extensions
- If nothing is testable (pure docs/config change), emit verify_done(verified=true) immediately
- Never claim verified=true without actually running at least one check
- Use find_binary / setup_env if a binary is missing
```

The agent retains an explicit escape hatch for genuinely non-testable steps (`.md`, `.yaml`,
`.toml`, `.json` with no build impact) but the decision is agent-driven, not a null-check gate.

---

## Files Changed

| File | Change |
|------|--------|
| `services/agentd-py/agentd/planning/prompts.py` | Tighten `test_command` rule in TEST DISCOVERY block and BEFORE-EMIT checklist |
| `services/agentd-py/agentd/tools/loop.py` | Remove short-circuit block; enrich patch-apply context message |
| `services/agentd-py/agentd/reasoning/tool_prompts.py` | Remove null=skip rule; replace verify rules block |

Domain model (`domain/models.py`) `PlanStep.test_command` docstring updated to reflect new rule
(only when test file is a target, not merely seen).

## What Does Not Change

- `max_verify_calls_per_step` budget (already 8 — sufficient for find_binary + setup_env +
  linter + test runner)
- `verify_done` guards (phase==explore guard, last_verify_run_errored guard) — both stay
- `VerifyResult` / `PlanHandoff` return types — unchanged
- Full-task validation (VALIDATING phase) — unchanged; this refactor only affects per-step verify

## Success Criteria

1. A step that touches only a source file (no test file as target) leaves `test_command: null`
   in the plan — and still enters the verify phase.
2. The verify-phase agent runs the correct test command using `testing_strategy` as guidance,
   discovering binaries via `find_binary` / `setup_env` when needed.
3. `test_output` in `VerifyResult` is never empty for code-touching steps.
4. Steps with only non-code targets (docs, config) still exit verify quickly via agent-emitted
   `verify_done(verified=true)` without running any commands.
