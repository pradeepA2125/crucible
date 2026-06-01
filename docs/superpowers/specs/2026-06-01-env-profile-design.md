# Workspace-level env profile

**Date**: 2026-06-01
**Status**: Draft — pending user review
**Author**: pradeepkumar, with Claude (Opus 4.7)

## Summary

Replace the ad-hoc env-discovery scattered across the verify phase with a single
**workspace-level env profile** built once on workspace registration (or first
task on a workspace) and persisted at `<workspace>/.agentd/env_profile.json`.
The agent reads it via a new `read_env_profile` tool to learn the package
manager, interpreter path, test command, and install command per ecosystem.
Mid-task manifest edits trigger an automatic re-install before the next
`run_command`. There is no new task status and no env state machine.

This matches the pattern used by Claude Code, Cursor, and Devin: detect-and-use
an existing env; encode conventions in a config the agent reads; bypass shell
state loss via direct interpreter paths.

## Background

### Failure modes observed in current code (verify-phase env discovery)

The current `find_binary` / `setup_env` / `init_workspace` tools work, but the
agent has to compose them from inside the verify-phase tool loop. Failure modes
observed in stress runs:

- **Detection**: `pytest` runs (found on system PATH), test exits with
  `ModuleNotFoundError`. Agent reads as code bug, not "wrong python".
- **Diagnosis**: `uv sync` fails with setuptools "Multiple top-level packages
  discovered". Fix is a manifest stanza, not a different PM. Agent retries the
  same command.
- **Toolset gaps** (since fixed in-session): `run_command` ignored `cwd`;
  `setup_env` rejected `cd subdir && uv sync` and had no `cwd` parameter.
- **Order-of-operations**: `find_binary` returns system python; no signal that
  a `.venv` should exist for this project.
- **Repeated attempts**: across s1-1, s1-2, s1-3, agent variants similar things.
  SM dedup helps but doesn't redirect to env work.
- **State loss across resumes**: parent shadow has partial setup state; child
  resume re-clones and rediscovers from scratch.

### Industry pattern (web research, 2026-06-01)

| Tool | Approach |
|---|---|
| Claude Code | Auto-detects existing `.venv`. Recommends direct interpreter path; shell tool-call state doesn't persist. Conventions in `CLAUDE.md`. |
| Cursor | Editor detects `.venv`. Agent learns PM (uv vs pip) from `.cursor/rules/`. Same fresh-shell problem; same workaround. |
| Aider | Installs itself in isolated env; surfaces missing-dep errors clearly. Does not perform agentic env setup for the target project. |
| Devin | Explicit setup wizard at repo onboard (8 steps); commands saved to config; per-task VMs from snapshot. `.bashrc` carries persistent state. |

Common pattern: **one-shot setup at workspace registration**, conventions in a
config file, direct-interpreter-path execution, no runtime env state machine.

## Decision history

Recorded brainstorm answers (sequence: 2026-06-01):

| Question | Answer |
|---|---|
| When does env-setup act? | Both preflight + reactive top-up |
| Authority | Hybrid — deterministic probe → env_profile → LLM consumer |
| Scope unit | Per ecosystem, plan-target-scoped |
| Reactive trigger | LLM emits `env_recovery_needed` |
| Recovery flow | Suspend verify SM, run env, resume in place |
| Structural shape | Dedicated `EnvPhaseStateMachine` |

**Reversed after industry review**: the per-task SM design was over-engineered
relative to peer tools. Final shape is workspace-level one-shot profile +
manifest-write auto-sync. No SM, no task-phase changes, no
`env_recovery_needed` action.

**Deferred**: `AGENTS.md` integration. The repo's `AGENTS.md` currently
duplicates `CLAUDE.md`; introducing it now is confusing. Profile JSON is the
single source of truth in v1.

## Architecture

```
Workspace registered, or profile > 30 days old, or explicit refresh
   ↓
EnvProfileBuilder.build() — ONE-TIME
   probe (deterministic) → draft_conventions (single LLM call) → write JSON
                                              ↓
                          <workspace>/.agentd/env_profile.json
                                              ↓
                                     persists across all tasks

Task lifecycle (unchanged):
   QUEUED → CONTEXT_READY → AWAITING_PLAN_APPROVAL → PLANNED → EXECUTING → … → SUCCEEDED
                                                                     ↓
                                              agent calls read_env_profile
                                              to learn PM / interpreter / test cmd
                                              before running commands
```

**Task-time touchpoints (only two)**:

1. **Lazy build on first task** — orchestrator calls `_ensure_env_profile` at
   the start of `run_task`/`resume_task`. If missing or stale, builds
   synchronously. Subsequent tasks reuse it. Cost paid once per workspace.

