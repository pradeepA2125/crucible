# P1 — Project Instructions & Prompt Files — Design

**Status:** Approved design, pre-implementation · **Date:** 2026-06-29 · **Owner:** pradeep
**Roadmap:** Phase 1 of `docs/superpowers/2026-06-29-feature-roadmap-copilot-parity.md`
**Next:** `writing-plans` → implementation plan.

---

## 1. Goal

Two Copilot-parity features that share one theme — *project-level configuration the agent honors automatically*:

1. **Project instructions** — auto-inject a workspace `AGENTS.md` into the chat controller's system prompt on every turn, self-updating when the file changes.
2. **Prompt files** — reusable `.crucible/prompts/<name>.md` snippets expanded inline in the composer via `/name [args]`.

Both are deliberately low-effort, high-frequency wins that plug into existing seams: the controller system-prompt assembly (the gated teaching-block pattern) and the VS Code extension composer.

## 2. Decisions (resolved during brainstorming)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **One spec, both features** | They ship as a coherent "project config" unit. |
| 2 | **Inject into the system prompt** (not the user-payload tail) | System-prompt instructions are followed more reliably. |
| 3 | **Self-updating via mtime-cached loader** (no separate watcher) | `format_controller_system_prompt` is rebuilt every turn → an mtime cache gives free freshness; unchanged file → identical cached bytes (KV-cache-stable); changed file → one cache-reset on the next turn. |
| 4 | **`AGENTS.md` is the only instructions source** | Single source of truth. No `.github/copilot-instructions.md` fallback (dropped — YAGNI). |
| 5 | **Default ON** | "Read your AGENTS.md" is table-stakes parity. `CRUCIBLE_PROJECT_INSTRUCTIONS` exists only as a kill-switch, defaulting on. |
| 6 | **Controller-only injection** | The planning path is dormant (task subsystem off by default); wiring it adds no live value now. Planning stays untouched. |
| 7 | **Prompt-file args: `$ARGUMENTS` + `$1..$N`, expanded in the composer** | Mirrors Claude Code / Copilot; the user sees and can edit the expanded text before sending. |

## 3. Architecture

### 3.1 Instruction injection (Python backend)

**New module `agentd/instructions/loader.py` — `ProjectInstructionsLoader`** (modeled on `retrieval/graph_walker.py`):

- Constructed with a `workspace_path`. Resolves `<workspace>/AGENTS.md`.
- **mtime-cached, thread-safe.** `load() -> str | None`:
  - File absent → `None`.
  - File present + mtime unchanged since last read → cached string.
  - File present + mtime moved → re-read, re-cache.
