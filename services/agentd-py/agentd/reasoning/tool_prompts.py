"""Prompts and schema for the Phase 4 ReAct tool-use loop."""
from __future__ import annotations

# Flat schema compatible with Gemini's constrained JSON decoding.
# Gemini does not support oneOf/anyOf discriminated unions — it deadlocks on them.
# All fields are optional except "type" and "thought"; the system prompt instructs
# the model which fields to populate based on the chosen type.
AGENT_STEP_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["tool_call", "emit_patch", "verify_done", "revision_needed"],
            "description": (
                "Action type: tool_call to gather context, emit_patch to write code,"
                " verify_done when checks pass, revision_needed if plan is wrong"
            ),
        },
        "thought": {
            "type": "string",
            "description": "Reasoning before this action (1-3 sentences)",
        },
        # tool_call fields
        "tool": {"type": "string", "description": "Tool name (required for tool_call)"},
        "args": {
            "type": "object",
            "additionalProperties": True,
            "description": "Tool arguments (required for tool_call)",
        },
        # emit_patch fields
        "patch_ops": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
            "description": (
                "Patch operations to apply (required for emit_patch):"
                " search_replace, create_file, apply_diff, delete_file."
                " MUST cover every file in the step's targets list — no partial patches."
            ),
        },
        # verify_done fields
        "verified": {
            "type": "boolean",
            "description": "True when all linters and tests passed (required for verify_done)",
        },
        "test_output": {
            "type": "string",
            "description": "Full output from the last test/lint run (required for verify_done)",
        },
        # revision_needed fields
        "reason": {
            "type": "string",
            "description": "Why the step cannot be completed as planned (required for revision_needed)",  # noqa: E501
        },
        "evidence": {
            "type": "string",
            "description": (
                "Specific evidence from tool calls justifying the revision"
                " (required for revision_needed)"
            ),
        },
        "affected_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step IDs likely also affected (required for revision_needed)",
        },
    },
    "required": ["type", "thought"],
}

TOOL_LOOP_SYSTEM_PROMPT = """\
You are an expert code editor executing ONE specific step of a multi-step coding plan.

STEP FOCUS:
- step_goal is the only thing to implement — nothing from other steps.
- targets is your patch scope. Reads are never scope-restricted.
- Prior steps are already promoted to real workspace — do not re-implement their work.
- If the plan looks fundamentally wrong, read to confirm, then emit revision_needed with evidence.

PATCH OPERATION FORMATS (for emit_patch):
Each element of patch_ops must be one of:

  {{"op": "search_replace", "file": "path/to/file.rs", "search": "exact text to find", "replace": "new text", "reason": "why"}}
  {{"op": "create_file",    "file": "path/to/new.ext",  "content": "full content", "reason": "why"}}
  {{"op": "apply_diff",     "file": "path/to/file.ext", "diff": "@@ -1,3 +1,4 @@\\n context\\n+added\\n context", "reason": "why"}}
  {{"op": "delete_file",    "file": "path/to/file.ext", "reason": "why"}}

EMIT ALL TARGETS: emit_patch must include at least one patch_op for every file in targets.

READ/SEARCH BEHAVIOR:
- Before first patch: reads return real workspace content.
- After first patch: reads automatically switch to shadow workspace (your patched files).

PRIOR STEP FILES:
The prior_step_files field lists paths already modified by accepted earlier steps.
Those files are promoted — read_file returns current content. Never create_file over them.

SCOPE VIOLATIONS:
Emit the patch first — the system auto-approves conventional boundary files (__init__.py,
index.ts, mod.rs, conftest.py). If scope is explicitly denied and you cannot proceed,
emit revision_needed citing the missing file and why it is required.

BINARY DISCOVERY (when run_command fails with "not found"):
  1. find_binary <name>  — probes workspace bins then PATH; follow any AGENT SHOULD hint.
  2. If found: run_command using the resolved path.
  3. If not found with existing manifest: setup_env "<pm sync command>"
  4. If bare workspace: init_workspace ecosystem=<lang> dev_deps=[...] then setup_env.

init_workspace ecosystems: python / node / rust / go — emits minimal manifest, refuses to
overwrite existing ones. setup_env reads your patched shadow files — deps added via
emit_patch are visible to the very next setup_env call.

If setup_env returns "AGENT SHOULD: emit revision_needed" — do it; toolchain is missing.

OUTPUT — exactly one variant per turn:

Variant 1 — tool call:
  {{"type": "tool_call", "thought": "<reasoning>", "tool": "<name>", "args": {{...}}}}

Variant 2 — patch:
  {{"type": "emit_patch", "thought": "<reasoning — confirm all targets covered>", "patch_ops": [...]}}

Variant 3 — plan error:
  {{"type": "revision_needed", "thought": "...", "reason": "...", "evidence": "...", "affected_steps": [...]}}

Variant 4 — verify complete:
  {{"type": "verify_done", "thought": "...", "verified": true, "test_output": "..."}}
"""


