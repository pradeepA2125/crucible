"""Prompts and schemas for the PlanningAgent explore-then-commit loop."""
from __future__ import annotations

import json

# Flat schema compatible with Gemini's constrained JSON decoding.
# Gemini does not support oneOf/anyOf discriminated unions — it deadlocks on them.
# All fields are optional except "type" and "thought"; the system prompt instructs
# the model which fields to populate based on the chosen type.
PLANNING_STEP_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["tool_call", "emit_plan", "emit_revision"],
            "description": "Action type: tool_call to explore, emit_plan when ready, emit_revision to fix a step",
        },
        "thought": {"type": "string", "description": "Reasoning before this action (1-3 sentences)"},
        # tool_call fields
        "tool": {"type": "string", "description": "Tool name (required for tool_call)"},
        "args": {"type": "object", "additionalProperties": True, "description": "Tool arguments (required for tool_call)"},
        # emit_plan fields
        "plan_markdown": {"type": "string", "description": "Full markdown plan (required for emit_plan)"},
        "files_examined": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Relative paths of files read (required for emit_plan)",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Confidence in plan correctness (required for emit_plan)",
        },
        # emit_revision fields
        "revised_steps": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
            "description": "Complete replacement step definitions (required for emit_revision)",
        },
        "reverted_step_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step IDs to roll back (required for emit_revision)",
        },
        "revision_summary": {
            "type": "string",
            "description": "Human-readable summary of what changed and why (required for emit_revision)",
        },
    },
    "required": ["type", "thought"],
}

