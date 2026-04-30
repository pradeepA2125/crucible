# Verify-Phase Environment Discovery Design

**Date:** 2026-04-30
**Phase:** 6 — Agentic Verify Loop
**Status:** Approved for implementation

---

## Problem

The current tool loop exits as soon as the agent emits `emit_patch`. Validation (mypy, ruff, pytest) is handled by the engine via `_run_fast_validation` and `_run_step_test_command` — static, outside the agent's control. This means:

1. The agent cannot react to test failures and self-correct in the same step
2. The engine hardcodes `.venv` path assumptions that break on poetry, conda, pyenv, pipenv, and non-standard layouts
3. Validation and testing are sequential in the engine; the agent has no feedback loop

This spec describes replacing all per-step engine-side validation with an agent-owned **verify phase** that runs inside the tool loop after `emit_patch`.

---

## Decisions Made

| Question | Decision |
|----------|----------|
| Where does the verify phase live? | Inside `ToolLoop` — `emit_patch` is a phase checkpoint, not an exit |
| Who owns static analysis (mypy, ruff, tsc)? | Agent — runs in verify phase via `run_command` |
| Who owns runtime tests (pytest, cargo test)? | Agent — same |
| Where does env get installed? | Real workspace (permanent) — agent installs via `setup_env` |
| Who discovers the package manager? | Agent — uses `list_directory` + `find_binary` tools |
| How does `setup_env` read patched dep files? | Runs with `cwd=shadow_root`; package manager reads shadow's files |
| How does agent find pre-existing binaries? | `find_binary` tool — searches real filesystem, not sandboxed to shadow |
| Split in engine code path (PatchResult vs VerifyResult)? | No — `PatchResult` removed; engine always handles `VerifyResult` |
| Per-step engine fast validation? | Removed — full-task `VALIDATING` state remains as safety net |

---

## Architecture

### Action Type Changes

`AGENT_STEP_RESPONSE_SCHEMA` gains a fourth variant. The `type` enum becomes:

```
"tool_call" | "emit_patch" | "verify_done" | "revision_needed"
```

New `verify_done` fields added to the flat schema:

```python
"verified": {
    "type": "boolean",
    "description": "True when all linters and tests passed (required for verify_done)",
},
"test_output": {
    "type": "string",
    "description": "Full output from the last test/lint run (required for verify_done)",
},
```

### Return Type Simplification

`PatchResult` is removed. `ToolLoop.run()` returns:

```python
StepOutcome = VerifyResult | PlanHandoff
```

```python
@dataclass
class VerifyResult:
    patch_document: dict[str, object]   # what was applied (for artifact writing)
    touched_files: list[str]            # relative paths modified (for task.modified_files)
    verified: bool
    test_output: str                    # empty string when no test_command
    tool_trace: AgentToolTrace
```

`touched_files` is extracted from the `file` field of each patch op across all `emit_patch` calls during the step (accumulates across corrections). `patch_document` holds the ops from the final `emit_patch` (the correction supersedes prior ops on the same files).

### Budget

`TaskBudget` gains:

```python
max_verify_calls_per_step: int = 4
```

Explore budget (`max_tool_calls_per_step`) and verify budget (`max_verify_calls_per_step`) are tracked separately inside `ToolLoop`.

---

## ToolLoop — Two-Phase Mechanics

### Phase 1: Explore

- Valid actions: `tool_call`, `emit_patch`, `revision_needed`
- Budget: `max_tool_calls_per_step` (default 8)
- On `emit_patch`:
  1. Call `patch_engine.apply(shadow_path, patch_ops)` inline
  2. If apply fails → inject `"Patch failed: {reason}"` into history; continue loop (agent sees error and can fix search strings immediately, no retry round-trip)
  3. If apply succeeds → record `touched_files`; switch `_phase = "verify"`; inject phase transition message into history; continue loop