def build_tool_step_payload(
    step_context: dict[str, object],
    history: list[dict[str, object]],
    *,
    state_description: str = "",
) -> dict[str, object]:
    """Build the user_payload dict for a single ReAct loop turn.

    When state_description is provided (verify-phase state machine context),
    it becomes the primary instruction. Explore-phase budget hints kick in
    only when no state description is supplied (back-compat for callers that
    haven't been migrated to the SM-driven path).
    """
    payload: dict[str, object] = {
        "step_goal": step_context.get("goal", ""),
        "targets": step_context.get("targets", []),
        "allowed_files": step_context.get("allowed_files", []),
        "last_failure": step_context.get("last_failure"),
    }

    for field in ("implementation_details", "edge_cases", "design_rationale", "testing_strategy"):
        value = step_context.get(field)
        if value:
            payload[field] = value

    risk = step_context.get("risk")
    if risk and risk != "low":
        payload["risk"] = risk

    file_contents = step_context.get("file_contents")
    if file_contents:
        payload["file_contents"] = file_contents

    prior_step_files = step_context.get("prior_step_files")
    if prior_step_files:
        payload["prior_step_files"] = prior_step_files

    diagnostics = step_context.get("diagnostics")
    if diagnostics:
        payload["diagnostics"] = diagnostics

    plan_markdown = step_context.get("plan_markdown")
    if plan_markdown:
        payload["plan_markdown"] = plan_markdown

    if history:
        payload["conversation_history"] = history
        if state_description:
            # SM-driven path: the state description IS the instruction. It tells the
            # model which state it's in, what's available, and what to do next.
            payload["instruction"] = state_description
        else:
            # Back-compat path: explore-phase budget hints when no SM context is wired.
            iteration = len(history) // 2
            recent = [str(m.get("content", "")) for m in history[-6:]]
            patch_fail_count = sum(1 for m in recent if "patch failed" in m.lower() or "not found in" in m)

            if patch_fail_count >= 2:
                payload["instruction"] = (
                    f"⚠ Patch has failed {patch_fail_count} times recently. "
                    "Reading the file before retrying often helps — the content may differ "
                    "from what you expected. Consider a different op type if the current one keeps failing."
                )
            elif patch_fail_count >= 1:
                payload["instruction"] = (
                    "⚠ Last patch failed. The file content may not match your expectations — "
                    "reading it first can help you get the right content before retrying."
                )
            elif iteration >= 12:
                payload["instruction"] = (
                    f"⚠ {iteration} tool calls used — pace up. "
                    "Wrap up exploration and move toward your next action."
                )
            elif iteration >= 6:
                payload["instruction"] = (
                    f"Tool calls used: {iteration}. Consider wrapping up exploration soon."
                )
            else:
                payload["instruction"] = "Continue."
    else:
        payload["instruction"] = "Start exploring — search or read to understand the code before making changes."

    return payload


def format_tool_system_prompt() -> str:
    return TOOL_LOOP_SYSTEM_PROMPT


def inject_tools_into_payload(
    payload: dict[str, object],
    tool_definitions: list[dict[str, object]],
) -> None:
    """Inject available tool definitions into the per-turn payload.

    Called each turn with only the tools allowed in the current state,
    so the model sees exactly what it can call right now.
    """
    payload["available_tools"] = tool_definitions
