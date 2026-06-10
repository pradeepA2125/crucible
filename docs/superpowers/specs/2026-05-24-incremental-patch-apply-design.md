# Design: Whole-Patch Syntax Validation + `replace_range` Op Surfacing

**Date:** 2026-05-24
**Status:** Approved (pending spec review)
**Area:** `services/agentd-py` — patch engine + execution tool-loop prompt

> **Scope note:** This spec was originally "Incremental Patch Apply." That partial/
> incremental-apply work is **deferred** (too invasive for now). This revision narrows
> to two small, well-understood fixes: (A) the per-op syntax-validation bug, and (B)
> surfacing the already-supported `replace_range` op in the prompt. The filename keeps
> the old slug for continuity.

## Problem

**A. Syntax is validated per-op, so collectively-valid patches are wrongly rejected.**
During step execution, the V2 apply path (`apply_patch_candidate`) walks ops one at a
time, and each op's apply method runs `_python_syntax_check` on *that op's individual
result* before writing (`_apply_search_replace:449`, `_apply_replace_range:378`). When
one logical change spans multiple ops — e.g. op A opens a `try:` and op B adds the
matching `except:` — the per-op check fails at the intermediate state (a `try` with no
handler), even though applying *all* ops yields valid syntax.

Evidence (`task-035fe0b61a32`, step s1, `turn-07.json`): a 5-op patch where **all
anchors matched** (it failed on *syntax*, not "search text not found"). Op index 1
opened `try:` around `self._run_step_with_retries(...)`; op index 3 added
`except TaskPausedError:`. The apply rejected it with *"line 477: expected 'except' or
'finally' block"* — op 1's `try` was syntax-checked before op 3's `except` was applied.

The **preflight is not the problem**: `preflight_patch_candidate` does anchor/text
simulation only (no syntax check), and it *passed* for turn-07. The bug is the **per-op
syntax check during apply**.

**B. `replace_range` exists but the model never uses it.** `replace_range` is already a
valid `PatchOperationV2` (`models.py:512`), validated by preflight and applied by the
engine. Because it targets by line number, it cannot fail with "search text not found"
— ideal when an anchor is hard to reproduce. But it is **not listed in the prompt**
(`tool_prompts.py:36, 82-85` only mention `search_replace, create_file, apply_diff,
delete_file`), so the model has no way to know it exists.

## Goals

- **Fix A:** validate Python syntax on the **whole patch's final result**, once, instead
  of per op. Keep apply **atomic** (anchors remain all-or-nothing; nothing is written
  unless the full patch's final result is valid).
- **Fix B:** add `replace_range` to the prompt's op catalog with scenario ("best for")
  guidance, and add a light nudge toward it in patch-failure feedback so it actually
  gets used.

## Non-goals

- **Partial / incremental apply** (keep-the-ops-that-succeed) — deferred; not in this
  change.
- Any SM, orchestrator, or loop-apply structural changes.
- The single-shot `create_patch` fallback path (`prompt_builder.py`) — untouched.

## A. Whole-patch-final syntax validation (`patch/engine.py`)

`preflight_patch_candidate` already simulates every op sequentially into an in-memory
`simulated_sources` dict (anchors only, no syntax check) and leaves it holding the
final content per file. Two edits:

1. **Add a final syntax check at the end of `preflight_patch_candidate`** — after the
   per-op simulation loop, before building the report: for each mutated file ending in
   `.py` whose simulated content is not `None`, run `_python_syntax_check(content,
   label=file)`; on `RuntimeError`, append a `PatchPreflightIssue(code=APPLY_ERROR,
   file=file, message=<syntax error>)`. A syntax-invalid **final** result therefore
   fails preflight → the whole patch is rejected **before anything is written**.
2. **Gate the per-op syntax check** — add `check_syntax: bool = True` to
   `_apply_search_replace` and `_apply_replace_range`, wrapping their existing
   `_python_syntax_check` calls. In the V2 `apply_patch_candidate` apply loop, call them
   with `check_syntax=False` (the final result is already validated by preflight, and
   the per-op check is what rejects valid intermediates). The V1 `apply_patch_document`
   path keeps the default `True` — unchanged.

Result: a split `try`/`except` whose **final** content is valid is accepted; a patch
whose final content is genuinely malformed is rejected atomically with line numbers; no
partial state is ever introduced.

## B. Surface `replace_range` in the prompt (`reasoning/tool_prompts.py`)

- Add `replace_range` to the `patch_ops` schema description (`:36`) and the
  `PATCH OPERATION FORMATS` block (`:82-85`), each op annotated with a short "best for"
  note (no op ranked above another):
  - `search_replace` — small, localized edits where the exact unique surrounding text
    can be reproduced.
  - `replace_range` — replace a contiguous block by **line numbers** (from `read_file`'s
    line-numbered output); best when text is hard to reproduce exactly or an anchor
    keeps not matching.
  - `apply_diff` — multi-line hunk edits with surrounding context.
  - `create_file` / `delete_file` — new / removed files.
- **Keep** the existing "EMIT ALL TARGETS / no partial patches" instruction (`:37, :87`)
  — apply stays atomic, so it remains correct.
- **Light feedback nudge (`tools/loop.py:513-552`):** in the "search text not found" and
  "appears N times" branches, add one line suggesting `replace_range` with the line
  numbers from `read_file` as the fitting alternative. (Passive catalog availability is
  not enough — the failure feedback is where op-switching is actually triggered.)

## Testing

**Patch engine (`tests/test_patch_engine_syntax.py`, new):**
- Regression for turn-07: a 2-op patch where op0 opens `try:` and op1 adds the matching
  `except:` (anchors both match). Before fix: rejected. After fix: `apply_patch_candidate`
  succeeds and the file is valid.
- A patch whose **final** result is malformed (e.g. unbalanced parens after all ops) →
  `apply_patch_candidate` raises "Patch preflight failed" with a syntax message; nothing
  written.
- A single valid `search_replace` still applies (no regression).
- V1 `apply_patch_document` still rejects a syntactically-bad op (per-op check intact
  via default `check_syntax=True`).

**Prompt (`tests/test_tool_prompts.py`, new):**
- `TOOL_LOOP_SYSTEM_PROMPT` and the `patch_ops` schema description contain `replace_range`
  and a "best for" note.

**Loop feedback (`tests/test_tool_loop_feedback.py`, new or existing):**
- The "not found" patch-failure feedback mentions `replace_range`.

## Files touched

- `patch/engine.py` — final per-`.py`-file syntax check at the end of
  `preflight_patch_candidate`; `check_syntax` param on `_apply_search_replace` /
  `_apply_replace_range`; `apply_patch_candidate` passes `check_syntax=False`.
- `reasoning/tool_prompts.py` — add `replace_range` + scenario guide to the op catalog.
- `tools/loop.py` — one-line `replace_range` nudge in the not-found / ambiguous
  patch-failure feedback.
- Tests as above.

**Not touched:** `domain/models.py` (`replace_range` already a valid op),
`tools/verify_phase_sm.py`, `orchestrator/engine.py`.

## Risks / notes

- Removing the per-op check *for the V2 path* means intermediate on-disk states during a
  multi-op apply can be transiently invalid between writes. That is fine: apply only runs
  after preflight validated the final result, and the final on-disk state equals the
  validated `simulated_sources`. Nothing reads the file between op writes within a single
  apply.
- `APPLY_ERROR` is reused for the final-syntax issue to avoid touching the
  `PatchFailureCode` enum; the message carries the `_python_syntax_check` line:col text.
