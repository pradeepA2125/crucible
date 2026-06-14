"""Prompt + schema for the agentic chat controller loop.

Mirrors planning/prompts.py: a FLAT response schema (a `type` enum + all variant
fields as optional siblings — NOT JSON-schema oneOf/anyOf, which Gemini deadlocks
on), per-phase gated by deep-copy + enum-trim; a system prompt carrying the tool
JSON; and a payload builder that keeps per-turn-varying fields LAST so the prompt
prefix stays KV-cache stable.
"""
from __future__ import annotations

import copy
import json

# Flat union (see module docstring). Mirrors PLANNING_STEP_RESPONSE_SCHEMA.
CONTROLLER_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["tool_call", "answer", "clarify", "propose_mode", "edit", "submit_changes"],
        },
        "thought": {"type": "string"},
        # tool_call
        "tool": {"type": "string"},
        "args": {"type": "object"},
        # answer / clarify
        "answer": {"type": "string"},
        "question": {"type": "string"},
        # propose_mode
        "plan_sketch": {"type": "string"},
        "recommended": {"type": "string"},
        "reason": {"type": "string"},
        "options": {"type": "array", "items": {"type": "object"}},
        # edit
        "patch_ops": {"type": "array", "items": {"type": "object"}},
        # submit_changes
        "summary": {"type": "string"},
    },
    "required": ["type", "thought"],
}

_PHASE_TYPES: dict[str, list[str]] = {
    "DECIDE": ["tool_call", "answer", "clarify", "propose_mode"],
    "EDIT": ["tool_call", "edit", "submit_changes"],
}


def controller_response_schema(*, phase: str) -> dict[str, object]:
    """Return the response schema with the `type` enum trimmed to the phase's
    allowed actions (deep-copied so the module-level schema is never mutated)."""
    schema = copy.deepcopy(CONTROLLER_RESPONSE_SCHEMA)
    schema["properties"]["type"]["enum"] = list(_PHASE_TYPES[phase])  # type: ignore[index]
    return schema


CONTROLLER_SYSTEM_PROMPT = """\
You are an agentic coding assistant in a chat turn. You own this turn's loop.
Each step, emit ONE JSON object (no prose, no markdown fences) per the schema.
Explore with tools (reads hit the real workspace). When you can answer in text, use type="answer".
When the request needs changes, DO NOT edit silently — emit type="propose_mode" recommending the
best mode (edit | create_task | resume | explain) with a short plan_sketch ("here's my approach",
not concrete code) and a user-facing description per option; the user picks. After the user picks
"edit" you may emit type="edit" with patch_ops, then type="submit_changes" when done. Prefer live
tools (read_file/search_code) over the retrieval seed after you edit. Available tools:
{tools_json}
"""

_DEFAULT_MAX_ITERS = 32


def format_controller_system_prompt(tool_definitions: list[dict[str, object]]) -> str:
    return CONTROLLER_SYSTEM_PROMPT.format(
        tools_json=json.dumps(tool_definitions, indent=2, sort_keys=True)
    )


def build_controller_step_payload(
    plan_context: dict[str, object],
    history: list[dict[str, object]],
    tool_definitions: list[dict[str, object]],
    *,
    phase: str,
) -> dict[str, object]:
    """Build the user payload for one controller turn.

    KV-cache discipline (mirrors build_planning_step_payload): stable head
    (goal/workspace/retrieval_seed) -> append-only conversation_history ->
    per-turn-varying fields (instruction, budget_status) LAST.
    """
    payload: dict[str, object] = {
        "goal": plan_context.get("goal", ""),
        "workspace_path": plan_context.get("workspace_path", ""),
    }
    seed = plan_context.get("retrieval_seed")
    if seed:
        payload["retrieval_seed"] = seed  # FROZEN; never mutated in place
    max_iters = int(plan_context.get("max_iters", _DEFAULT_MAX_ITERS))  # type: ignore[arg-type]
    iteration = len(history) // 2
    if history:
        payload["conversation_history"] = history
    _phase_hint = (
        "You are in EDIT mode: emit type='edit' (patch_ops) to make changes, then "
        "type='submit_changes' when done. Do NOT propose_mode again."
        if phase == "EDIT"
        else "Explore with tools, then answer, clarify, or propose_mode."
    )
    payload["instruction"] = (
        f"Phase={phase}. {_phase_hint} You have used {iteration} of {max_iters} steps. "
        "Choose ONE action per the schema."
    )
    payload["budget_status"] = f"{iteration}/{max_iters} steps used"  # LAST (varies every turn)
    return payload
