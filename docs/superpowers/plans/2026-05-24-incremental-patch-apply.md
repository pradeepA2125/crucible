# Whole-Patch Syntax Validation + `replace_range` Surfacing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the patch engine from rejecting collectively-valid multi-op patches by validating Python syntax once on the whole patch's final result instead of per op, and let the model reach for the already-supported `replace_range` op by surfacing it in the prompt.

**Architecture:** Keep patch apply **atomic** (anchors all-or-nothing, as today). Add a single final per-file syntax check to the V2 preflight (over the in-memory simulated result), and gate the existing per-op syntax check behind a flag that the V2 apply path turns off. Separately, list `replace_range` in the prompt op catalog with scenario guidance and nudge toward it in patch-failure feedback.

**Tech Stack:** Python 3.13, pydantic v2, pytest + pytest-asyncio. Work in the worktree `services/agentd-py`.

**Spec:** `docs/superpowers/specs/2026-05-24-incremental-patch-apply-design.md`

> **Scope note:** Partial/incremental apply is **deferred** and NOT in this plan. The plan filename keeps the old slug for continuity.

---

## File Structure

- `agentd/patch/engine.py` — final `.py` syntax check at the end of `preflight_patch_candidate`; `check_syntax: bool = True` param on `_apply_search_replace` (`:422`) and `_apply_replace_range` (`:351`); `apply_patch_candidate` (`:960`) passes `check_syntax=False`.
- `agentd/reasoning/tool_prompts.py` — add `replace_range` + "best for" guide to the op catalog (`:34-38`, `:79-87`).
- `agentd/tools/loop.py` — one-line `replace_range` nudge in the not-found / ambiguous patch-failure feedback (`:513-552`).
- Tests: `tests/test_patch_engine_syntax.py` (new), `tests/test_tool_prompts.py` (new), and a feedback assertion.

---

## Task 1: Engine — whole-patch-final syntax validation

**Files:**
- Modify: `agentd/patch/engine.py`
- Test: `tests/test_patch_engine_syntax.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_patch_engine_syntax.py`:

```python
from __future__ import annotations

import pytest

from agentd.domain.models import PatchCandidateV2
from agentd.patch.engine import PatchEngine


def _candidate(ops: list[dict]) -> PatchCandidateV2:
    return PatchCandidateV2(candidate_id="c1", patch_ops=ops)


@pytest.mark.asyncio
async def test_split_try_except_across_ops_is_accepted(tmp_path):
    # op0 opens a try: (invalid alone). op1 adds the matching except: .
    # Both anchors match; the FINAL file is valid -> must apply.
    f = tmp_path / "m.py"
    f.write_text("def g():\n    do_thing()\n    after()\n", encoding="utf-8")
    engine = PatchEngine()
    candidate = _candidate([
        {"op": "search_replace", "file": "m.py",
         "search": "    do_thing()\n    after()",
         "replace": "    try:\n        do_thing()\n        after()",
         "reason": "open try"},
        {"op": "search_replace", "file": "m.py",
         "search": "        after()",
         "replace": "        after()\n    except Exception:\n        pass",
         "reason": "close with except"},
    ])

    result = await engine.apply_patch_candidate(tmp_path, candidate, allowed_files={"m.py"})

    assert result.touched_files == ["m.py"]
    text = f.read_text(encoding="utf-8")
    assert "try:" in text and "except Exception:" in text
    # final content is valid python
    compile(text, "m.py", "exec")


@pytest.mark.asyncio
async def test_malformed_final_result_is_rejected_atomically(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = call()\n", encoding="utf-8")
    engine = PatchEngine()
    candidate = _candidate([
        {"op": "search_replace", "file": "m.py", "search": "x = call()",
         "replace": "x = call(", "reason": "unbalanced paren"},
    ])
    with pytest.raises(RuntimeError, match="preflight failed"):
        await engine.apply_patch_candidate(tmp_path, candidate, allowed_files={"m.py"})
    # nothing written
    assert f.read_text(encoding="utf-8") == "x = call()\n"


@pytest.mark.asyncio
async def test_single_valid_search_replace_still_applies(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("A = 1\n", encoding="utf-8")
    engine = PatchEngine()
    candidate = _candidate([
        {"op": "search_replace", "file": "m.py", "search": "A = 1", "replace": "A = 2", "reason": "ok"},
    ])
    result = await engine.apply_patch_candidate(tmp_path, candidate, allowed_files={"m.py"})
    assert result.touched_files == ["m.py"]
    assert f.read_text(encoding="utf-8") == "A = 2\n"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_patch_engine_syntax.py -p no:cacheprovider -q`
Expected: `test_split_try_except_across_ops_is_accepted` FAILS — current per-op check rejects op0's lone `try:` with "Patch preflight failed: ... expected 'except' or 'finally' block" (raised from the per-op `_python_syntax_check` during apply). The other two may already pass.

