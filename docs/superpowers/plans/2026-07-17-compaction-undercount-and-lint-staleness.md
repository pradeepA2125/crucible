# Compaction Token-Undercount and Lint-Claim Staleness Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two findings from the 2026-07-17 chat-controller live-smoke session
(`docs/superpowers/2026-07-17-chat-controller-live-smoke-findings.md`, open items A and C): the
memory compactor's token estimator undercounts real tokens badly enough that compaction fires too
late to prevent context overflow, and the `submit_changes` gate lets a stale "lint check: done"
todo item stand even after later edits introduced new lint errors.

**Architecture:** Both fixes are small, targeted changes in `services/agentd-py/agentd/chat/` and
`agentd/memory/` — no new dependencies, no data-model migrations, no schema changes. Task 1
corrects the token-estimation ratio in the memory compactor using real numbers captured live this
session. Task 2 adds a final mandatory re-verification requirement at the `submit_changes` gate,
mirroring the wording style of the existing TDD-gap fix already shipped this session.

**Tech Stack:** Python 3.13, pytest, pytest-asyncio (existing stack — no additions).

## Global Constraints

- No new third-party dependencies (real BPE tokenizer integration is explicitly out of scope for
  this plan — see Task 1's rationale for why a corrected heuristic is preferred here).
- Every step's test must actually run (`pytest`) before being marked done — this plan practices
  what it's fixing.
- Mirror existing code style exactly: the reserved-tool-name guard (`controller_loop.py`), the
  dedup-clear fix, and the TDD-gap prompt fix (`controller_prompts.py`) are the reference patterns
  for how this codebase phrases model-facing corrections and code comments.
- Both tasks are independent — either can be implemented and committed on its own.

---

### Task 1: Correct the memory compactor's token-undercount

**Files:**
- Modify: `services/agentd-py/agentd/memory/compactor.py:17-18` (`estimate_tokens`)
- Test: `services/agentd-py/tests/test_memory_compactor.py:48-50` (existing test asserts the
  buggy behavior by name — must be updated, not just added to)

**Interfaces:**
- Consumes: nothing new — `estimate_tokens(text: str) -> int` keeps its exact signature; every
  call site (`_history_tokens`, `_select_hot`, the token count in `_truncate_to_tokens` via
  `max_tokens`) is unaffected by an internal ratio change.
- Produces: same `estimate_tokens` signature, just a more accurate constant.

**Evidence backing this fix** (captured live during the 2026-07-17 session, not guessed): at the
turn where the backend crashed with `request (98577 tokens) exceeds the available context size
(98304 tokens)`, TurboQuant's own KV cache was logged at `cache_n=98244` (the real, measured token
count). The controller-turn debug artifact captured at essentially the same point
(`controller-turn-41.json`) shows `conversation_history` at 236,469 chars. The current estimator
(`len(text) // 4`) computes that as ~59,117 estimated tokens — a **1.66x undercount** versus the
real 98,244. This means the compaction trigger (`_history_tokens(history) < window_tokens *
trigger_frac`) doesn't fire until the *real* token count is already at the hard ceiling, leaving
no safety margin for the rest of that iteration's growth.

- [ ] **Step 1: Write the failing test**

Replace the existing test that names and asserts the buggy `//4` ratio:

```python
# tests/test_memory_compactor.py — replace test_estimate_tokens_charsdiv4 with:
def test_estimate_tokens_uses_conservative_ratio():
    """Regression test for the 2026-07-17 undercount finding: real TurboQuant KV-cache
    tokens (98244) vs the len//4 estimate for equivalent-sized content (~59117) showed a
    1.66x undercount, which let compaction's trigger fire too late to prevent context
    overflow. len//3 is still an approximation but cuts the undercount roughly in half
    without needing a real tokenizer dependency."""
    # 300 chars of representative code/JSON-ish content (the kind that dominates this
    # conversation's history — tool_result payloads, patch_ops, ruff/pytest output).
    text = '{"op": "search_replace", "file": "core/x.py", "search": "a", "replace": "b"}' * 4
    assert len(text) == 304
    # len//4 (the old ratio) would give 76 — the fix must NOT be the old ratio.
    assert estimate_tokens(text) != len(text) // 4
    # len//3 is the new floor (101).
    assert estimate_tokens(text) == len(text) // 3


def test_estimate_tokens_minimum_is_one():
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_compactor.py::test_estimate_tokens_uses_conservative_ratio -v`
Expected: FAIL — `assert 76 != 76` fails (the old `len//4` ratio is still in place, so
`estimate_tokens(text)` returns 76, and the "must not equal old ratio" assertion fails since
`76 != 76` is false).

