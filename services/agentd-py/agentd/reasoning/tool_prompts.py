"""Prompts and schema for the Phase 4 ReAct tool-use loop."""
from __future__ import annotations

import json

# Discriminated union: each variant enforces its own required fields.
# Constrained-decoding providers (Gemini, OpenAI) enforce this at the token level.
# Anthropic (prompt-only) gets explicit per-variant required lists as guidance.
AGENT_STEP_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "oneOf": [
        {
            "title": "ToolCallAction",
            "description": "Call a tool to gather context before writing code.",
            "properties": {
                "type": {"type": "string", "enum": ["tool_call"]},
                "thought": {"type": "string", "description": "Reasoning before this action (1-3 sentences)"},
                "tool": {"type": "string", "minLength": 1, "description": "Tool name"},
                "args": {"type": "object", "description": "Tool arguments"},
            },
            "required": ["type", "thought", "tool", "args"],
        },
        {
            "title": "EmitPatchAction",
            "description": "Emit patch operations when ready to write code.",
            "properties": {
                "type": {"type": "string", "enum": ["emit_patch"]},
                "thought": {"type": "string", "description": "Final reasoning"},
                "patch_ops": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Patch operations to apply (search_replace, create_file, apply_diff, delete_file)",
                },
            },
            "required": ["type", "thought", "patch_ops"],
        },
        {
            "title": "RevisionNeededAction",
            "description": "Signal that the step's planned approach is fundamentally wrong.",
            "properties": {
                "type": {"type": "string", "enum": ["revision_needed"]},
                "thought": {"type": "string", "description": "Reasoning"},
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Why the step cannot be completed as planned",
                },
                "evidence": {
                    "type": "string",
                    "description": "Specific evidence from tool calls justifying the revision",
                },
                "affected_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Step IDs likely also affected (hint for planning agent)",
                },
            },
            "required": ["type", "thought", "reason", "evidence", "affected_steps"],
        },
    ],
}

TOOL_LOOP_SYSTEM_PROMPT = """\
You are an expert code editor executing a single step of a coding plan.
You have access to tools to gather information before writing code.

AVAILABLE TOOLS:
{tools_json}

PATCH OPERATION FORMATS (for emit_patch):
Each element of patch_ops must be one of these objects:

search_replace — find and replace text in a file (most reliable):
  {{"op": "search_replace", "file": "path/to/file.py", "search": "exact text to find", "replace": "new text", "reason": "why"}}

create_file — create a new file:
  {{"op": "create_file", "file": "path/to/new_file.py", "content": "full file content", "reason": "why"}}

apply_diff — apply a unified diff (for multi-section edits):
  {{"op": "apply_diff", "file": "path/to/file.py", "diff": "@@ -1,3 +1,4 @@\\n context\\n+added line\\n context", "reason": "why"}}

delete_file — delete a file:
  {{"op": "delete_file", "file": "path/to/file.py", "reason": "why"}}

RULES:
1. Use tools to gather context before writing code. Read files you haven't seen.
2. When you have enough information, emit a patch. Do not over-search.
3. The search field in search_replace must be an EXACT substring of the current file content.
4. Output exactly one JSON object per turn matching the schema.
5. To signal a plan error: {{"type": "revision_needed", "thought": "...", "reason": "...", "evidence": "...", "affected_steps": [...]}}
   Use ONLY when the target files/symbols in the plan are fundamentally wrong and cannot be fixed with a patch.
   Provide specific evidence from your tool calls.
"""


def build_tool_step_payload(
    step_context: dict[str, object],
    history: list[dict[str, object]],
    tool_definitions: list[dict[str, object]],
) -> dict[str, object]:
    """Build the user_payload dict for a single ReAct loop turn."""
    payload: dict[str, object] = {
        "step_goal": step_context.get("goal", ""),
        "targets": step_context.get("targets", []),
        "allowed_files": step_context.get("allowed_files", []),
        "last_failure": step_context.get("last_failure"),
    }

    # Rich planner context — include only when present (LLM generated these during planning)
    for field in ("implementation_details", "edge_cases", "design_rationale", "testing_strategy"):
        value = step_context.get(field)
        if value:
            payload[field] = value

    risk = step_context.get("risk")
    if risk and risk != "low":
        payload["risk"] = risk

    # File contents and diagnostics from retrieval context
    file_contents = step_context.get("file_contents")
    if file_contents:
        payload["file_contents"] = file_contents

    diagnostics = step_context.get("diagnostics")
    if diagnostics:
        payload["diagnostics"] = diagnostics

    plan_markdown = step_context.get("plan_markdown")
    if plan_markdown:
        payload["plan_markdown"] = plan_markdown

    # Embed conversation history so this remains a single-turn call (no transport changes)
    if history:
        payload["conversation_history"] = history
        payload["instruction"] = (
            "Continue the conversation above. Output your NEXT action as a JSON object. "
            "If you have gathered enough context, emit_patch. Otherwise call another tool."
        )
    else:
        payload["instruction"] = (
            "Start gathering context for this step. "
            "Output your first action as a JSON object."
        )

    return payload


def format_tool_system_prompt(tool_definitions: list[dict[str, object]]) -> str:
    tools_json = json.dumps(tool_definitions, indent=2)
    return TOOL_LOOP_SYSTEM_PROMPT.format(tools_json=tools_json)
