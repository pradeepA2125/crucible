# Tool Loop Guards — Original Scenarios & Impact of Static Auto-Checks

> Context: `services/agentd-py/agentd/tools/loop.py`
> Auto-check change: `ruff` output is now **advisory** (labelled "do not patch-loop to fix style").
> `py_compile` and `mypy` remain **blocking** ("must fix before verify_done").

---

## Guard 1 — verify_done in explore phase

**Location:** `~line 235`

**Original scenario:**
Model emits `verify_done` before applying any patch — i.e., it reads the code, decides nothing needs changing, and skips straight to "done." This is almost always wrong: the task was submitted because a change was needed.

**Behaviour:**
Push back with a message explaining that at least a no-op patch is required to enter verify phase.

**Impact of static checks:**
**None.** Static checks only fire after a patch is applied. Guard 1 fires before any patch exists. Still fully needed.

---

## Guard 2 — verified=True after failing run_command

**Location:** `~line 256`

**Original scenario:**
Model runs a test command (pytest, tsc, etc.), it exits non-zero, and the model still emits `verify_done(verified=True)`. Classic "declare victory despite evidence of failure."

**Behaviour:**
Push back: "last run_command failed, fix it before claiming verified."

**Impact of static checks:**
**Minimal.** Static checks fire automatically and are already injected into history, so the model sees blocking failures without needing to claim verified. Guard 2 still protects the specific case where a test command explicitly failed and the model ignores it. Remains needed. No change required.

---

## Guard 3 — verified=True with no passing run_command since last patch

**Location:** `~line 277`

**Original scenario (pre-static-checks):**
Model patches a file and immediately emits `verify_done(verified=True)` without running anything. Without any feedback mechanism, this was the only way to force the model to actually execute verification.

**Behaviour:**
Escalating push-back (3 violations → auto-fail). Requires at least one passing `run_command` after each patch before `verified=True` is accepted.

**Impact of static checks — most affected guard.**

Static checks now run automatically after every patch and inject `py_compile` / `mypy` results directly into history. The model sees real pass/fail feedback without needing to call `run_command`. This means:

- If `py_compile` and `mypy` are clean → the model has legitimate evidence to declare `verified=True` even without running a test command
- If there is no `test_command` for the step (e.g. domain model changes without a coupled test file), Guard 3 forces the model to run *something* — but it often has nothing valid to run, leading to wrong-path failures and the infinite cycle seen in the task-5194c8dcdcd4 log

**Problem Guard 3 now causes:**
The log shows the exact failure mode: model adds `PAUSED` to `TaskStatus` correctly (iter=4), auto-check shows only ruff advisory issues, model wants to declare done, Guard 3 fires, model runs `pytest tests/test_domain.py` (doesn't exist → exit code 4), counter resets (Bug 1 — see Guard 4), cycle repeats.

**Recommended change:**
Guard 3 should be bypassed when the last auto-check result had **no blocking failures** (`py_compile` ✓, `mypy` ✓). Track a `last_blocking_check_passed: bool` flag alongside `verify_passed_after_last_patch`. Either condition should satisfy the guard:

```python
if verified and not verify_passed_after_last_patch and not last_blocking_check_passed:
    # fire Guard 3
```

This lets the model declare done via static checks alone when there is no valid test to run.

---

## Guard 4 — verify-phase patch loop (no run_command between patches)

**Location:** `~line 359`

**Original scenario (pre-static-checks):**
Model enters verify phase, applies a patch, applies another patch, applies a third — with no `run_command` in between. In the old world without any feedback, this was pure blind patching: the model had no information about whether earlier patches worked, so it was likely thrashing the same change repeatedly.

Two triggers:
- **(a)** 3+ successful patches without `run_command`
- **(b)** 3+ patch attempts without `read_file`/`search_code` (carpet-bombing from memory)

**Behaviour:**
Block the patch with a push-back message. Reset only when `run_command` is called.

**Impact of static checks — second most affected guard.**

Static checks changed the meaning of "patching in verify phase":

- **Before static checks:** patches in verify phase had no automatic feedback → patching without run_command = blind
- **After static checks:** every successful patch fires auto-checks → the model *has* feedback after each patch; iterating to fix blocking `mypy` errors is **legitimate and informed behaviour**

**The cycle this guard now creates (confirmed in logs):**
1. Auto-check reports blocking mypy/py_compile errors
2. Model patches to fix them (informed, correct behaviour)
3. Guard 4 fires after 3 patches — "no run_command between patches"
4. Model calls `pytest tests/nonexistent.py` → exits code 4 (file not found) → `is_error=True`
5. **Bug:** counter resets on ANY `run_command` including failed ones → 3 more patches allowed
6. Repeat from step 2 indefinitely (iters 14–28 in the log: identical thought + 709 tokens each time)

**Two concrete bugs exposed:**

*Bug 1 — reset on failed run_command:*
`verify_patches_without_run` resets at line 715 regardless of `tool_output.is_error`. Should only reset on a passing command.

*Bug 2 — no escape hatch for consecutive blocks:*
When Guard 4 blocks the same patch attempt N times in a row (identical response), the model enters a frozen loop consuming the full iteration budget. No counter exists to abort after repeated blocks.

**Recommended changes:**
1. Reset `verify_patches_without_run` only on `not tool_output.is_error`
2. Add `guard4_consecutive_blocks` counter; abort step (raise `ToolBudgetExceededError`) after 3 consecutive Guard 4 triggers with no corrective action
3. Consider raising the threshold from 3 to 5 patches, since auto-checks mean each patch is informed

---

## Guard 5 — duplicate tool call dedup

**Location:** `~line 573`

**Original scenario:**
Model calls `read_file("engine.py", start_line=1, end_line=100)` at iteration 2, then calls the exact same thing at iteration 7. The second call returns identical output — the model is stuck in a context loop, re-reading the same file section without making progress.

**Behaviour:**
Block the duplicate call, inject a message listing alternative actions (read a different range, emit_patch if context is sufficient).

**Impact of static checks:**
**None.** Dedup is purely about tool call patterns, independent of what auto-checks report. Still fully needed — static checks give the model more *information* but don't prevent repetitive tool calls when the model is confused about what it's seen.

One note: `_seen_calls` is correctly cleared when a patch is applied successfully (line 526 — `_seen_calls = {}`). This means the model can legitimately re-read a file in verify phase to inspect what the patch actually produced (shadow reads). This interaction is correct.

---

## Summary table

| Guard | Original problem | Static checks change | Action needed |
|-------|-----------------|---------------------|---------------|
| 1 | verify_done before any patch | None | None |
| 2 | verified=True after failing run_command | None | None |
| 3 | verified=True with no passing run_command | **Breaks valid flows** — blocking auto-check pass should be sufficient | Add `last_blocking_check_passed` bypass |
| 4 | Blind patch loop with no run_command | **Causes infinite loops** — informed patching to fix blocking errors is now legitimate | Fix reset-on-failed-run-command bug; add consecutive-block abort |
| 5 | Duplicate tool calls | None | None |