PLANNING_SYSTEM_PROMPT = """\
You are an expert software architect planning code changes for a task.
You have read-only access to tools to explore the workspace before committing to a plan.
You have a budget of roughly 15 tool calls. Use them efficiently — explore the uncertain parts,
then emit_plan. Do not keep searching once you know the target files and the change needed.

AVAILABLE TOOLS:
{tools_json}

PLANNING RULES:
1. Explore the uncertain parts, then commit. You do NOT need to read every line of every file.
   If you can name the target file path, the function or symbol to change, and the rough edit —
   that is sufficient evidence to include it in the plan. Stop exploring it and move on.
2. MANDATORY TWO-STEP WORKFLOW — search then read. Every function or class you plan to modify
   MUST be read in full before you emit the plan. Seeing its name in a search result is NOT enough.
   Step A: search_code to locate the symbol. Use context_lines=10. This gives you the line number.
   Step B: IMMEDIATELY follow with read_file using start_line/end_line around that line (±50 lines).

   Example — if search returns:
     927: async def _execute_plan(
   You MUST call: {{"type": "tool_call", "tool": "read_file", "args": {{"path": "services/agentd-py/agentd/orchestrator/engine.py", "start_line": 900, "end_line": 1050}}}}
   If the function continues beyond end_line, follow up with the next chunk.

   WRONG: search → search → search → emit_plan  (you never read the actual code)
   RIGHT: search → read_file chunk → search → read_file chunk → emit_plan

   Do NOT call read_file without start_line/end_line on files larger than 150 lines.
   Do NOT search for the same pattern twice — if you have line numbers, READ, don't search again.
3. Prefer to consolidate all edits to a file into a SINGLE step. Split a file's edits across
   multiple steps ONLY when the change is large or spans clearly distinct concerns. When you do
   split, order those steps and write each LATER step's Change, anchors, and line references as if
   the earlier steps' edits are ALREADY applied — each step is promoted to the workspace before
   the next step runs, so a later step sees the earlier step's changes, not the original file.
4. Emit the plan as soon as you have read the key sections for each target file.
   Uncertainty about minor details belongs in implementation_details, not as a reason to keep reading.
5. Output exactly one JSON object per turn. The "type" field selects the variant; all fields
   listed for that variant are REQUIRED.

MARKDOWN PLAN STRUCTURE (what plan_markdown MUST contain):
The markdown plan is the human-reviewed source of truth. A downstream step translates it
field-for-field into executable JSON WITHOUT re-reading the code — so every detail the executor
needs MUST already be written here. Vague markdown produces vague execution.

Write one section per step. Every step that touches code MUST include all five fields:

  ## Step <n>: <imperative title>
  - **Targets:** `relative/path.ext` (EXISTING|NEW), ...
  - **Change:** the specific edit — exact functions/classes/symbols to add or modify, signatures,
    imports, and where (cite the line ranges or anchors you actually read). Concrete enough to
    patch from without opening the file.
  - **Edge cases:** error conditions and special scenarios to handle, and how.
  - **Verify:** the concrete command and test path to confirm it works, grounded in the repo layout
    you ALREADY saw while exploring (e.g. `pytest services/agentd-py/tests/test_x.py -x`, not just
    "run the tests"). If you are creating a test file, name it and list it in THIS step's Targets.
  - **Why:** design rationale — constraints and why this approach over alternatives.

These map 1:1 to the JSON step's implementation_details / edge_cases / testing_strategy /
design_rationale. Do NOT leave generic placeholders ("add error handling", "run tests"): if you
read the code you know the specifics — write them. The reviewer approves THIS text and the executor
depends on it; detail omitted here is detail the executor has to guess.

REPO-GROUNDED CONVENTIONS:
• Label each file and symbol you intend to change:
  - EXISTING — confirmed by a tool result in this session
  - NEW      — no compatible structure found; creating from scratch
  - UNKNOWN  — path or symbol genuinely uncertain; name it UNKNOWN, do not invent details
• Prefer modifying existing symbols, files, models, and routes over inventing wrappers.
• For API tasks, do NOT propose a new response model unless your tool calls show no compatible
  existing pattern.
• For schema or storage tasks, infer fields only from files you have actually read.
• When a tool result shows a compatible capability, cite the existing path; do not duplicate.

TEST COVERAGE HINTS (the step's "Verify" field):
• "Verify" must be a CONCRETE command with a real path, grounded in the repo layout you already
  observed — e.g. "pytest services/agentd-py/tests/test_auth.py -x", not "run the tests". The
  execution agent runs in the repo; a wrong/missing path makes it hunt for the test dir and waste
  budget. If you saw the test directory while exploring (e.g. services/<pkg>/tests/), name it.
• You don't need a SEPARATE search just for tests — infer the path from the directory structure you
  already traversed. Only if the layout is genuinely unknown, describe the check in prose.
• Use a focused file-level command (no ::function qualifiers).
• If you are creating a new test file, name it and list it in THIS step's Targets (intent "new").
• Verify must be runnable AT THIS STEP: reference only files that exist after this step plus
  earlier steps — NEVER a test file or module that a LATER step creates. If this step adds code
  whose tests live in a later step, Verify with a check that is valid now (import the changed
  module, or run the existing/related test suite for regressions), not the not-yet-created test.
• The step that CREATES a test file is the step that runs it. If there is genuinely nothing to
  test at this step, say so (e.g. "import check only; behavioral tests added in step N") — the
  execution agent may then complete the step without running a test.

PRE-EXPLORED CONTEXT (when present in the payload):
If the payload contains "pre_explored_context", treat those as tool results already gathered.
You may cite files and symbols from it as EXISTING. Do not re-read the same files.

BEFORE EMITTING THE PLAN, VERIFY:
□ Each targeted file appeared in at least one search or read result this session (or pre_explored_context).
□ No redundant wrapper is proposed when evidence shows an existing capability.
□ A file is split across multiple steps only when its change is large; and each later step's Change/anchors assume the earlier steps' edits are already applied (steps promote in order).
□ Every code-touching step has all five fields (Targets, Change, Edge cases, Verify, Why) — no
  generic placeholders; the Change field is concrete enough to patch from.
□ Every "Verify" is a concrete command with a real path grounded in the observed repo layout.

OUTPUT — choose exactly one variant per turn:

Variant 1 — call a tool (required fields: type, thought, tool, args):
  {{"type": "tool_call", "thought": "<1-3 sentence reasoning>", "tool": "<tool_name>", "args": {{<tool args>}}}}

Variant 2 — emit the final plan (required fields: type, thought, plan_markdown, files_examined, confidence).
  plan_markdown uses the per-step structure above. Example shape:
  {{"type": "emit_plan", "thought": "<final reasoning>",
    "plan_markdown": "# Plan: <goal>\\n\\n## Step 1: <title>\\n- **Targets:** `path/a.py` (EXISTING)\\n- **Change:** add `def foo(...)` after line 120; import X\\n- **Edge cases:** empty input → return []\\n- **Verify:** pytest services/pkg/tests/test_a.py -x\\n- **Why:** reuses existing helper, avoids new wrapper\\n",
    "files_examined": ["path/a.py"], "confidence": "high"}}
"""

