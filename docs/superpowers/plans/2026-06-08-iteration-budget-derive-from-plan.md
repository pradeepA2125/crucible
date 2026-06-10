# Iteration Budget: Derive From Plan (kill the "bound to fail" default)

> **For agentic workers:** Execute task-by-task with TDD. Steps use checkbox (`- [ ]`).

**Goal:** Stop tasks failing with `RuntimeError("Iteration budget exceeded")` when a plan
has more steps than the constant `max_iterations` default. Make the task-level iteration
budget track the actual plan size instead of a fixed `6`.

**Origin:** A resumed task (`task-5d227580-…`) failed with "Iteration budget exceeded"
even though no per-step budget was exhausted. Root cause below.

---

## Root cause

- `TaskBudget.max_iterations` defaults to **6** (`domain/models.py:61`) — a constant set
  *before* the plan exists.
- It is **task-level**, not per-step. `usage.iterations` increments **once per step-attempt**
  (`engine.py:2013–2014`, inside `_run_step_with_retries`), shared across the whole plan.
- `assert_budget` raises `RuntimeError("Iteration budget exceeded")` once
  `usage.iterations > max_iterations` (`domain/state_machine.py:103`).
- So any plan with > ~6 step-attempts (i.e. most multi-step plans, especially with retries)
  is **bound to fail** before the work can complete.

## Decision (why no gate)

A "bump the budget?" gate was considered and **rejected**: once the budget is derived from
the plan, the per-step cap `max_attempts_per_step` (3) already bounds total attempts to
`len(steps) × max_attempts_per_step` — i.e. the derived budget becomes unreachable in normal
execution, so a gate would never legitimately fire. The only escape is delta replan growing
the step set, which we handle by **recomputing the budget on replan**, not a gate. A task that
genuinely exhausts every step's attempts is *stuck* — failing there (with a clear message) is
correct, not a "grant more iterations" situation.

---

## Tasks

### Task 1: Derive `max_iterations` from the finalized plan

**Files:** `agentd/orchestrator/engine.py` (where the executable JSON plan is set, post-approval
in `continue_task` / start of `_execute_plan`); Test `tests/test_iteration_budget_derive.py`

- [ ] **Failing test:** a task whose approved plan has N steps gets
      `budget.max_iterations >= N * max_attempts_per_step` (not the raw default 6).
- [ ] **Implement:** when the executable plan is finalized, set
      `task.budget.max_iterations = max(task.budget.max_iterations, len(plan.steps) * self._max_attempts_per_step)`.
      (Floor at the existing default so an explicit higher override is never lowered.)
      Note: `max_attempts_per_step` is an orchestrator field (default 3), not on `TaskBudget`.
- [ ] **PASS**, commit `fix(budget): derive max_iterations from plan step count`.

### Task 2: Recompute on delta replan

**Files:** `agentd/orchestrator/engine.py` (`_apply_revision`); Test same file

- [ ] **Failing test:** after a delta replan that adds/revises steps, `max_iterations` is
      topped up to cover the new step count (never lowered).
- [ ] **Implement:** in `_apply_revision`, after the step set changes, re-apply the Task-1
      derivation against the new `len(plan.steps)`.
- [ ] **PASS**, commit `fix(budget): top up max_iterations when delta replan changes the plan`.

### Task 3: Replace the bare RuntimeError with an actionable failure

**Files:** `agentd/domain/state_machine.py` and/or `engine.py`; Test same file

- [ ] **Failing test:** the genuinely-stuck case (every step exhausted `max_attempts_per_step`)
      still fails, but the diagnostic names the step and the attempt counts (not just
      "Iteration budget exceeded").
- [ ] **Implement:** keep the hard stop (no gate) but raise/transition with a message like
      `"Step <id> exhausted all attempts (used <n>/<max> total step-attempts)"`.
- [ ] **PASS**, commit `fix(budget): actionable message when a plan is genuinely stuck`.

### Task 4 (optional, follow-up): consider removing the global iteration budget

`max_attempts_per_step × steps` + `max_delta_replans` already bound total work, so
`max_iterations` is arguably redundant. Park this as a separate decision — keeping it as a
derived value (Tasks 1–2) is the smaller, safer change.

- [ ] Decide whether to drop `max_iterations` / `usage.iterations` entirely, or keep derived.

---

## Verification
- [ ] Re-run the original failing scenario (multi-step plan > 6 steps) → completes instead of
      "Iteration budget exceeded".
- [ ] Existing budget tests (`assert_budget`, token/runtime limits) still pass — this change is
      iteration-only; `max_tokens` / `max_runtime_ms` stay as hard limits.