- On `revision_needed` → return `PlanHandoff`
- Budget exhausted before any `emit_patch` → raise `ToolBudgetExceededError` (engine retries as today)

### Phase 2: Verify

- Valid actions: `tool_call`, `verify_done`, `emit_patch` (correction)
- Budget: `max_verify_calls_per_step` (default 4)
- On `emit_patch` again → apply correction on top of previous patch (incremental); agent uses `search_replace` to undo and redo — agent is responsible for correct correction ops
- On `verify_done` → return `VerifyResult`
- Budget exhausted → return `VerifyResult(verified=False, test_output="Verify budget exhausted after N calls")` — NOT an exception; engine retries with test output as `last_failure`

### No test_command shortcut

When `step.test_command is None`: after the first `emit_patch` applies successfully, ToolLoop immediately returns `VerifyResult(verified=True, test_output="", ...)` without entering verify phase. No extra LLM call. No verify budget consumed.

### Constructor changes

```python
class ToolLoop:
    def __init__(
        self,
        reasoning_engine: ReasoningEngine,
        registry: ToolRegistry,           # gains real_workspace_path
        broadcaster: PatchEventBroadcaster,
        task_id: str,
        patch_engine: PatchEngine,        # NEW — for inline apply
        shadow_path: Path,                # NEW
    ) -> None: ...
```

When transitioning to verify phase, `ToolLoop` creates a second `ToolRegistry` instance with `phase="verify"` to expose `setup_env` and regenerates `tool_defs`. The explore-phase registry (without `setup_env`) is used for all turns before `emit_patch` fires.

---

## New Tools

### `list_directory` (added to `ToolRegistry`)

Already implemented in `PlanningToolRegistry` — extract implementation to `agentd/tools/files.py` and reuse in both registries.

```
name: list_directory
description: List files and directories at a path in the workspace.
             Use to detect lockfiles (uv.lock, package-lock.json) at project
             root, or check if a binary exists (.venv/bin/pytest,
             node_modules/.bin/vitest).
parameters:
  path (required): relative path to list, e.g. "." or ".venv/bin"
```

Returns one line per entry: `file  pytest` or `dir   __pycache__`. Capped at 200 entries. Path traversal rejected (must stay within shadow_root).

### `setup_env` (new file: `agentd/tools/env.py`)

Runs with `cwd=shadow_root` so it reads the agent's patched dependency files. Installs to the real workspace's env (permanent).

```
name: setup_env
description: Install or sync declared dependencies into the real workspace.
             Reads dependency files from YOUR patched workspace (shadow).
             Any dependency you added via emit_patch will be picked up.
             Installs binaries permanently to the real workspace's .venv or
             node_modules. Call ONLY when find_binary confirms binary is absent.
parameters:
  command (required): full command string. Allowed:
    "uv sync"
    "pip install -r requirements.txt"
    "pip install -r requirements-dev.txt"
    "npm ci"
    "yarn install --frozen-lockfile"
    "pnpm install --frozen-lockfile"
    "cargo build"
    "go mod download"
    "poetry install"
```

Implementation per package manager:

| Binary | cwd | Real-workspace targeting |
|--------|-----|--------------------------|
| `uv` | `shadow_root` | `UV_PROJECT_ENVIRONMENT={real_workspace}/.venv` env var |
| `pip3`/`pip` | `shadow_root` | use `{real_workspace}/.venv/bin/pip3` as binary if exists |
| `npm` | `shadow_root` | `npm_config_prefix={real_workspace}` env var |
| `yarn` | `shadow_root` | `--modules-dir {real_workspace}/node_modules` injected |
| `pnpm` | `shadow_root` | `--modules-dir {real_workspace}/node_modules` injected |
| `cargo` / `go` | `shadow_root` | global cache; no real-workspace targeting needed |

Allowlist validation: first word of command must be in `SETUP_ENV_BINARIES = {"uv", "pip3", "pip", "npm", "yarn", "pnpm", "cargo", "go", "poetry"}`. Timeout: 300s.

