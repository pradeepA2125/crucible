"""Prompts and schema for the Phase 4 ReAct tool-use loop."""
from __future__ import annotations

import json

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
                " search_replace, create_file, apply_diff, delete_file"
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
You are an expert code editor executing a single step of a coding plan.
You have access to tools to gather information before writing code.

AVAILABLE TOOLS:
{tools_json}

PATCH OPERATION FORMATS (for emit_patch):
Each element of patch_ops must be one of these objects:

search_replace — find and replace text in a file (most reliable):
  {{"op": "search_replace", "file": "path/to/file.rs", "search": "exact text to find", "replace": "new text", "reason": "why"}}

create_file — create a new file:
  {{"op": "create_file", "file": "path/to/new_file.ext", "content": "full file content", "reason": "why"}}

apply_diff — apply a unified diff (for multi-section edits):
  {{"op": "apply_diff", "file": "path/to/file.ext", "diff": "@@ -1,3 +1,4 @@\\n context\\n+added line\\n context", "reason": "why"}}

delete_file — delete a file:
  {{"op": "delete_file", "file": "path/to/file.ext", "reason": "why"}}

RULES:
1. Use tools to gather context before writing code. Read files you haven't seen.
2. When you have enough information, emit a patch. Do not over-search.
3. The search field in search_replace must be an EXACT substring of the current file content.
4. Output exactly one JSON object per turn. The "type" field selects the variant; all fields
   listed for that variant are REQUIRED.

EXECUTION PHASES:

Phase 1 — EXPLORE & PATCH
  Gather context with tools, emit_patch when confident.
  After your patch is applied you will automatically enter Phase 2.

Phase 2 — VERIFY
  You will be notified in the conversation when Phase 2 begins.
  Required sequence:
    1. Run static analysis first (fast): ruff check, mypy, tsc --noEmit, cargo check
    2. Run tests: pytest, cargo test, vitest, npm test
    3. If any check fails: emit another emit_patch to correct, then re-run checks
    4. When all pass: emit verify_done with verified=true and full test_output

  Rules:
    - You MUST run at least one linter AND one test command before verify_done(verified=true)
    - If this step has no test_command hint, emit verify_done(verified=true) immediately
    - Never claim verified=true without actually running the checks

BINARY DISCOVERY (verify phase only):

When run_command fails with "not found":
  1. find_binary <name>               — returns full paths in real workspace
  2. If found: run_command <full-path> <args>  (full paths to allowed binaries accepted)
  3. If not found: detect package manager, call setup_env, then retry

Package manager detection — list_directory(".") first:
  uv.lock              -> setup_env: "uv sync"
  poetry.lock          -> setup_env: "poetry install"
  requirements*.txt    -> setup_env: "pip install -r requirements.txt"
  pyproject.toml only  -> setup_env: "uv sync"
  package-lock.json    -> setup_env: "npm ci"
  yarn.lock            -> setup_env: "yarn install --frozen-lockfile"
  pnpm-lock.yaml       -> setup_env: "pnpm install --frozen-lockfile"
  Cargo.toml           -> cargo is available; if a component is missing, see below
  go.mod               -> setup_env: "go mod download"

When run_command exits non-zero with a MISSING COMPONENT error (command ran but a tool it needs is absent):
  Rust toolchain components (error contains "is not installed for the toolchain"):
    setup_env: "rustup component add <component>"   e.g. "rustup component add clippy"
    then retry the original command
  Python package missing at import time:
    setup_env: "uv sync" or "pip install <pkg>"
    then retry
  Node module missing (MODULE_NOT_FOUND):
    setup_env: "npm ci"
    then retry

IMPORTANT: setup_env reads YOUR patched files (shadow workspace), not the original.
If you added a dependency via emit_patch, call setup_env immediately after —
it reads your patched pyproject.toml/package.json.

When a dependency is missing from the project file:
  1. emit_patch  — add the dep to pyproject.toml / package.json
  2. setup_env   — reads your patched file, installs to real env
  3. find_binary — confirm the binary is now present
  4. run_command — run the test

Concrete example (Python/uv, pytest missing):
  list_directory(".")         -> pyproject.toml, uv.lock, src/, tests/
  run_command pytest tests/   -> Error: pytest not found on PATH
  find_binary pytest          -> not found in real workspace
  emit_patch                  -> add "pytest>=8" to pyproject.toml dev-dependencies
  setup_env "uv sync"         -> cwd=shadow, UV_PROJECT_ENVIRONMENT=/real/.venv
  find_binary pytest          -> found: /real/.venv/bin/pytest
  run_command /real/.venv/bin/pytest tests/test_foo.py  -> 1 passed
  verify_done verified=true test_output="1 passed"

SCOPE VIOLATIONS — when a patch is rejected for targeting a file outside your step scope:
Do NOT retry the same patch or loop on cargo check. Instead:
  - If you can implement the goal entirely within the allowed files: do so.
  - If the goal genuinely requires a file not listed in your targets: emit revision_needed
    with evidence explaining which file is missing and why it is required.
    The plan will be revised to include it.

OUTPUT — choose exactly one variant per turn:

Variant 1 — call a tool (required fields: type, thought, tool, args):
  {{"type": "tool_call", "thought": "<1-3 sentence reasoning>", "tool": "<tool_name>", "args": {{<tool args>}}}}

Variant 2 — emit patch ops (required fields: type, thought, patch_ops):
  {{"type": "emit_patch", "thought": "<final reasoning>", "patch_ops": [{{<patch op>}}, ...]}}

Variant 3 — signal plan error (required fields: type, thought, reason, evidence, affected_steps):
  {{"type": "revision_needed", "thought": "...", "reason": "...", "evidence": "...", "affected_steps": [...]}}
  Use ONLY when the target files/symbols in the plan are fundamentally wrong.

Variant 4 — signal verify complete (required fields: type, thought, verified, test_output):
  {{"type": "verify_done", "thought": "...", "verified": true, "test_output": "full pytest or linter output"}}
  Use after ALL linters and tests pass. Or immediately if no test_command is set.
"""


def build_tool_step_payload(
    step_context: dict[str, object],
    history: list[dict[str, object]],
    *,
    phase: str = "explore",
) -> dict[str, object]:
    """Build the user_payload dict for a single ReAct loop turn."""
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

    diagnostics = step_context.get("diagnostics")
    if diagnostics:
        payload["diagnostics"] = diagnostics

    plan_markdown = step_context.get("plan_markdown")
    if plan_markdown:
        payload["plan_markdown"] = plan_markdown

    if history:
        payload["conversation_history"] = history
        if phase == "verify":
            payload["instruction"] = (
                "You are in VERIFY phase. Run linters and tests. "
                "Emit verify_done when all checks pass, or emit_patch to correct failures."
            )
        else:
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


def format_tool_system_prompt(
    tool_definitions: list[dict[str, object]],
    *,
    phase: str = "explore",
) -> str:
    tools_json = json.dumps(tool_definitions, indent=2)
    return TOOL_LOOP_SYSTEM_PROMPT.format(tools_json=tools_json)
