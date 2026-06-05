# Feedback Plan-Patch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a plan-feedback round emit a small `emit_plan_patch` (a list of `search_replace` ops against the current plan) instead of re-emitting the full `plan_markdown`, so weak models don't degrade at high context.

**Architecture:** New `emit_plan_patch` action type in the planning response schema (gated to feedback rounds). The current plan is written to `<shadow>/plan.md`; the model's `search_replace` ops are applied via the existing `PatchEngine`; the patched file is read back as the new `plan_markdown`. Apply failures are non-fatal (inject a correction, continue the loop). The model chooses `emit_plan_patch` (small edits) vs `emit_plan` (large rewrite) by scale. No line numbers anywhere; the current plan reaches the model append-only (the replayed `emit_plan` history turn for round 2; an appended feedback turn for round 3+).

**Tech Stack:** Python 3.13, Pydantic v2, pytest/pytest-asyncio. Reuses `PatchEngine` (`agentd/patch/engine.py`), `SearchReplaceOpV2`/`PatchCandidateV2` (`agentd/domain/models.py`), and the existing feedback-replay machinery (`planning_conversation_history`, `_format_feedback_turn`).

**Design spec:** `docs/superpowers/specs/2026-06-05-feedback-plan-patch-design.md`

**Prerequisite:** The feedback-replay work (seed_history / `planning_conversation_history` / `_format_feedback_turn`) must be present in the working tree — this plan builds on it.

---

## File Structure

- **Create** `agentd/planning/plan_patch.py` — pure applier: takes the current plan markdown + raw ops, writes `plan.md` under a scratch dir, applies `search_replace` ops via `PatchEngine`, returns the patched markdown; raises `PlanPatchError` on apply failure. One responsibility: turn (plan, ops) → new plan or error.
- **Modify** `agentd/planning/prompts.py` — add the `emit_plan_patch` schema variant; gate its `type` enum entry to feedback rounds; teach the model when to use it.
- **Modify** `agentd/planning/loop.py` — handle `action_type == "emit_plan_patch"` in `_run_single_pass`; thread the current plan + scratch dir + "patch allowed" flag through `run`.
- **Modify** `agentd/planning/agent.py` — pass current plan + scratch dir + patch-allowed through `generate_plan` to the loop.
- **Modify** `agentd/orchestrator/engine.py` — in the `continue_task` feedback branch: enable patch mode, pass the current plan + shadow root, render the current plan into the feedback turn on round 3+, broadcast a `plan_diff` event.
- **Create/extend tests** `tests/test_plan_patch.py`, `tests/test_planning_agent.py`, `tests/test_plan_feedback_history.py`.

---

## Task 1: Pure plan-patch applier

**Files:**
- Create: `agentd/planning/plan_patch.py`
- Test: `tests/test_plan_patch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_patch.py
from __future__ import annotations
from pathlib import Path
import pytest
from agentd.planning.plan_patch import apply_plan_patch, PlanPatchError

_PLAN = "# Plan\n\n## Step 1: Alpha\n- do alpha\n\n## Step 2: Beta\n- do beta\n"


@pytest.mark.asyncio
async def test_apply_single_search_replace(tmp_path: Path) -> None:
    ops = [{"op": "search_replace", "search": "- do beta", "replace": "- do beta CHANGED", "reason": "fix"}]
    out = await apply_plan_patch(_PLAN, ops, scratch_dir=tmp_path)
    assert "- do beta CHANGED" in out
    assert "- do alpha" in out  # untouched


@pytest.mark.asyncio
async def test_apply_multiple_disjoint_ops(tmp_path: Path) -> None:
    ops = [
        {"op": "search_replace", "search": "- do alpha", "replace": "- do ALPHA", "reason": "a"},
        {"op": "search_replace", "search": "- do beta", "replace": "- do BETA", "reason": "b"},
    ]
    out = await apply_plan_patch(_PLAN, ops, scratch_dir=tmp_path)
    assert "- do ALPHA" in out and "- do BETA" in out


@pytest.mark.asyncio
async def test_search_not_found_raises_planpatcherror(tmp_path: Path) -> None:
    ops = [{"op": "search_replace", "search": "- nonexistent", "replace": "x", "reason": "r"}]
    with pytest.raises(PlanPatchError) as exc:
        await apply_plan_patch(_PLAN, ops, scratch_dir=tmp_path)
    assert "not found" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_ambiguous_search_raises_planpatcherror(tmp_path: Path) -> None:
    plan = "## Step 1: X\n- shared\n\n## Step 2: Y\n- shared\n"
    ops = [{"op": "search_replace", "search": "- shared", "replace": "- z", "reason": "r"}]
    with pytest.raises(PlanPatchError) as exc:
        await apply_plan_patch(plan, ops, scratch_dir=tmp_path)
    assert "unique" in str(exc.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plan_patch.py -p no:cacheprovider -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.planning.plan_patch'`

