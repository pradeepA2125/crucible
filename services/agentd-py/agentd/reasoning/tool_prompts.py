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
    - Never claim verified=true without actually running the checks
    - Use the "testing_strategy" and "test_command hint" from the patch-apply message to
      discover what to run. If both are absent and the step only touches non-executable
      files (docs, config, assets), you may emit verify_done(verified=true) with an
      explanation in test_output instead of running checks.

BINARY DISCOVERY (verify phase only):

run_command auto-resolves naked binary names against the real workspace's
.venv/bin and node_modules/.bin (where setup_env installs them) — try the
direct command first. CWD is the shadow so your patched files are tested.

When run_command fails with "not found":
  1. find_binary <name>      — probes workspace bins, then PATH; on miss it appends
                                an "AGENT SHOULD: setup_env <cmd>" hint — follow it.
  2. If found:  run_command <name> ...   (or use the full path returned)
  3. If not found and you have an existing manifest: setup_env "<pm sync command>"
  4. If not found and the workspace is bare:  init_workspace + setup_env

WORKSPACE BOOTSTRAPPING:

Bare workspace (no manifest files) — use init_workspace, NOT hand-written manifests:
  init_workspace ecosystem=python dev_deps=["pytest"]
  init_workspace ecosystem=node   dev_deps=["vitest"]
  init_workspace ecosystem=rust   dev_deps=[]
  init_workspace ecosystem=go     dev_deps=[]
init_workspace emits the smallest valid manifest with EXACTLY the deps you list —
no extras. Then call setup_env to install. Refuses to overwrite existing manifests.

Existing workspace — list_directory(".") to detect, then setup_env directly:
  uv.lock / pyproject.toml only / requirements*.txt -> setup_env "uv sync"
                                                       (uv missing -> auto-fallback to
                                                        python3 -m venv + pip; transparent)
  poetry.lock           -> setup_env "poetry install"  (no fallback — needs poetry)
  package-lock.json     -> setup_env "npm ci"
  yarn.lock             -> setup_env "yarn install --frozen-lockfile"
  pnpm-lock.yaml        -> setup_env "pnpm install --frozen-lockfile"
  Cargo.toml            -> cargo must be on PATH (no auto-install); if a component
                           is missing, setup_env "rustup component add <name>"
  go.mod                -> setup_env "go mod download"  (go must be on PATH)

If setup_env returns "AGENT SHOULD: setup_env \"<alt-pm>\"" — follow it (alternate PM).
If setup_env returns "AGENT SHOULD: emit revision_needed" — emit revision_needed,
do NOT retry; the toolchain is genuinely missing and only the user can install it.

setup_env reads YOUR patched files in the shadow workspace. If you added a dep via
emit_patch (or init_workspace), the very next setup_env call sees it.

Concrete example (bare Python workspace):
  list_directory(".")           -> src/  (no manifest, no .venv)
  init_workspace ecosystem=python dev_deps=["pytest"]
                                -> Created pyproject.toml with 1 dep
  setup_env "uv sync"           -> if uv on PATH: uv installs into /real/.venv
                                   if uv missing: note: bootstrapped via python3 + pip
  run_command pytest tests/     -> auto-resolves /real/.venv/bin/pytest -> 1 passed
  verify_done verified=true

Concrete example (cargo missing — non-recoverable):
  list_directory(".")           -> Cargo.toml, src/main.rs
  run_command cargo test        -> Error: 'cargo' not found on PATH
  setup_env "cargo build"       -> Error: 'cargo' not found on PATH. Cannot bootstrap automatically.
                                   Install: https://rustup.rs
                                   AGENT SHOULD: emit revision_needed citing missing toolchain 'cargo'.
  revision_needed               -> reason="missing rust toolchain", evidence="cargo not on PATH"

PRIOR STEP FILES:

The "prior_step_files" field in your request context lists paths already created or
modified by earlier steps in this task. These files exist in your working copy but NOT
in the original workspace — so read_file and list_directory will not show them.

Rules:
- NEVER emit create_file for a path in prior_step_files — it already exists.
- To modify one: use search_replace or apply_diff.
- To read one: use run_command with "cat <path>".
- run_command and pytest already see these files when they execute.

SCOPE VIOLATIONS:
ALWAYS emit_patch first — even when you need files outside your targets.
The system automatically approves small conventional files (__init__.py,
conftest.py) and will extend your scope without interrupting execution.
Never skip the patch attempt because you anticipate a scope issue.

If your patch is rejected and the system explicitly denies scope extension:
  - Implement within your allowed files if possible.
  - Otherwise emit revision_needed with the missing file and why it is required.

revision_needed is a LAST RESORT — only after an explicit denial, never preemptive.

OUTPUT — choose exactly one variant per turn:

Variant 1 — call a tool (required fields: type, thought, tool, args):
  {{"type": "tool_call", "thought": "<1-3 sentence reasoning>", "tool": "<tool_name>", "args": {{<tool args>}}}}

Variant 2 — emit patch ops (required fields: type, thought, patch_ops):
  {{"type": "emit_patch", "thought": "<final reasoning>", "patch_ops": [{{<patch op>}}, ...]}}

Variant 3 — signal plan error (required fields: type, thought, reason, evidence, affected_steps):
  {{"type": "revision_needed", "thought": "...", "reason": "...", "evidence": "...", "affected_steps": [...]}}
  Use ONLY after scope extension was explicitly denied or the plan targets are fundamentally wrong.

Variant 4 — signal verify complete (required fields: type, thought, verified, test_output):
  {{"type": "verify_done", "thought": "...", "verified": true, "test_output": "full pytest or linter output"}}
  Use after ALL linters and tests pass. For non-executable files (docs/config/assets) with no
  testing_strategy, you may emit this immediately with test_output explaining why no checks ran.
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
