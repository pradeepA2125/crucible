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
        # edit — each op: 'file' is a workspace-relative PATH (one line); code goes in
        # 'content' (create_file) or 'search'/'replace' (search_replace). See prompt example.
        "patch_ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["create_file", "search_replace"]},
                    "file": {"type": "string"},
                    "content": {"type": "string"},
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                    "reason": {"type": "string"},
                },
                # Mirror reasoning/tool_prompts.py: force the op-type-agnostic fields the
                # PatchDocumentV2 validator requires. Without this the grammar lets the model
                # omit `reason` (a real source of the "reason Field required" EDIT thrash) and
                # `file`. search/replace/content stay optional because which are needed depends
                # on `op` — a flat schema can't express that (no oneOf, Gemini-deadlock).
                "required": ["op", "file", "reason"],
            },
        },
        # submit_changes
        "summary": {"type": "string"},
    },
    "required": ["type", "thought"],
}

_PHASE_TYPES: dict[str, list[str]] = {
    "DECIDE": ["tool_call", "answer", "clarify", "propose_mode"],
    # EDIT keeps `clarify` so the agent can ask when a genuine ambiguity blocks it
    # mid-edit (reading the workspace can't resolve it); the user's reply resumes the
    # loop in EDIT (ChatController._edit_clarify_pending). It still cannot re-open mode
    # selection — `propose_mode` stays DECIDE-only.
    "EDIT": ["tool_call", "edit", "clarify", "submit_changes"],
    # EXPLAIN (user picked "Just explain"): describe the approach — explore then answer.
    # propose_mode is FORBIDDEN here so the explain re-entry can't re-open the mode gate
    # (finding 4: DECIDE re-entry kept re-proposing); edit is forbidden too (no changes).
    "EXPLAIN": ["tool_call", "answer", "clarify"],
}

# Per-variant property/required specs for the TIGHT (oneOf) schema. Each entry is one
# discriminated-union branch: a `const` `type` discriminator + exactly that variant's
# own fields + `additionalProperties: False`, so a provider whose grammar enforces
# `oneOf` (measured: llama.cpp/TQP; Gemini deadlocks — see module docstring) makes
# cross-variant field bleed STRUCTURALLY impossible. `thought` is required on every
# variant. The `required` lists mirror the flat schema's per-variant guards in
# controller_loop.py and the OUTPUT block in CONTROLLER_SYSTEM_PROMPT.
_OBJECT = {"type": "object"}
_STR = {"type": "string"}
_PATCH_OP_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["op", "file", "reason"],  # mirror the flat schema's op-item guard
    "properties": {
        "op": {"type": "string", "enum": ["create_file", "search_replace"]},
        "file": _STR, "content": _STR, "search": _STR, "replace": _STR, "reason": _STR,
    },
}
_VARIANT_SPECS: dict[str, dict[str, object]] = {
    "tool_call": {
        "required": ["tool", "args"],
        "properties": {"tool": _STR, "args": _OBJECT},
    },
    "answer": {"required": ["answer"], "properties": {"answer": _STR}},
    "clarify": {"required": ["question"], "properties": {"question": _STR}},
    "propose_mode": {
        "required": ["plan_sketch", "recommended", "reason", "options"],
        "properties": {
            "plan_sketch": _STR, "recommended": _STR, "reason": _STR,
            "options": {"type": "array", "items": _OBJECT},
        },
    },
    "edit": {
        "required": ["patch_ops"],
        "properties": {"patch_ops": {"type": "array", "items": _PATCH_OP_ITEM}},
    },
    "submit_changes": {"required": ["summary"], "properties": {"summary": _STR}},
}


def _tight_variant_branch(variant: str) -> dict[str, object]:
    spec = _VARIANT_SPECS[variant]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "thought", *spec["required"]],  # type: ignore[misc]
        "properties": {
            "type": {"const": variant},
            "thought": _STR,
            **spec["properties"],  # type: ignore[dict-item]
        },
    }


def controller_response_schema(*, phase: str, tight: bool = False) -> dict[str, object]:
    """Return the controller response schema for a phase.

    `tight=False` (default, the universal fallback) → the FLAT schema with the `type`
    enum trimmed to the phase's allowed actions. `tight=True` → a discriminated-union
    (`oneOf`) of just those variants, each a closed object — use ONLY for providers
    whose grammar enforces `oneOf` (the engine gates this on `supports_oneof_grammar`).
    The flat path is deep-copied; the tight path builds fresh branch dicts per call
    (shared leaf type-schemas like `_STR` are read-only and never mutated).
    """
    if tight:
        return {"oneOf": [_tight_variant_branch(v) for v in _PHASE_TYPES[phase]]}
    schema = copy.deepcopy(CONTROLLER_RESPONSE_SCHEMA)
    schema["properties"]["type"]["enum"] = list(_PHASE_TYPES[phase])  # type: ignore[index]
    return schema


