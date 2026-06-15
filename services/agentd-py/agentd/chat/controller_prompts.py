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
Before proposing a change that touches, extends, or depends on EXISTING code, FIRST explore
(search_code/read_file) so your approach is grounded — don't propose blind. (A brand-new isolated
file may need no exploration.) Make plan_sketch CONCRETE — the exact file path, the function
signature, and how it integrates — NOT a restatement of the user's request.
When the request needs changes, DO NOT edit silently — emit type="propose_mode" so the user picks
HOW to proceed. propose_mode MUST have:
  - "plan_sketch": a short "here's my approach" (the areas/files + intended change, NOT concrete code),
  - "reason": one line on why you recommend what you do,
  - "recommended": EXACTLY one of edit | create_task | resume | explain,
  - "options": a list of objects, each {"mode": <one of edit|create_task|resume|explain>,
    "label": <short button text>, "description": <one line>}.
Normally offer BOTH "edit" (make the change inline now, user accepts/rejects each edit) and
"create_task" (plan it as a reviewed, step-by-step task), plus "explain" (just describe it).
Use the exact key "mode" (never "type") and only those four mode values. Example:
{"type":"propose_mode","thought":"...","plan_sketch":"Add clamp() to src/mathutil.py",
 "reason":"Single small new file","recommended":"edit","options":[
   {"mode":"edit","label":"Edit inline now","description":"I add the file directly; you review it."},
   {"mode":"create_task","label":"Plan it as a task","description":"Draft a plan you approve, then execute."},
   {"mode":"explain","label":"Just explain","description":"No changes — I describe the approach."}]}
After the user picks "edit" you may emit type="edit" with patch_ops, then type="submit_changes" when
done. Prefer live tools (read_file/search_code) over the retrieval seed after you edit. Available tools:
{tools_json}
"""

_DEFAULT_MAX_ITERS = 32


def format_controller_system_prompt(tool_definitions: list[dict[str, object]]) -> str:
    # .replace (not .format): the prompt embeds a literal JSON example with { } braces
    # that str.format would misparse as fields.
    return CONTROLLER_SYSTEM_PROMPT.replace(
        "{tools_json}", json.dumps(tool_definitions, indent=2, sort_keys=True)
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
    raw_max = plan_context.get("max_iters", _DEFAULT_MAX_ITERS)
    max_iters = raw_max if isinstance(raw_max, int) else _DEFAULT_MAX_ITERS
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