- [ ] **Step 3: Write minimal implementation**

```python
# agentd/memory/compactor.py — replace lines 17-18
def estimate_tokens(text: str) -> int:
    # len//3, not len//4: a 2026-07-17 live-smoke session measured a real TurboQuant
    # KV-cache token count (cache_n=98244) against the len//4 estimate for equivalent
    # conversation_history content (~59117) — a 1.66x undercount that let compaction's
    # trigger fire too late to prevent a hard context-size overflow. len//3 halves the
    # undercount without adding a real-tokenizer dependency; if this proves insufficient
    # in practice, the next escalation is a real BPE-based estimate (e.g. tiktoken as an
    # approximation even for non-OpenAI models), not a further ratio tweak.
    return max(1, len(text) // 3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_compactor.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones exercising
`_history_tokens`/`_select_hot`/`maybe_compact` — none of them hardcode the `//4` ratio elsewhere,
so they should pass unchanged against the new constant. If any DO hardcode `//4`-derived expected
values, update those expected values to match `//3` in this same step — do not leave the suite red.)

- [ ] **Step 5: Run the full memory test suite**

Run: `pytest tests/ -k memory -v`
Expected: PASS, no regressions (was 96 passed at end of the 2026-07-17 session before this fix).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/memory/compactor.py services/agentd-py/tests/test_memory_compactor.py
git commit -m "fix(memory): correct token-estimate undercount that delayed compaction

Real TurboQuant KV-cache count (98244) vs the len//4 estimate for
equivalent conversation_history content (~59117) showed a 1.66x
undercount, letting compaction's trigger fire too late to prevent a
hard context-size overflow. Switch to len//3."
```

---

### Task 2: Require a fresh verification run before `submit_changes`, not a stale "done" claim

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (the `submit_changes` variant
  teaching block, and the `TODO LIST POLICY` section added by the 2026-07-17 TDD-gap fix)
- Test: `services/agentd-py/tests/test_controller_loop_submit_requires_fresh_verify.py` (new file)

**Interfaces:**
- Consumes: the existing `submit_changes` gate in `controller_loop.py` (the `still_open` check
  around line 728) — this task does NOT change that gate's Python logic, only the prompt text
  feeding into it, per the finding's own conclusion that a full staleness-tracking data-model
  change (timestamps on `TodoItem`, tracking "edits since this item was marked done") is more than
  this specific problem needs. If live behavior after this fix shows the prompt-only approach
  doesn't hold (mirroring the caveat already written for the narrate-without-act fix), the
  escalation path is a real `submit_changes`-time Python check, not a further prompt tweak.
- Produces: no new function signatures — this is a prompt-text-only change, verified via the
  existing `ScriptedReasoningEngine` test harness pattern (asserting the rendered prompt contains
  the new rule, mirroring how `test_controller_reserved_tool_name.py` and
  `test_controller_loop_dedup_clears_on_edit.py` verify their fixes).

**Why this is scoped as a prompt fix, not a data-model change**: `TodoItem` (`todo_ledger.py:21-25`)
has only `title`/`status`/`note` — no timestamp, no "edits since done" counter. Building real
staleness detection would mean adding that field, threading it through `write_todos`'s tool
schema, and the reconcile-checkpoint logic — a much larger change for a problem the live session
only observed once. The minimal fix: make "run the verification one final time, right before
`submit_changes`" an unconditional rule, regardless of any earlier `done` claim's staleness —
matching ordinary engineering practice (re-run checks right before merging, not conditionally
based on tracked staleness).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_controller_loop_submit_requires_fresh_verify.py
"""Fix: submit_changes must not rely on a stale lint/test 'done' claim from earlier in
the todo list — the model must be told to re-verify immediately before submitting.

Root cause (found live 2026-07-17): the 'Run lint check and verify' todo item was marked
done early in the session (Task 1), but by session end — after Tasks 3/4/5 added many new
files — `ruff check core/ tests/` showed 18 fresh errors that were never re-caught, because
nothing re-triggers verification once an item is already 'done'.
"""
from agentd.chat.controller_prompts import CONTROLLER_SYSTEM_PROMPT


def test_submit_changes_teaching_requires_fresh_verification():
    idx = CONTROLLER_SYSTEM_PROMPT.find("Variant — submit_changes")
    assert idx != -1, "submit_changes variant block not found"
    # Slice just that variant's teaching (up to the next "Variant —" or section break).
    next_variant = CONTROLLER_SYSTEM_PROMPT.find("Variant —", idx + 1)
    block = CONTROLLER_SYSTEM_PROMPT[idx:next_variant if next_variant != -1 else idx + 2000]
    assert "re-run" in block.lower() or "one more time" in block.lower(), (
        "submit_changes teaching must require a fresh verification run, not rely on an "
        "earlier 'done' claim that may now be stale"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_controller_loop_submit_requires_fresh_verify.py -v`