- [ ] **Step 3: Write minimal implementation**

```python
# agentd/planning/plan_patch.py
"""Apply search/replace ops to the markdown plan, reusing PatchEngine.

The plan is written to <scratch_dir>/plan.md, the ops are applied as
SearchReplaceOpV2 against that file, and the patched file is read back. This
reuses the exact op + engine the execution loop already uses for code, and keeps
the plan addressable by content (no line numbers).
"""
from __future__ import annotations

from pathlib import Path

from agentd.domain.models import PatchCandidateV2, SearchReplaceOpV2
from agentd.patch.engine import PatchEngine

_PLAN_FILE = "plan.md"


class PlanPatchError(Exception):
    """A plan patch op could not be applied (search text missing or not unique)."""


async def apply_plan_patch(
    plan_markdown: str,
    ops: list[dict[str, object]],
    *,
    scratch_dir: Path,
    patch_engine: PatchEngine | None = None,
) -> str:
    """Apply search_replace `ops` to `plan_markdown`; return the patched markdown.

    Raises PlanPatchError if any op fails to apply (so the caller can inject a
    correction and let the model retry).
    """
    if not ops:
        raise PlanPatchError("emit_plan_patch had no ops")

    engine = patch_engine or PatchEngine()
    scratch_dir.mkdir(parents=True, exist_ok=True)
    plan_path = scratch_dir / _PLAN_FILE
    plan_path.write_text(plan_markdown, encoding="utf-8")

    patch_ops: list[SearchReplaceOpV2] = []
    for raw in ops:
        if not isinstance(raw, dict) or raw.get("op") != "search_replace":
            raise PlanPatchError(f"unsupported plan patch op: {raw!r} (only search_replace)")
        try:
            patch_ops.append(
                SearchReplaceOpV2(
                    op="search_replace",
                    file=_PLAN_FILE,
                    search=str(raw.get("search", "")),
                    replace=str(raw.get("replace", "")),
                    reason=str(raw.get("reason", "plan edit")),
                )
            )
        except Exception as exc:  # pydantic validation (e.g. empty search)
            raise PlanPatchError(f"invalid plan patch op: {exc}") from exc

    candidate = PatchCandidateV2(candidate_id="plan-patch", patch_ops=patch_ops)
    result = await engine.apply_patch_candidate(
        scratch_dir, candidate, allowed_files={_PLAN_FILE}
    )
    if not result.success:
        msg = result.issues[0].message if result.issues else "plan patch failed to apply"
        raise PlanPatchError(msg)

    return plan_path.read_text(encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plan_patch.py -p no:cacheprovider -q`
Expected: PASS (4 tests). If `PatchResult.success`/`.issues[].message` field names differ, fix the read to match `agentd/domain/models.py` (`PatchResult`, `PatchIssue`).

- [ ] **Step 5: Commit**

```bash
git add agentd/planning/plan_patch.py tests/test_plan_patch.py
git commit -m "feat(planning): plan-patch applier reusing PatchEngine search_replace"
```

---

## Task 2: `emit_plan_patch` schema variant + gating + prompt

**Files:**
- Modify: `agentd/planning/prompts.py` (`PLANNING_STEP_RESPONSE_SCHEMA`, `format_planning_system_prompt`, `build_planning_step_payload`)
- Test: `tests/test_planning_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planning_agent.py  (add)
from agentd.planning.prompts import planning_response_schema

def test_emit_plan_patch_absent_when_no_current_plan() -> None:
    schema = planning_response_schema(allow_plan_patch=False)
    assert "emit_plan_patch" not in schema["properties"]["type"]["enum"]

def test_emit_plan_patch_present_on_feedback_round() -> None:
    schema = planning_response_schema(allow_plan_patch=True)
    assert "emit_plan_patch" in schema["properties"]["type"]["enum"]
    # ops carry search_replace shape
    assert "ops" in schema["properties"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planning_agent.py -k emit_plan_patch -p no:cacheprovider -q`
Expected: FAIL — `ImportError: cannot import name 'planning_response_schema'`

- [ ] **Step 3: Write minimal implementation**

In `agentd/planning/prompts.py`, add `ops` + `question`-style properties to `PLANNING_STEP_RESPONSE_SCHEMA` and a builder that filters the `type` enum:

```python
# Add to PLANNING_STEP_RESPONSE_SCHEMA["properties"] (alongside plan_markdown etc.):
"ops": {
    "type": "array",
    "description": "For emit_plan_patch: search_replace ops against the current plan",
    "items": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["search_replace"]},
            "search": {"type": "string", "description": "exact unique snippet from the current plan"},
            "replace": {"type": "string", "description": "replacement text"},
            "reason": {"type": "string"},
        },
        "required": ["op", "search", "replace"],
    },
},

# New: a per-call schema view that gates emit_plan_patch.
def planning_response_schema(*, allow_plan_patch: bool) -> dict[str, object]:
    import copy
    schema = copy.deepcopy(PLANNING_STEP_RESPONSE_SCHEMA)
    enum = list(schema["properties"]["type"]["enum"])  # type: ignore[index]
    if allow_plan_patch and "emit_plan_patch" not in enum:
        enum.append("emit_plan_patch")
    if not allow_plan_patch and "emit_plan_patch" in enum:
        enum.remove("emit_plan_patch")
    schema["properties"]["type"]["enum"] = enum  # type: ignore[index]
    return schema
```

Ensure `"emit_plan_patch"` is in the base `PLANNING_STEP_RESPONSE_SCHEMA["properties"]["type"]["enum"]` so the builder can add/remove it. Add a teaching line to `PLANNING_SYSTEM_PROMPT` (gated text is fine since it's constant): "On a feedback round you MAY reply with `emit_plan_patch` (a list of `search_replace` ops editing the current plan) for small/scattered changes; use `emit_plan` only for a large rewrite. Each `search` must be an exact, unique snippet copied from the current plan."

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planning_agent.py -k emit_plan_patch -p no:cacheprovider -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentd/planning/prompts.py tests/test_planning_agent.py
git commit -m "feat(planning): emit_plan_patch schema variant gated to feedback rounds"
```

---

## Task 3: PlanningLoop handles `emit_plan_patch` (non-fatal)

**Files:**
- Modify: `agentd/planning/loop.py` (`run`, `_run_single_pass`)
- Modify: `agentd/planning/agent.py` (`generate_plan`)
- Test: `tests/test_planning_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planning_agent.py  (add)
import pytest
from pathlib import Path
from agentd.domain.models import PlanningResult, TaskBudget
from agentd.planning.loop import PlanningLoop

class _PatchThenEmitEngine:
    """First call returns a bad patch (triggers correction), second a good patch."""
    def __init__(self) -> None:
        self.calls = 0
    async def create_planning_step(self, plan_context, history, tool_definitions,
                                   on_thinking=None, state_description="",
                                   allowed_action_types=None):
        self.calls += 1
        if self.calls == 1:
            return {"type": "emit_plan_patch", "thought": "t",
                    "ops": [{"op": "search_replace", "search": "NOPE", "replace": "x", "reason": "r"}]}
        return {"type": "emit_plan_patch", "thought": "t",
                "ops": [{"op": "search_replace", "search": "- do beta",
                         "replace": "- do beta FIXED", "reason": "r"}]}

@pytest.mark.asyncio
async def test_loop_applies_plan_patch_and_recovers_from_bad_op(tmp_path: Path, _planning_registry) -> None:
    # _planning_registry: existing fixture/stub used by other PlanningLoop tests
    loop = PlanningLoop(reasoning_engine=_PatchThenEmitEngine(), registry=_planning_registry,
                        broadcaster=None, task_id="t")
    plan_ctx = {
        "goal": "g", "workspace_path": str(tmp_path), "task_id": "t",
        "current_plan_markdown": "# Plan\n\n## Step 2: Beta\n- do beta\n",
        "plan_patch_scratch_dir": str(tmp_path / "scratch"),
        "allow_plan_patch": True,
    }
    result = await loop.run(plan_ctx, TaskBudget(), seed_history=[{"role": "user", "content": "feedback"}])
    assert isinstance(result, PlanningResult)
    assert "- do beta FIXED" in result.plan_markdown
```

(Use the same registry stub the other `PlanningLoop` tests use; if none is shared, build a minimal read-only registry like in `test_orchestrator_retrieval.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planning_agent.py -k plan_patch_and_recovers -p no:cacheprovider -q`
Expected: FAIL — the loop has no `emit_plan_patch` branch (treated as malformed).

- [ ] **Step 3: Write minimal implementation**

In `agentd/planning/loop.py` `_run_single_pass`, after the `emit_revision` branch and **before** the `if action_type != "tool_call":` malformed handler, add:

```python
if action_type == "emit_plan_patch":
    from agentd.planning.plan_patch import apply_plan_patch, PlanPatchError
    current_plan = str(plan_context.get("current_plan_markdown", ""))
    scratch = plan_context.get("plan_patch_scratch_dir")
    raw_ops = response.get("ops")
    ops = raw_ops if isinstance(raw_ops, list) else []
    try:
        new_plan = await apply_plan_patch(
            current_plan, ops, scratch_dir=Path(str(scratch))
        )
    except PlanPatchError as exc:
        # Non-fatal: inject a correction, let the model retry or emit_plan.
        history.append(_assistant_turn(response))
        history.append({
            "role": "tool_result", "tool": "",
            "content": (
                f"PLAN PATCH FAILED: {exc}. Each `search` must be an exact, unique "
                "snippet copied verbatim from the current plan. Fix the op, or reply "
                "with emit_plan for a full rewrite."
            ),
        })
        continue
    history.append(_assistant_turn(response))
    return PlanningResult(
        plan_markdown=new_plan,
        files_examined=[],
        confidence="medium",
        tool_trace=trace,
        conversation_history=history,
    )
```

Add `from pathlib import Path` if not already imported at top of `loop.py` (it is used elsewhere; confirm). Thread the new keys through `agent.py::generate_plan` by adding params and putting them in `plan_context`:

```python
# agent.py generate_plan signature additions:
current_plan_markdown: str | None = None,
plan_patch_scratch_dir: str | None = None,
allow_plan_patch: bool = False,
# ...and in plan_context:
if current_plan_markdown is not None:
    plan_context["current_plan_markdown"] = current_plan_markdown
if plan_patch_scratch_dir is not None:
    plan_context["plan_patch_scratch_dir"] = plan_patch_scratch_dir
plan_context["allow_plan_patch"] = allow_plan_patch
```

Also pass `allow_plan_patch` into the per-call schema: where the loop calls `create_planning_step`, the reasoning engine builds the response schema — wire `planning_response_schema(allow_plan_patch=...)` so the `type` enum is gated. (Follow how the execution loop already filters its `type` enum per state.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planning_agent.py -k plan_patch -p no:cacheprovider -q`
Expected: PASS (both the gated-schema tests from Task 2 and this one).

- [ ] **Step 5: Commit**

```bash
git add agentd/planning/loop.py agentd/planning/agent.py tests/test_planning_agent.py
git commit -m "feat(planning): apply emit_plan_patch in the loop; non-fatal on bad op"
```

---

## Task 4: `continue_task` feedback wiring + diff event

**Files:**
- Modify: `agentd/orchestrator/engine.py` (`continue_task` feedback branch; `_format_feedback_turn`)
- Test: `tests/test_plan_feedback_history.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_feedback_history.py  (add)
@pytest.mark.asyncio
async def test_feedback_round_applies_plan_patch(tmp_path: Path) -> None:
    orchestrator, reasoner, task = await _make_orchestrator(tmp_path)
    # _RecordingPlanningEngine returns emit_plan first (round 1), emit_plan_patch on feedback.
    await orchestrator.run_task(task.task_id)
    await orchestrator.continue_task(task.task_id, feedback="tweak step 1")
    refreshed = await orchestrator._store.get(task.task_id)
    assert refreshed.status == TaskStatus.AWAITING_PLAN_APPROVAL
    assert "PATCHED" in (refreshed.plan_markdown or "")  # the patch's replacement text
```

Extend `_RecordingPlanningEngine` so its `create_planning_step` returns an
`emit_plan_patch` (a `search_replace` whose `search` is a snippet of the round-1
`plan_markdown`, `replace` contains "PATCHED") when the seed history contains the
feedback turn.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plan_feedback_history.py -k applies_plan_patch -p no:cacheprovider -q`
Expected: FAIL — `continue_task` doesn't enable patch mode or pass the current plan/scratch dir, so the loop can't apply the patch.

- [ ] **Step 3: Write minimal implementation**

In `continue_task`'s `if feedback:` branch, pass the patch context into `generate_plan`:

```python
planning_result = await planning_agent.generate_plan(
    task=task,
    initial_context=pinned_initial_context,
    budget=task.budget,
    pre_explored_context=task.initial_explore_context or None,
    chat_channel_id=task.chat_channel_id,
    seed_history=seed_history,
    current_plan_markdown=task.plan_markdown,
    plan_patch_scratch_dir=str(Path(task.shadow_workspace_path) / ".plan-patch"),
    allow_plan_patch=True,
)
```

For round 3+, include the current plan in the appended feedback turn so the model
copies `search` snippets from the live version. Update `_format_feedback_turn`:

```python
def _format_feedback_turn(feedback: str, *, current_plan: str | None = None) -> dict[str, object]:
    body = (
        "The user reviewed your plan and gave this feedback:\n\n"
        f"{feedback}\n\n"
    )
    if current_plan:
        body += (
            "Current plan (edit it with emit_plan_patch search_replace ops, copying "
            "exact unique snippets from below; or emit_plan for a large rewrite):\n\n"
            f"{current_plan}\n\n"
        )
    body += (
        "Revise the plan to address the feedback. Explore only if the feedback raises "
        "something not already examined."
    )
    return {"role": "user", "content": body}
```

Call it with `current_plan=task.plan_markdown`. After `generate_plan` returns, if the
plan changed, broadcast a diff event:

```python
self.broadcaster.broadcast(task.chat_channel_id or task_id, {
    "type": "plan_diff",
    "payload": {"task_id": task_id, "plan_markdown": task.plan_markdown},
})
```

(Place after `task.plan_markdown = planning_result.plan_markdown`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plan_feedback_history.py -k applies_plan_patch -p no:cacheprovider -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentd/orchestrator/engine.py tests/test_plan_feedback_history.py
git commit -m "feat(orchestrator): feedback rounds can apply emit_plan_patch + emit plan_diff"
```

---

## Task 5: Regression sweep + KV-invariant guard

**Files:**
- Test: `tests/test_plan_feedback_history.py`

- [ ] **Step 1: Write the failing test (append-only / no-mutation guard)**

```python
# tests/test_plan_feedback_history.py  (add)
@pytest.mark.asyncio
async def test_feedback_turn_is_append_only_with_patch(tmp_path: Path) -> None:
    orchestrator, reasoner, task = await _make_orchestrator(tmp_path)
    await orchestrator.run_task(task.task_id)
    await orchestrator.continue_task(task.task_id, feedback="one")
    h1 = list(reasoner.histories[1])
    await orchestrator.continue_task(task.task_id, feedback="two")
    h2 = reasoner.histories[2]
    # round-2 history is a prefix of round-3 history (no earlier entry mutated)
    assert h2[: len(h1)] == h1
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `pytest tests/test_plan_feedback_history.py -k append_only_with_patch -p no:cacheprovider -q`
Expected: PASS if Task 4 kept appends append-only; FAIL means the feedback-turn render mutated an earlier entry — fix to append-only.

- [ ] **Step 3: Full regression**

Run: `pytest tests/ -k "plan_patch or feedback or planning or orchestrator or turboquant or tools_registry or search" -p no:cacheprovider -q`
Expected: all pass except the 4 pre-existing `test_tools_registry.py` `get_event_loop` failures (Python 3.13 legacy asyncio, unrelated).

- [ ] **Step 4: Commit**

```bash
git add tests/test_plan_feedback_history.py
git commit -m "test(planning): append-only KV guard for plan-patch feedback rounds"
```

---

## Self-Review

**Spec coverage:**
- `emit_plan_patch` action gated to feedback rounds → Task 2 (schema gating) + Task 3 (loop) + Task 4 (enabled in continue_task).
- `search_replace` ops applied via `PatchEngine` to shadow `plan.md` → Task 1.
- Plan file in shadow root (not artifacts) → Task 4 (`shadow_workspace_path/.plan-patch`).
- Model chooses patch vs full emit by scale → Task 2 (prompt) + gated enum keeps `emit_plan` always legal.
- Non-fatal apply failures (inject + continue, no fallback ladder) → Task 3.
- Current plan delivered append-only, no line numbers (emit turn round 2; feedback turn round 3+) → Task 4 (`_format_feedback_turn`) + relies on existing emit-turn append.
- KV append-only invariant → Task 5 guard.
- Diff shown → Task 4 `plan_diff` broadcast.

**Out of scope (per spec):** first-plan emit, JSON `PlanDocument` patching, delta-replan/`emit_revision`, VS Code diff-card rendering.

**Open verification points for the implementer:**
- Confirm `PatchResult` exposes `.success` and `.issues[].message` (Task 1 Step 4); adjust field reads if names differ.
- Confirm where `create_planning_step` builds the response schema, to wire `planning_response_schema(allow_plan_patch=...)` (Task 3) — mirror the execution loop's per-state `type`-enum filtering.
- Confirm `loop.py` imports `Path` (Task 3).
```