- **Size budget:** cap at `CRUCIBLE_INSTRUCTIONS_MAX_CHARS` (default `16000` ≈ ~4k tokens). Over budget → truncate at the cap with a `\n\n[... AGENTS.md truncated at N chars ...]` marker + a one-line `logger.warning`. Always-on context, so we keep it lean.
- **Best-effort:** any read/stat error → log + return `None`. Instructions must never break a turn (same contract as the memory harness's `prepare_turn`).

**Prompt-assembly wiring** (mirrors `_MEMORY_BLOCK` exactly):

- `agentd/chat/controller_prompts.py`:
  - New `_INSTRUCTIONS_BLOCK_TEMPLATE` — a labeled block:
    ```
    PROJECT INSTRUCTIONS (from this workspace's AGENTS.md — always-on guidance from the user):
    <text>
    ```
  - `format_controller_system_prompt(tool_definitions, *, task_subsystem_enabled=None, memory_enabled=None, project_instructions: str | None = None)` — appends the rendered block when `project_instructions` is a non-empty string. Appended (not a placeholder), after the memory block, so the cached prefix stays stable when content is unchanged.
- `agentd/reasoning/engine.py` (`create_controller_step`): resolves the text via the loader and threads it into `format_controller_system_prompt(...)`.

**Workspace resolution (footgun guard):** the loader uses the controller's **frozen `_workspace_path`** (from `main.py` / `CRUCIBLE_WORKSPACE_PATH`) — the same path used for the shadow root and all file ops — **not** the thread's `workspace_path` column (ignored per-turn; see CLAUDE.md "controller is workspace-frozen at startup"). The loader instance is built once at controller construction (factory time, alongside the memory harness) and reused per turn; the mtime cache lives inside it.

**Flag** `CRUCIBLE_PROJECT_INSTRUCTIONS` — resolved in `agentd/chat/controller_factory.py` next to `is_memory_enabled` as `is_project_instructions_enabled()`. **Defaults ON** (truthy unless explicitly set to `0/false/no/off`). When off, the loader is never consulted / the block is never appended (kill-switch).

### 3.2 Prompt files (`/name`, frontend only)

Entirely within the VS Code extension + React composer — **no backend involvement, no backend flag** (inert until a prompt file exists and `/` is typed).

- **Storage:** `<workspace>/.crucible/prompts/<name>.md`. The markdown body is the prompt text. (Frontmatter / description metadata deferred to a later phase.)
- **Discovery:** the extension host lists `.crucible/prompts/*.md`; the basenames (sans `.md`) power composer `/` autocomplete suggestions.
- **Expansion flow** (expansion happens *before* send, so the user can edit the result):
  1. Composer detects a leading `/name [args]`; expansion is triggered by an explicit affordance — selecting the entry from `/` autocomplete, or pressing Tab/Enter on the completed command — **not** by sending. (Send of an unexpanded `/name` is treated as "expand, don't send yet.")
  2. Webview posts `{ type: "expandPrompt", name, args }` to the extension host.
  3. Host reads `.crucible/prompts/<name>.md`, substitutes:
     - `$ARGUMENTS` → the full argument string after `/name`.
     - `$1, $2, … $N` → whitespace-split positional args (`$1` = first token, etc.); unfilled positionals → empty string.
  4. Host returns the expanded text; the composer **replaces its content inline** so the user sees and can edit it before sending. The message is then sent as ordinary text — the backend never sees `/name`.
- **Unknown `/name`:** no expansion; leave the text as typed + a soft toast ("No prompt 'name' in .crucible/prompts").

## 4. Components & boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `instructions/loader.py::ProjectInstructionsLoader` | mtime-cached read of AGENTS.md, size cap, degrade-to-None | filesystem only |
| `controller_prompts.py::_INSTRUCTIONS_BLOCK` + `format_controller_system_prompt` param | append instructions to the controller system prompt | loader output (a string) |
| `controller_factory.py::is_project_instructions_enabled` | resolve the kill-switch flag (default on) | env |
| `reasoning/engine.py::create_controller_step` | resolve loader text + thread into the prompt builder | loader, factory flag |
| extension host `expandPrompt` handler | read prompt file, substitute args, list prompts for autocomplete | filesystem (workspace) |
| composer (webview) | detect `/name`, request expansion, replace text inline, render `/` autocomplete | postMessage to host |

Each unit is testable in isolation: the loader against a `tmp_path`, the prompt-assembly with a plain string, the substitution logic as a pure function, the composer with a stub host.

## 5. Error handling

- Loader: all IO wrapped → `None` on error; warn-log with the path. Never raises into a turn.
- Truncation is silent to the agent (marker in-band) but warn-logged for the operator.
- Prompt-file expansion: missing file → toast, no throw; malformed args → positional blanks, never an error.

## 6. Testing

**Python (pytest):**
- Loader: absent file → `None`; present → text; mtime-unchanged → same cached object; mtime-moved → re-read; over-budget → truncated + marker; IO error → `None` + warning.
- Prompt assembly: block appended iff `project_instructions` non-empty; absent when `None`/empty; appears after `_MEMORY_BLOCK`; flag off → engine passes `None`. Parametrized like the existing `memory_enabled` / `task_subsystem_enabled` prompt tests.
- Factory: `is_project_instructions_enabled` default-on; explicit `0/false` → off.

**TypeScript (vitest):**
- Pure substitution fn: `$ARGUMENTS`, `$1..$N`, missing positionals → blank, no-arg prompt, repeated tokens.
- Host `expandPrompt`: known name → expanded; unknown → signals not-found; autocomplete listing from a stubbed dir.
- Composer: `/name` detection, inline replacement, unknown-name toast.

**Live smoke:**
1. Drop `AGENTS.md` with a distinctive directive (e.g. "prefix every reply with 🦊"); a live controller turn obeys it.
2. **Edit AGENTS.md mid-session; the next turn reflects the change** (self-updating, no restart).
3. Create `.crucible/prompts/review.md` using `$1`; `/review src/foo.py` expands inline in the composer.
4. Kill-switch: `CRUCIBLE_PROJECT_INSTRUCTIONS=0` → the directive is ignored.

## 7. Exit criteria

- An `AGENTS.md` measurably steers a live controller run, and a mid-session edit is picked up on the next turn.
- `/prompt-name [args]` expands inline in the composer with `$ARGUMENTS`/`$1..$N` substitution; `/` autocomplete lists available prompts.
- `CRUCIBLE_PROJECT_INSTRUCTIONS` kill-switch verified (default on; `0` disables).
- All TS + Python suites + typecheck green; live smoke (steps 1–4) passes.

## 8. Out of scope (deferred)

- Planning-loop instruction injection (dormant path).
- `.github/copilot-instructions.md` and other instruction filenames.
- Per-directory nested AGENTS.md.
- Prompt-file frontmatter / descriptions, named (non-positional) args, prompt-file management UI (P4).
- Settings/UI surface for any of this (P4).