2. **Manifest-write auto-sync** — when `PatchEngine` writes a manifest file
   (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`) during step
   execution, it sets `task.execution_state.pending_install_for_scope`. The
   tool loop runs the profile's `install_command` for that scope before the
   next `run_command`. Flag is one-shot; no retry loop.

**Refresh triggers**:
- Profile age > 30 days
- Explicit refresh via `POST /v1/workspaces/env-profile`
- Probe failure on build → empty profile with `bootstrap_needed: true` +
  diagnostics; agent falls back to existing `find_binary` / `init_workspace`
  flow

**Unchanged**: `run_command`, `setup_env`, `find_binary`, `init_workspace`,
`PatchEngine`, verify SM, planning agent.

## Components

### New modules (4)

| Module | Role |
|---|---|
| `agentd/env/profile_builder.py` — `EnvProfileBuilder.build(workspace_root) → EnvProfile` | Deterministic probe + one LLM `draft_conventions` call → returns profile object. |
| `agentd/env/profile_store.py` — `EnvProfileStore` | `read(workspace) → EnvProfile \| None`, `write(workspace, profile)`, `is_stale(workspace) → bool`. JSON at `<workspace>/.agentd/env_profile.json`. |
| `agentd/tools/env_profile.py` — `read_env_profile` tool | Returns profile JSON to agent. Available in explore and verify phases. |
| `agentd/reasoning/env_prompts.py` — `DRAFT_CONVENTIONS_*` | System prompt, payload builder, response schema for the single LLM call. |

### Hooks (6 edits to existing files)

| File | Edit |
|---|---|
| `orchestrator/engine.py` | `run_task` and `resume_task` call `_ensure_env_profile(workspace_root)` at entry. Uses a workspace-keyed asyncio lock to serialize concurrent first-task builds. |
| `patch/engine.py` | When an op writes a manifest file, resolve the ecosystem scope and set `task.execution_state.pending_install_for_scope = scope_key`. |
| `tools/loop.py` | Before each `run_command`: if `pending_install_for_scope` is set, run the ecosystem's `install_command` via existing `setup_env`, clear the flag, then proceed. |
| `reasoning/tool_prompts.py` | Add an `ENV_PROFILE` teaching block: "Call `read_env_profile` before guessing the interpreter or test command. Use `interpreter_or_runner` directly — don't activate a venv." |
| `tools/registry.py` | Register `read_env_profile` in both `explore` and `verify` tool lists. |
| `api/routes.py` | `POST /v1/workspaces/env-profile?workspace=<path>` (force-rebuild), `GET /v1/workspaces/env-profile?workspace=<path>` (read). |

### Schemas (added to `domain/models.py`)

```python
class EnvProfile(BaseModel):
    workspace_root: str
    built_at: datetime
    bootstrap_needed: bool   # probe found nothing usable; agent uses find_binary/init_workspace normally
    ecosystems: list[EnvEcosystemEntry]
    conventions_notes: str | None  # short LLM summary, free-form
    diagnostics: list[str]   # probe warnings (e.g. setuptools flat-layout risk)

class EnvEcosystemEntry(BaseModel):
    ecosystem: Literal["python", "node", "rust", "go"]
    subdir: str              # relative; "" = workspace root
    manifest_path: str       # relative
    package_manager: str     # "uv" | "pip" | "npm" | "yarn" | "pnpm" | "cargo" | "go"
    install_command: str     # ready to pass to setup_env (e.g. "uv sync", "npm ci")
    interpreter_or_runner: str | None   # rel path (e.g. ".venv/bin/python")
    test_command: str | None # consumed as-is with subdir as cwd (e.g. "pytest")
    declared_dependencies_top: list[str]   # top ~20 from the manifest, verbatim
    notes: str | None        # LLM-supplied quirks

    @property
    def scope_key(self) -> str:
        return f"{self.ecosystem}:{self.subdir}"
```

`scope_key` is the deterministic identifier used across the design:
`task.execution_state.pending_install_for_scope` stores it; the manifest-write
hook computes it from the touched manifest path; the auto-sync lookup uses it
to find the entry.

### `draft_conventions` LLM call

- **Input (rich, per the "model performs well with context" rule)**: workspace
  tree (3 levels), and per-detected-ecosystem: raw manifest text, lockfile
  presence map, top-level dirs under that subdir, language runtime versions on
  PATH, package managers on PATH.
- **Output (compact, structured)**: `list[EnvEcosystemEntry]` +
  `conventions_notes`. One call per build.

## Data flow

### Path 1 — profile build

```
orchestrator.run_task(task)
  → _ensure_env_profile(workspace_root)
       store.read(workspace) → None | stale
       → builder.build(workspace_root):
           probe   = EcosystemProbe.scan(workspace_root)
                     # walks file tree (3 levels), finds manifests/lockfiles, queries PMs/runtimes on PATH
           decision = await reasoner.draft_conventions(probe)
                     # ONE structured LLM call
           profile = EnvProfile(workspace_root, built_at=now,
                                ecosystems=decision.entries, ...)
       store.write(workspace, profile)
  → continue with task
```

If `probe` returns no usable manifests → `bootstrap_needed=true`, skip the LLM
call, write empty profile + diagnostics.

### Path 2 — task execution

```
ToolLoop iteration
  → agent emits tool_call(read_env_profile, {})
  → EnvProfileTool reads <workspace>/.agentd/env_profile.json
  → agent now knows interpreter_or_runner = "services/agentd-py/.venv/bin/python"
                       test_command       = "pytest"
                       install_command    = "uv sync"
  → tool_call(run_command, {command: ".venv/bin/python", args: ["-c", "import agentd"],
                            cwd: "services/agentd-py"})
```

Direct interpreter path; no activation. Profile is cached in the tool registry
for the rest of the loop iteration to avoid repeated disk reads.

### Path 3 — mid-task manifest write

```
PatchEngine.apply_op(op)
  if op writes pyproject.toml | package.json | Cargo.toml | go.mod:
     scope_key = _resolve_ecosystem_for_manifest(op.path, profile)
     task.execution_state.pending_install_for_scope = scope_key

ToolLoop, before each next run_command:
  if task.execution_state.pending_install_for_scope is not None:
     entry = profile.ecosystems[pending_install_for_scope]
     await setup_env(command=entry.install_command, cwd=entry.subdir)
     task.execution_state.pending_install_for_scope = None
  proceed with run_command
```

One flag on `execution_state`. Cleared after the install runs. If `setup_env`
fails, the flag is still cleared (avoids loop); failure surfaces in tool
result and the agent decides next step.

### SSE events

| Event | Fires when |
|---|---|
| `env_profile_building` | `_ensure_env_profile` starts (first task on workspace) |
| `env_profile_built` | Profile written; payload: `ecosystems_count`, `bootstrap_needed` |
| `env_install_running` | Mid-task auto-sync starts; payload: `scope_key`, `install_command` |
| `env_install_done` | Exit code + tail of output |

Reuses the existing task SSE channel.

## Error handling

| Failure | Behavior |
|---|---|
| Probe finds no manifests | `bootstrap_needed=true`; no LLM call; empty profile; agent uses `find_binary` / `init_workspace` normally |
| Probe finds manifests, one read fails (permission, IO) | Skip that scope; record diagnostic; continue with others |
| `draft_conventions` LLM call times out or returns malformed JSON | Retry once. On second failure, write profile with `bootstrap_needed=true` + diagnostic. Agent falls back. |
| `store.write` fails (permission, disk) | Log + raise from `_ensure_env_profile`. Task transitions to `FAILED` with `env_profile_write_failed`. Rare. |
| `read_env_profile` called when no profile exists | Return `ToolOutput(output="profile not yet built; proceed without it", is_error=False)`. Non-fatal. |
| Auto-sync `setup_env` fails | Flag cleared regardless; failure surfaces in tool result. Agent decides next step. No automatic redo. |
| Profile is stale but rebuild fails | Use the stale profile; log warning. Don't block the task on a refresh. |
| Concurrent first-task-on-workspace | Workspace-keyed asyncio lock in orchestrator. Second task waits, then reads. |

## Testing

| Layer | Tests |
|---|---|
| Unit — `EcosystemProbe` | Synthetic workspaces under `tmp_path`: monorepo with python+node+rust; bare workspace; subdir manifests; broken `pyproject.toml`. Asserts probe result + diagnostics. |
| Unit — `EnvProfileStore` | Read/write round-trip; staleness checks (age, mtime); missing-file → `None`. |
| Unit — `EnvProfileBuilder` | `ScriptedReasoningEngine` returning canned `draft_conventions` output. Asserts ecosystems get correct PM/test-cmd. Includes "malformed response → bootstrap_needed" path. |
| Unit — `EnvProfileTool` | Tool returns profile JSON; returns "not built yet" message when absent. |
| Integration — auto-sync | `tmp_path` workspace, python pyproject. Scripted engine emits a patch that touches `pyproject.toml`, then a `run_command`. Assert `setup_env` ran with the profile's `install_command` and `cwd` between them. Verify flag cleared after. |
| Integration — lazy build on first task | New workspace, no `.agentd/env_profile.json`. Submit a task with scripted plan + reasoner. Assert profile written before first step ran; assert `env_profile_built` SSE event fired. |
| Regression | Existing verify SM, plan loop, orchestrator tests stay green. Only new code paths are exercised. |

## Open questions / future work

- **`AGENTS.md` integration** (deferred). Once the repo has a tool-neutral
  `AGENTS.md` curated separately from `CLAUDE.md`, the builder should include
  it in the `draft_conventions` payload and add an mtime-based refresh
  trigger.
- **Persistent venv via direct interpreter** vs **activation**: this design
  uses direct interpreter paths everywhere. Revisit if a future provider
  needs activated env vars for tool calls.
- **Multi-workspace profile cache**: profile JSON lives per workspace; no
  cross-workspace sharing. If the same toolchain is reused (e.g. same uv
  version across 10 projects), no deduplication. Acceptable for v1.
- **LLM model for `draft_conventions`**: same provider as `create_tool_step`.
  If qwen3.6 proves unreliable here, swap to a smaller, faster cloud model.