Only exposed in the verify phase — `ToolRegistry.definitions()` omits `setup_env` when `phase="explore"`.

### `find_binary` (new, in `agentd/tools/env.py`)

NOT sandboxed to shadow — it searches the real filesystem for executable paths.

```
name: find_binary
description: Locate an executable binary in the real workspace or on system PATH.
             Use when run_command fails with "not found".
             Returns full paths to all matches ranked by proximity to workspace root.
parameters:
  name (required): binary name to find, e.g. "pytest", "vitest", "cargo"
```

Implementation:
1. Run `which {name}` (system PATH lookup)
2. Run `find {real_workspace} -name {name} -maxdepth 6 -type f` (workspace-local envs)
3. Return all found paths, deduplicated, sorted by path depth (shallowest first)

### `run_command` — full path support

Allowlist check updated from exact name match to basename match:

```python
binary_name = Path(command).name   # "pytest" from "/home/user/.venv/bin/pytest"
if binary_name not in allowlist:
    return error
```

This allows the agent to use full paths returned by `find_binary` without allowlist bypass.

### `ToolRegistry` constructor change

```python
class ToolRegistry:
    def __init__(
        self,
        shadow_root: Path,
        real_workspace_path: Path,         # NEW
        semantic_index: object | None = None,
        phase: Literal["explore", "verify"] = "explore",  # NEW
    ) -> None: ...
```

The `phase` parameter gates `setup_env` visibility in `definitions()`.

---

## ShadowWorkspaceManager — Env Symlinks

`prepare()` is extended to symlink pre-existing real workspace envs into the shadow, giving the agent natural visibility into available binaries:

```python
# After shutil.copytree() in prepare():
for env_dir in (".venv", "venv", "env", "node_modules"):
    real_env = real_path / env_dir
    shadow_env = shadow_path / env_dir
    if real_env.exists() and not shadow_env.exists():
        shadow_env.symlink_to(real_env)
```

Effect:
- `list_directory(".venv/bin")` → agent sees real workspace's installed binaries
- If binary exists → agent skips `setup_env`
- If binary missing → agent calls `setup_env` → installs to real workspace → symlink reflects update automatically
- `run_command {shadow}/.venv/bin/pytest` → resolves through symlink to real binary

**Fallback for freshly-created envs** (no symlink at prepare time): `find_binary` searches the real workspace and returns the full path. Agent uses full path in `run_command`.

Promote is unaffected — it only copies `task.modified_files`, never `.venv` or `node_modules`.

---

## Engine Contract Changes

### `_run_step_with_retries` — before/after

**Deleted methods:**
- `_run_fast_validation` (per-step call)
- `_run_step_test_command`
- `_build_test_env`
- `_extract_path_from_test_command`
- `_merge_validation_results`

**Checkpoint timing shift:** checkpoint is taken **before** `tool_loop.run()` each attempt (not after), since the patch is applied inside the loop.

**New structure:**

```python
for attempt in range(max_attempts):
    await self._checkpoint_shadow(step, shadow_workspace)  # BEFORE run()

    try:
        outcome = await tool_loop.run(
            step, patch_request_context, budget, usage
        )
    except ToolBudgetExceededError as exc:
        # Explore budget exhausted — agent couldn't form a patch
        last_failure = str(exc)
        await self._restore_checkpoint(step, shadow_workspace)
        patch_request_context["last_failure"] = last_failure
        continue

    match outcome:
        case PlanHandoff():
            return outcome   # delta replan — handled by caller

        case VerifyResult() if not outcome.verified:
            # Verify phase failed — retry with test output as context
            last_failure = outcome.test_output or "Verify phase failed"
            await self._restore_checkpoint(step, shadow_workspace)
            patch_request_context["last_failure"] = last_failure
            continue

        case VerifyResult():
            # Success
            task.modified_files.extend(outcome.touched_files)
            await self._write_step_artifact(step, attempt, outcome)
            return outcome
```

