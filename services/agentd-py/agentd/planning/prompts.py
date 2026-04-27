"""Prompts and schemas for the PlanningAgent explore-then-commit loop."""
from __future__ import annotations

import json

PLANNING_STEP_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["tool_call", "emit_plan", "emit_revision"],
            "description": "Action type for this turn",
        },
        "thought": {
            "type": "string",
            "description": "Reasoning before taking this action (1-3 sentences)",
        },
        "tool": {
            "type": "string",
            "description": "Tool name (required when type='tool_call')",
        },
        "args": {
            "type": "object",
            "description": "Tool arguments (required when type='tool_call')",
        },
        "plan_markdown": {
            "type": "string",
            "description": "Full markdown plan (required when type='emit_plan')",
        },
        "files_examined": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Relative paths of all files read during exploration",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Confidence in plan correctness (required when type='emit_plan')",
        },
        "revised_steps": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Complete step replacements (required when type='emit_revision')",
        },
        "reverted_step_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step IDs to roll back (must have checkpoints)",
        },
        "revision_summary": {
            "type": "string",
            "description": "Human-readable summary of what changed and why",
        },
    },
    "required": ["type", "thought"],
}

PLANNING_SYSTEM_PROMPT = """\
You are an expert software architect planning code changes for a task.
You have read-only access to tools to explore the workspace before committing to a plan.

AVAILABLE TOOLS:
{tools_json}

PLANNING RULES:
1. Explore broadly before committing. Read the actual files before naming them in the plan.
2. Use search_code to find where things live. Use read_file to confirm structure.
3. All changes to a given file must be consolidated into a single step. Never list the same
   file path in more than one step's targets.
4. When you have high confidence in the target files, emit the plan.
5. Output exactly one JSON object per turn matching the schema.

OUTPUT:
- To call a tool: {{"type": "tool_call", "thought": "...", "tool": "<name>", "args": {{...}}}}
- To emit the final plan: {{"type": "emit_plan", "thought": "...", "plan_markdown": "# Plan\\n...",
    "files_examined": ["path/to/file.py"], "confidence": "high"}}
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

OUTPUT:
- To call a tool: {{"type": "tool_call", "thought": "...", "tool": "<name>", "args": {{...}}}}
- To emit revision: {{"type": "emit_revision", "thought": "...",
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

    revision_request = plan_context.get("revision_request")
    if revision_request:
        payload["plan_steps"] = plan_context.get("plan_steps", [])
        payload["revision_request"] = revision_request
        payload["revertable_step_ids"] = plan_context.get("revertable_step_ids", [])

    if history:
        payload["conversation_history"] = history
        payload["instruction"] = (
            "Continue exploring. Output your NEXT action. "
            "When confident about all target files, emit_plan (or emit_revision in revision mode)."
        )
    else:
        payload["instruction"] = (
            "Start exploring the workspace. Output your first action as a JSON object."
        )

    return payload