- [ ] **Step 3: Add the final syntax check to `preflight_patch_candidate`**

In `agentd/patch/engine.py`, at the **end** of `preflight_patch_candidate`, after the `for index, operation in enumerate(candidate.patch_ops):` simulation loop and **before** the final `return PatchPreflightReport(...)`, insert a final per-file Python syntax check over the simulated result:

```python
        # Final whole-patch syntax check: validate the COMBINED result of all ops,
        # not each op individually. This accepts changes that are only valid once all
        # ops apply (e.g. a try: in one op and its except: in another) while still
        # rejecting a genuinely-malformed final result.
        for file in sorted(mutated_files):
            content = simulated_sources.get(file)
            if content is None or not file.endswith(".py"):
                continue
            try:
                _python_syntax_check(content, label=file)
            except RuntimeError as exc:
                issues.append(
                    PatchPreflightIssue(
                        code=PatchFailureCode.APPLY_ERROR,
                        file=file,
                        message=str(exc),
                    )
                )

        return PatchPreflightReport(success=not issues, issues=issues)
```

(Use the existing local variable names `issues`, `simulated_sources`, `mutated_files` from the simulation loop. If the method currently returns via a different final expression, replace that return with the one above.)

- [ ] **Step 4: Gate the per-op syntax check behind `check_syntax`**

In `_apply_search_replace` (`:422`), change the signature and the check:

```python
    def _apply_search_replace(
        self, base_path: Path, operation: SearchReplaceOpV2, *, check_syntax: bool = True
    ) -> None:
        ...
        new_content = original_content.replace(operation.search, operation.replace, 1)
        if check_syntax and operation.file.endswith(".py"):
            _python_syntax_check(new_content, label=operation.file)
        target.write_text(new_content, encoding="utf-8")
```

In `_apply_replace_range` (`:351`), do the same — add `*, check_syntax: bool = True` to the signature and guard its `_python_syntax_check` call (`:377-378`) with `if check_syntax and operation.file.endswith(".py"):`.

- [ ] **Step 5: Turn the per-op check off in the V2 apply loop**

In `apply_patch_candidate` (`:960`), the apply loop (`:984-1003`) calls these methods. Pass `check_syntax=False` (the final result was already validated by preflight at `:970-979`):

```python
                elif isinstance(operation, SearchReplaceOpV2):
                    self._apply_search_replace(base_path, operation, check_syntax=False)
                ...
                elif isinstance(operation, ReplaceRangeOp):
                    self._apply_replace_range(base_path, operation, check_syntax=False)
```

Leave the V1 `apply_patch_document` (`:313`) call to `_apply_replace_range` unchanged (defaults to `check_syntax=True`).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/agentd-py && python -m pytest tests/test_patch_engine_syntax.py -p no:cacheprovider -q`
Expected: PASS (3 tests). Then regression-check the existing engine + loop suites:
`python -m pytest tests/ -k "patch_engine or tool_loop or plan_target or orchestrator_verify" -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agentd/patch/engine.py tests/test_patch_engine_syntax.py
git commit -m "fix(patch): validate syntax on whole-patch final result, not per op"
```

---

## Task 2: Prompt — surface `replace_range` + scenario guide

**Files:**
- Modify: `agentd/reasoning/tool_prompts.py` (`:34-38`, `:79-87`)
- Test: `tests/test_tool_prompts.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_prompts.py`:

```python
from agentd.reasoning.tool_prompts import TOOL_LOOP_SYSTEM_PROMPT, AGENT_STEP_RESPONSE_SCHEMA


def test_prompt_lists_replace_range_with_scenario_guide():
    p = TOOL_LOOP_SYSTEM_PROMPT
    assert "replace_range" in p
    assert "best for" in p.lower()


def test_schema_patch_ops_description_includes_replace_range():
    desc = AGENT_STEP_RESPONSE_SCHEMA["properties"]["patch_ops"]["description"]
    assert "replace_range" in desc
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_tool_prompts.py -p no:cacheprovider -q`
Expected: FAIL — `replace_range` not present.

- [ ] **Step 3: Update the schema `patch_ops` description**

In `agentd/reasoning/tool_prompts.py`, replace the `patch_ops` description (`:34-38`):

```python
            "description": (
                "Patch operations to apply (required for emit_patch):"
                " search_replace, replace_range, apply_diff, create_file, delete_file."
                " MUST cover every file in the step's targets list — no partial patches."
            ),