**Two distinct failure signals:**

| Failure | `last_failure` content | Meaning |
|---------|----------------------|---------|
| `ToolBudgetExceededError` | "Explore budget exhausted after N calls" | Agent couldn't form a patch |
| `VerifyResult(verified=False)` | Full test/linter output from agent | Agent patched but checks failed |

The full-task `VALIDATING` state (runs after all steps) is unchanged — it remains the definitive safety net.

---

## System Prompt Changes (`reasoning/tool_prompts.py`)

### New `verify_done` variant in OUTPUT section

```
Variant 4 — signal verify complete (required: type, thought, verified, test_output):
  {"type": "verify_done", "thought": "...", "verified": true,
   "test_output": "full pytest or linter output"}
  Use after ALL linters and tests pass. Or immediately if no test_command is set.
```

### New EXECUTION PHASES section

```
EXECUTION PHASES:

Phase 1 — EXPLORE & PATCH
  Gather context with tools, emit_patch when confident.
  After your patch is applied you will automatically enter Phase 2.

Phase 2 — VERIFY
  You will be notified in the conversation when Phase 2 begins.
  Required sequence:
    1. Run static analysis first (fast): ruff check, mypy, tsc --noEmit, cargo check
    2. Run tests: pytest, cargo test, vitest, npm test
    3. If any check fails: emit another emit_patch to correct, then re-run
    4. When all pass: emit verify_done with verified=true and full test_output

  Rules:
    - You MUST run at least one linter AND one test command before verify_done(verified=true)
    - If this step has no test_command, emit verify_done(verified=true) immediately
    - Never claim verified=true without actually running the checks
```

### New BINARY DISCOVERY section

```
BINARY DISCOVERY (verify phase only):

When run_command fails with "not found":
  1. find_binary <name>               → returns full paths in real workspace
  2. If found: run_command <full-path> <args>  (full paths to known binaries allowed)
  3. If not found: detect package manager, call setup_env, then retry

Package manager detection — list_directory(".") first:
  uv.lock              → setup_env: "uv sync"
  poetry.lock          → setup_env: "poetry install"
  requirements*.txt    → setup_env: "pip install -r requirements.txt"
  pyproject.toml only  → setup_env: "uv sync"
  package-lock.json    → setup_env: "npm ci"
  yarn.lock            → setup_env: "yarn install --frozen-lockfile"
  pnpm-lock.yaml       → setup_env: "pnpm install --frozen-lockfile"
  Cargo.toml           → cargo is always available, no setup needed
  go.mod               → setup_env: "go mod download"

IMPORTANT: setup_env reads YOUR patched files (shadow workspace), not the
original. If you added a dependency via emit_patch, call setup_env immediately
after — it reads your patched pyproject.toml/package.json.

When a dependency is missing from the project file:
  1. emit_patch  → add the dep to pyproject.toml / package.json
  2. setup_env   → reads your patched file, installs to real env
  3. find_binary → confirm the binary is now present
  4. run_command → run the test

Concrete example (Python/uv, pytest missing):

  list_directory(".")
  → pyproject.toml, uv.lock, src/, tests/       ← uv.lock → use uv

  run_command pytest tests/test_foo.py
  → Error: pytest not found on PATH

  find_binary pytest
  → not found in real workspace

  emit_patch
  → add "pytest>=8" to pyproject.toml dev-dependencies

  setup_env "uv sync"
  → runs: cwd=/tmp/shadow/task-xyz/  ← reads YOUR patched pyproject.toml
           UV_PROJECT_ENVIRONMENT=/real/workspace/.venv
           $ uv sync                 ← installs pytest into real .venv

  find_binary pytest
  → found: /real/workspace/.venv/bin/pytest

  run_command /real/workspace/.venv/bin/pytest tests/test_foo.py
  → 1 passed

  verify_done verified=true test_output="1 passed"
```