Expected: FAIL — `AssertionError: submit_changes teaching must require a fresh verification run...`

- [ ] **Step 3: Write minimal implementation**

Find the `submit_changes` variant block in `controller_prompts.py` (currently reads, per the
2026-07-17 session's reading of the file):

```
Variant — submit_changes (EDIT mode, when all edits are done): {type, summary}
  "summary": a non-empty one-liner of what you changed. Emit this to END the edit turn.
  {"type":"submit_changes","thought":"done","summary":"Added with_tax() to src/tax.py and rounded the total in pricing.py."}
```

Replace with:

```
Variant — submit_changes (EDIT mode, when all edits are done): {type, summary}
  "summary": a non-empty one-liner of what you changed. Emit this to END the edit turn.
  BEFORE emitting this: if your todo list has a lint/test/verify item, re-run it ONE MORE
  TIME right now — even if it was already marked 'done' earlier. A 'done' from before your
  most recent edits is stale: later edits can introduce new errors a prior lint/test run
  never saw. Don't trust an old result; get a fresh one.
  {"type":"submit_changes","thought":"done","summary":"Added with_tax() to src/tax.py and rounded the total in pricing.py."}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_controller_loop_submit_requires_fresh_verify.py -v`
Expected: PASS

- [ ] **Step 5: Run the full controller test suite**

Run: `pytest tests/ -k controller -v`
Expected: PASS, no regressions (was 202 passed at end of the 2026-07-17 session before this fix).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/tests/test_controller_loop_submit_requires_fresh_verify.py
git commit -m "fix(controller): require a fresh verify run before submit_changes, not a stale done claim

A lint/test todo item marked done early in a turn can go stale once
later edits add new files/changes. Require re-running verification
immediately before submit_changes rather than trusting an old result."
```

---

## Self-Review

### Spec Coverage
- [x] Open item A (compaction token-undercount) → Task 1
- [x] Open item C (lint-claim staleness) → Task 2

### Placeholder Scan
- No "TBD", "TODO", "implement later" — both fixes have complete code in every step.
- Both root causes are backed by real numbers/log lines captured during the actual session, not
  guessed.

### Type Consistency
- `estimate_tokens(text: str) -> int` signature unchanged in Task 1 — every call site
  (`_history_tokens`, `_select_hot`, `_truncate_to_tokens`) keeps working without modification.
- Task 2 touches only prompt text, no function signatures — verified via a substring-presence
  test on `CONTROLLER_SYSTEM_PROMPT`, the same verification style already used for the
  TDD-gap fix's prompt changes.

### Gaps Identified
- Task 1's `len//3` is still a heuristic, not a real tokenizer — flagged explicitly in the code
  comment as the first thing to revisit if this proves insufficient (next step: a real BPE
  estimate, e.g. `tiktoken`, even as an approximation for non-OpenAI models).
- Task 2 is deliberately NOT a full staleness-tracking system (no timestamps on `TodoItem`) — if
  live use shows the "always re-verify before submit" prompt rule doesn't reliably land (same
  risk class as the narrate-without-act fix, which is prompt-only and known to sometimes need
  more than one nudge), the documented escalation is a real Python-level check at the
  `submit_changes` gate, not a further prompt reword.