```

- [ ] **Step 4: Update the system-prompt op formats with a scenario guide**

Replace the `PATCH OPERATION FORMATS` block (`:79-85`) — keep the `EMIT ALL TARGETS` line at `:87` unchanged:

```python
PATCH OPERATION FORMATS (for emit_patch) — pick the op best for the situation; none is preferred:

  {{"op": "search_replace", "file": "f.py", "search": "exact unique text", "replace": "new text", "reason": "why"}}
    Best for: small, localized edits where you can reproduce the exact, unique surrounding text.

  {{"op": "replace_range", "file": "f.py", "anchor": {{"start_line": 10, "end_line": 14}}, "content": "new block", "reason": "why"}}
    Best for: replacing a contiguous block by LINE NUMBERS (from read_file's line-numbered output).
    Use it when the text is hard to reproduce exactly (whitespace/quotes) or an anchor keeps not matching.

  {{"op": "apply_diff",     "file": "f.py", "diff": "@@ -1,3 +1,4 @@\\n context\\n+added\\n context", "reason": "why"}}
    Best for: multi-line hunk edits that carry surrounding context.

  {{"op": "create_file",    "file": "new.ext", "content": "full content", "reason": "why"}}   # new files
  {{"op": "delete_file",    "file": "old.ext", "reason": "why"}}                              # removed files
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/agentd-py && python -m pytest tests/test_tool_prompts.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agentd/reasoning/tool_prompts.py tests/test_tool_prompts.py
git commit -m "feat(prompts): surface replace_range op with scenario guidance"
```

---

## Task 3: Loop — nudge toward `replace_range` on anchor failures

**Files:**
- Modify: `agentd/tools/loop.py` (`:514-540`)
- Test: `tests/test_tool_loop_feedback.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_loop_feedback.py`. The feedback strings are built inline in `ToolLoop.run`; extract them to a tiny static helper so they're unit-testable, then assert content. First write the test against the helper we will add:

```python
from agentd.tools.loop import _anchor_failure_hint


def test_not_found_hint_suggests_replace_range():
    msg = _anchor_failure_hint("not found")
    assert "replace_range" in msg


def test_ambiguous_hint_suggests_replace_range():
    msg = _anchor_failure_hint("appears 3 times")
    assert "replace_range" in msg
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/agentd-py && python -m pytest tests/test_tool_loop_feedback.py -p no:cacheprovider -q`
Expected: FAIL — `ImportError: cannot import name '_anchor_failure_hint'`.

- [ ] **Step 3: Add the `_anchor_failure_hint` helper**

In `agentd/tools/loop.py`, add a module-level helper near `_extract_line_hint` (`:42`):

```python
def _anchor_failure_hint(error_msg: str) -> str:
    """One-line nudge toward the op best suited to an anchor failure."""
    low = error_msg.lower()
    if "appears" in low and "times" in low:
        return ("The search text is not unique. Either extend it with more surrounding "
                "context, or target the block by line range with replace_range "
                "(use the line numbers read_file returns).")
    if "not found" in low:
        return ("The exact text was not found. replace_range is often the reliable choice "
                "here — give start_line/end_line from read_file's line-numbered output and "
                "the new content, instead of reproducing the exact text for search_replace.")
    return ""
```

- [ ] **Step 4: Wire the hint into the not-found / ambiguous feedback branches**

In `ToolLoop.run`, the patch-failure feedback (`:514-540`), append the hint. In the `if "appears" in error_msg and "times" in error_msg:` branch and the `elif "not found" in error_msg.lower():` branch, add `+ "\n" + _anchor_failure_hint(error_msg)` to the `feedback` string (after the existing numbered steps, before the schema injection). Example for the not-found branch:

```python
                            elif "not found" in error_msg.lower():
                                feedback = (
                                    f"Patch FAILED: {error_msg}\n"
                                    "The search text does not exist in the file.\n"
                                    + _anchor_failure_hint(error_msg) + "\n"
                                    "DO NOT re-emit immediately. You MUST search and read first:\n"
                                    f"{line_hint}"
                                    "  1. Use search_code to locate error symbols or the exact code block (shows line numbers).\n"
                                    "  2. Call read_file with start_line and end_line around the target.\n"
                                    "  3. Re-emit using replace_range (line numbers) or search_replace (verbatim text from read_file).\n"
                                    "\nread_file tool schema:\n" + _rf_json + "\n"
                                    "\nsearch_code tool schema:\n" + _sc_json
                                )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/agentd-py && python -m pytest tests/test_tool_loop_feedback.py -p no:cacheprovider -q`
Expected: PASS. Regression: `python -m pytest tests/ -k "tool_loop" -p no:cacheprovider -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add agentd/tools/loop.py tests/test_tool_loop_feedback.py
git commit -m "feat(tool-loop): nudge toward replace_range on anchor-match failures"
```

---

## Notes for the implementer

- **Do not edit watched `*.py` under the worktree `services/agentd-py/` while a task is running** against the live backend — `uvicorn --reload` restarts and kills it.
- The patch stays **atomic** — there is no partial apply here. The only behavioral change is *when* syntax is checked (whole-patch final, not per op) and that `replace_range` is now offered to the model.
- `replace_range`/`ReplaceRangeOp` already exists end-to-end in the engine; Task 2 only surfaces it in the prompt.