### Phase transition message injected into history

When ToolLoop transitions to verify phase after successful `emit_patch`:

```python
history.append({
    "role": "system",
    "content": (
        "Patch applied successfully.\n"
        "You are now in VERIFY phase.\n"
        f"test_command hint: {step.test_command or '(none — emit verify_done immediately)'}\n"
        "Run linters and tests. Emit verify_done when all checks pass, "
        "or emit_patch again to correct failures."
    )
})
```

---

## Files Changed

| File | Change |
|------|--------|
| `agentd/domain/models.py` | Add `max_verify_calls_per_step: int = 4` to `TaskBudget` |
| `agentd/reasoning/tool_prompts.py` | Add `verify_done` to schema; add PHASES + BINARY DISCOVERY sections to prompt |
| `agentd/tools/loop.py` | Two-phase logic; inline `patch_engine.apply()`; `VerifyResult` return; remove `PatchResult` |
| `agentd/tools/registry.py` | Add `real_workspace_path`, `phase` param; add `list_directory`, `setup_env`, `find_binary`; update `run_command` allowlist check to basename |
| `agentd/tools/files.py` | Extract `list_directory` implementation (shared with PlanningToolRegistry) |
| `agentd/tools/env.py` | New — `setup_env` and `find_binary` implementations |
| `agentd/workspace/shadow.py` | Symlink `.venv`, `venv`, `env`, `node_modules` in `prepare()` |
| `agentd/orchestrator/engine.py` | Remove 5 validation methods; checkpoint before `tool_loop.run()`; handle `VerifyResult \| PlanHandoff`; pass `patch_engine` + `shadow_path` to `ToolLoop`; pass `real_workspace_path` to `ToolRegistry` |
| `agentd/planning/registry.py` | Use shared `list_directory` from `tools/files.py` |

### No new files needed (beyond `tools/env.py`)

---

## Verification

1. **Binary already present** — task on pydantic workspace (has `.venv` + pytest): agent calls `list_directory(".venv/bin")` via symlink, sees pytest, skips `setup_env`, runs tests directly.
2. **Binary missing, declared** — bare workspace with `pyproject.toml` listing pytest but no `.venv`: agent detects `uv.lock`, calls `setup_env "uv sync"`, `find_binary pytest` finds it, tests run.
3. **Binary missing, undeclared** — workspace with no pytest in `pyproject.toml`: agent adds it via correction patch, calls `setup_env "uv sync"`, tests run.
4. **Non-standard venv path** — env at `venv/` not `.venv/`: symlink created for both; `find_binary` finds binary regardless.
5. **Node workspace** — `package-lock.json` present: agent detects npm, calls `setup_env "npm ci"`, `find_binary vitest` returns path, tests run.
6. **Rust** — `cargo` always on PATH: agent skips `setup_env`, runs `cargo check` + `cargo test` directly.
7. **Verify budget exhausted** — agent runs 4 verify calls without passing: returns `VerifyResult(verified=False)`; engine retries with test output as `last_failure`.
8. **Explore budget exhausted** — agent uses 8 tool calls without emitting patch: `ToolBudgetExceededError`; engine retries as today.
9. **Patch inline failure** — agent emits `emit_patch` with wrong search string: error injected into history, agent corrects and re-emits within same attempt.
10. **No test_command** — agent emits `emit_patch`, ToolLoop immediately returns `VerifyResult(verified=True)` without entering verify phase; no extra LLM call.
11. **Engine code path** — no branching on PatchResult vs VerifyResult; engine always handles `VerifyResult`.
12. **Deleted methods** — `grep -r "_run_fast_validation\|_run_step_test_command\|_build_test_env\|_extract_path_from_test_command\|_merge_validation_results" services/agentd-py/` returns zero results.