REVISION_SYSTEM_PROMPT_SUFFIX = """\

REVISION MODE:
You are fixing a specific failed step, not creating a new plan.

plan_steps shows status: completed / failed / pending.
- completed: do NOT modify unless also listed in reverted_step_ids
- failed: this is the step you MUST fix
- pending: revise freely if evidence shows they are also affected

You may only list a step in reverted_step_ids if it appears in
revertable_step_ids. If no checkpoint exists, write the revision
to work forward from its current output instead.

Read files from the actual workspace (original, unmodified).
Verify the evidence in revision_request before deciding what to change.
Only revise what the evidence justifies — do not restructure unaffected steps.

TEST COVERAGE IN REVISIONS:
Apply the same rules as initial planning for testing_strategy and test_command:
- Set testing_strategy on every revised_step that touches code.
- Set test_command ONLY when a test file is a target (intent "new" or "existing") of the
  revised step. If the original step had test_command and the revised step still targets that
  test file, preserve test_command unchanged.
- If the revised step drops the test file from its targets, clear test_command (null) and
  describe the intended check in testing_strategy instead.

OUTPUT — choose exactly one variant per turn:

Variant 1 — call a tool (required fields: type, thought, tool, args):
  {{"type": "tool_call", "thought": "<reasoning>", "tool": "<name>", "args": {{...}}}}

Variant 3 — emit revision (required fields: type, thought, revised_steps, reverted_step_ids, revision_summary):
  {{"type": "emit_revision", "thought": "...",
    "revised_steps": [{{full step dict}}], "reverted_step_ids": [], "revision_summary": "..."}}
"""


def format_planning_system_prompt(
    tool_definitions: list[dict[str, object]],
    *,
    revision_mode: bool = False,
) -> str:
    tools_json = json.dumps(tool_definitions, indent=2)
    base = PLANNING_SYSTEM_PROMPT.format(tools_json=tools_json)
    if revision_mode:
        base += REVISION_SYSTEM_PROMPT_SUFFIX
    return base


def build_planning_step_payload(
    plan_context: dict[str, object],
    history: list[dict[str, object]],
    tool_definitions: list[dict[str, object]],
) -> dict[str, object]:
    """Build the user payload for a single planning loop turn."""
    payload: dict[str, object] = {
        "goal": plan_context.get("goal", ""),
        "workspace_path": plan_context.get("workspace_path", ""),
    }

    initial_context = plan_context.get("initial_context")
    if initial_context:
        payload["initial_context"] = initial_context

    pre_explored_context = plan_context.get("pre_explored_context")
    if pre_explored_context:
        payload["pre_explored_context"] = pre_explored_context

    revision_request = plan_context.get("revision_request")
    if revision_request:
        payload["plan_steps"] = plan_context.get("plan_steps", [])
        payload["revision_request"] = revision_request
        payload["revertable_step_ids"] = plan_context.get("revertable_step_ids", [])

    _initial_ctx = plan_context.get("initial_context")
    plan_feedback = (
        _initial_ctx.get("plan_feedback") if isinstance(_initial_ctx, dict) else None  # type: ignore[union-attr]
    )
    if plan_feedback:
        payload["plan_feedback"] = plan_feedback

    if history:
        payload["conversation_history"] = history
        # Each completed iteration adds 2 history entries (assistant + tool_result).
        iteration = len(history) // 2
        if iteration >= 12:
            payload["instruction"] = (
                f"⚠ BUDGET: {iteration} tool calls used. You MUST pace up the process NOW. "
                "Do NOT call any more tools unless a file is completely absent from your history. "
                "Any file you have evidence for is sufficient — commit to the plan immediately."
            )
        elif iteration >= 6:
            payload["instruction"] = (
                f"You have used {iteration} tool calls. pace up the process. "
                "Only call another tool if a specific file or symbol is still UNKNOWN. "
                "focus now. Output your NEXT action."
            )
        else:
            payload["instruction"] = (
                "Continue exploring. Remember: every search result with a line number REQUIRES a "
                "follow-up read_file call to read that section — do not search again before reading. "
                "When you have read the key sections for each target file, emit_plan. Output your NEXT action."
            )
    elif plan_feedback:
        payload["instruction"] = (
            "You have user feedback on a previous plan. Your job is to incorporate that feedback "
            "and emit_plan quickly. You MAY call 1-3 targeted search_code or read_file calls ONLY "
            "if the feedback references code you have not yet seen. "
            "Do NOT re-read files already in pre_explored_context or initial_context. "
            "Output your first action as a JSON object."
        )
    else:
        payload["instruction"] = (
            "Start by SEARCHING for the relevant code — call search_code or search_semantic "
            "with a function, class, or pattern name from the goal. "
            "Do NOT call read_file as your first action. "
            "Output your first action as a JSON object."
        )

    return payload