CONTROLLER_SYSTEM_PROMPT = """\
You are an agentic coding assistant in a chat turn. You own this turn's loop.
Each step, emit EXACTLY ONE JSON object (no prose, no markdown fences) matching the schema. The
"type" field selects a variant; EVERY field listed for that variant below is REQUIRED and must be
non-empty. A bare object like {"type":"answer"} or a tool_call with no "tool"/"args" is INVALID
and wastes a turn.

⚠ GROUND BEFORE YOU COMMIT — this is the difference between a correct turn and a confident wrong one:
Your retrieval seed (in the payload) is a map (file outlines + a few excerpts), NOT the full code. It tells you
WHERE things are; it does NOT contain most file bodies. If you answer or propose from the seed
alone for anything code-specific, you WILL confabulate the parts it doesn't contain (a wrong class
name, a wrong endpoint, a function that doesn't exist). The fix is cheap: READ the specific code
first.
  • LOCATE before you read: search_code / search_semantic / query_graph to find the exact file +
    line, THEN read_file the located region (use start_line/end_line on files >150 lines). Do not
    read_file blindly; do not re-issue an IDENTICAL call whose result you already have.
  • A code-specific question ("how does X work", "where is Y", "trace Z") REQUIRES reading the
    actual functions you will cite — outlines and line numbers are NOT enough to describe behavior.
    Cite only files/symbols you have READ this turn (or that appear verbatim in the seed excerpts);
    never describe a file you only saw as a bare name.
  • A purely conversational message you can fully answer without the repo (a greeting, a question
    about your own capabilities) may be answered directly — tools are not mandatory for those.
  • Stop exploring once further reads would not change your answer: when you can name the concrete
    files/functions AND have read the code behind your claims, commit.

WHEN THE REQUEST NEEDS A CHANGE — do NOT edit silently. First ground yourself (search/read the
EXISTING code you'll touch; a brand-new isolated file may need none), then emit type="propose_mode"
so the user picks HOW to proceed. Make "plan_sketch" CONCRETE (exact file path + function signature
+ how it integrates), NOT a restatement of the request. After the user picks "edit" you emit
type="edit" actions, then type="submit_changes" when done.

OUTPUT — choose exactly one variant per turn. ALL listed fields are REQUIRED and non-empty:

Variant — tool_call (explore): {type, thought, tool, args}
  "tool" is a tool name from AVAILABLE TOOLS; "args" is a NON-EMPTY object of that tool's params.
  Before mode selection, use ONLY read-only tools (search_code / read_file / list_directory /
  read_env_profile / search_semantic). run_command is NOT available yet — to change anything,
  emit propose_mode (never write files via the shell); run_command unlocks once editing starts.
  {"type":"tool_call","thought":"locate the chat route","tool":"search_code","args":{"pattern":"def .*message","path_filter":"*.py"}}
  {"type":"tool_call","thought":"read the handler","tool":"read_file","args":{"path":"services/agentd-py/agentd/api/routes.py","start_line":120,"end_line":200}}

Variant — answer (respond in text): {type, answer}
  The COMPLETE response goes in "answer" (self-contained, specific, cites files/functions you READ).
  Keep "thought" brief so your output lands in "answer". NEVER an empty or placeholder "answer".
  {"type":"answer","thought":"have read the route + loop","answer":"The message flow: `routes.py` ... "}

Variant — clarify (you genuinely cannot proceed): {type, question}
  Use when an ambiguity blocks you and reading the workspace won't resolve it. Never a blank answer.
  {"type":"clarify","thought":"ambiguous target","question":"Which pricing module — src/pricing.py or billing/pricing.py?"}

Variant — propose_mode (the request needs a change): {type, plan_sketch, reason, recommended, options}
  "recommended": EXACTLY one of edit | create_task | resume | explain.
  "options": list of {"mode": <edit|create_task|resume|explain>, "label": <short>, "description": <one line>}.
  Use the exact key "mode" (never "type") and only those four values. Normally offer "edit"
  (inline now, user accepts/rejects each edit), "create_task" (a reviewed step-by-step task), and
  "explain" (describe only).
  {"type":"propose_mode","thought":"small new file","plan_sketch":"Add clamp(x,lo,hi) to src/mathutil.py","reason":"single small new file","recommended":"edit","options":[
    {"mode":"edit","label":"Edit inline now","description":"I add the file directly; you review it."},
    {"mode":"create_task","label":"Plan it as a task","description":"Draft a plan you approve, then execute."},
    {"mode":"explain","label":"Just explain","description":"No changes — I describe the approach."}]}

Variant — edit (EDIT mode only, after the user picked "edit"): {type, patch_ops}
  "patch_ops" is a NON-EMPTY list. Each op: "file" is a WORKSPACE-RELATIVE PATH (one line like
  "src/tax.py") — NEVER put code in "file". Code goes in "content" (create_file) or "search"/"replace"
  (search_replace). EVERY op needs a one-line "reason". Prefer ONE op per turn for a search_replace
  (broad multi-op rewrites fail as a unit if any single search misses). Read the target region
  before editing existing code.
  {"type":"edit","thought":"add helper","patch_ops":[{"op":"create_file","file":"src/tax.py","content":"def with_tax(price, rate):\\n    return price * (1 + rate)\\n","reason":"add tax helper"}]}
  {"type":"edit","thought":"round price","patch_ops":[{"op":"search_replace","file":"src/pricing.py","search":"return total","replace":"return round(total, 2)","reason":"round price"}]}

Variant — submit_changes (EDIT mode, when all edits are done): {type, summary}
  "summary": a non-empty one-liner of what you changed. Emit this to END the edit turn.
  {"type":"submit_changes","thought":"done","summary":"Added with_tax() to src/tax.py and rounded the total in pricing.py."}

After an edit, prefer live tools (read_file/search_code) over the retrieval seed — your edit is
already on the real workspace. Available tools:
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
    (workspace/retrieval_seed) -> append-only conversation_history ->
    per-turn-varying fields LAST. NOTE: `goal` is the CURRENT turn's user message
    — it changes every turn, so it must live in the TAIL, not the head. Putting it
    first (the original bug) broke the cached prefix from the start of the user
    content every turn → measured cache_n=0 / full ~13k-token re-prefill per turn
    on TQP (smoke finding #13). The byte-identity unit test missed it because it
    compares the SAME turn across a restart, never consecutive turns.
    """
    payload: dict[str, object] = {
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
    # TAIL (per-turn-varying): the current request + instruction + budget. Placed
    # AFTER the append-only history so the multi-k-token prefix stays cache-stable.
    payload["goal"] = plan_context.get("goal", "")
    # Per-turn steering, mirroring build_planning_step_payload's reflect-then-choose
    # scaffold: first-turn anchoring (don't commit cold), mid-turn reflect→(explore|commit),
    # and a final-step "land it now" warning. Phase-aware (DECIDE vs EDIT). This — not the
    # static system prompt — is what stops the iter=0 cold answer and the endless thrash.
    has_query_graph = any(t.get("name") == "query_graph" for t in tool_definitions)
    _graph = "/query_graph" if has_query_graph else ""
    final_call = iteration >= max_iters - 1
    if phase == "EDIT":
        if not history:
            hint = (
                "EDIT mode. Read the target region of any EXISTING file before you change it "
                f"(search_code{_graph} → read_file); a brand-new file needs no read. Emit "
                "type='edit' to make a change, then type='submit_changes' when all edits are done."
            )
        elif final_call:
            hint = (
                "⚠ FINAL STEP: emit type='submit_changes' now (a non-empty summary) to end the "
                "turn — or type='clarify' if a true blocker remains. No more edits after this."
            )
        else:
            hint = (
                "FIRST reflect on your last edit's result: did it apply ('applied+promoted') or "
                "fail ('PATCH FAILED: …')? THEN choose ONE: (A) CONTINUE/FIX — if it failed, "
                "re-read the exact lines and re-emit ONE corrected op (do NOT repeat the failed "
                "op verbatim); if more changes remain, emit the next type='edit'. (B) DONE — emit "
                "type='submit_changes' with a summary. A genuine, read-resistant blocker → "
                "type='clarify'. Do NOT propose_mode again."
            )
    else:  # DECIDE
        if not history:
            hint = (
                "Plan your first move. For a code-specific request (how/where/trace, or a change) "
                f"your FIRST action must LOCATE the code: call search_code/search_semantic{_graph} "
                "— do NOT answer or propose_mode cold from the seed (it lacks most file bodies; "
                "answering from it confabulates). Answer directly ONLY if this is a purely "
                "conversational message needing no repo access."
            )
        elif final_call:
            hint = (
                "⚠ FINAL STEP: exploration budget is spent. Commit now — type='answer' (complete, "
                "citing what you READ), type='propose_mode' (for a change), or type='clarify'. "
                "No more tool calls."
            )
        else:
            hint = (
                "FIRST reflect: which files/functions can you cite from code you ACTUALLY opened "
                "this turn, and is anything material still unread? THEN choose ONE: (A) READ MORE "
                "— if any claim you'd make rests on a file you haven't opened, locate it "
                f"(search{_graph}) and read that region; never re-issue an identical call. "
                "(B) COMMIT — if you've read the code behind every claim, emit type='answer' "
                "(complete, non-empty, in the 'answer' field) or type='propose_mode' for a change. "
                "Neither is penalized — pick what your reflection supports."
            )
    payload["instruction"] = f"Phase={phase}. {hint} ({iteration} of {max_iters} steps used.)"
    payload["budget_status"] = f"{iteration}/{max_iters} steps used"  # LAST (varies every turn)
    return payload
