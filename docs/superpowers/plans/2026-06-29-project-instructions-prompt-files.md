# Project Instructions & Prompt Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-inject a workspace `AGENTS.md` into the chat controller's system prompt (self-updating), and expand `.crucible/prompts/<name>.md` snippets inline in the composer via `/name [args]`.

**Architecture:** Backend half — a new mtime-cached `ProjectInstructionsLoader` reads `AGENTS.md`; `DefaultReasoningEngine` resolves its text each controller turn and appends it to the system prompt via a new `_INSTRUCTIONS_BLOCK` (mirrors the existing `_MEMORY_BLOCK` pattern), gated by a default-ON `CRUCIBLE_PROJECT_INSTRUCTIONS` kill-switch. Frontend half — pure vscode-free helpers in `prompt-files.ts`, controller methods that read the prompts dir, host plumbing in `chat-panel.ts`/`extension.ts`, and composer wiring in `InputArea.tsx` that expands a slash command *before* send.

**Tech Stack:** Python 3.13 (FastAPI/Pydantic backend, pytest), TypeScript (VS Code extension + React webview-ui, vitest).

**Spec:** `docs/superpowers/specs/2026-06-29-project-instructions-prompt-files-design.md`

## Global Constraints

- **AGENTS.md is the ONLY instructions source** — no `.github/copilot-instructions.md` fallback, no nested files.
- **Controller-only injection** — do NOT touch `format_planning_system_prompt` or the planning/task path.
- **Instruction flag `CRUCIBLE_PROJECT_INSTRUCTIONS` defaults ON** (kill-switch); truthy set = `{1,true,yes,on}`.
- **Size cap** `CRUCIBLE_INSTRUCTIONS_MAX_CHARS`, default `16000`; over-budget → truncate + `logger.warning`.
- **Loader is best-effort** — any IO error degrades to `None`; instructions must never break a turn.
- **Prompt-file args:** `$ARGUMENTS` = full arg string; `$1..$N` = whitespace-split positional; unfilled positional → empty string.
- **Prompt files are frontend-only** — no backend route, no editor-client contract change. Expansion happens *before* send so the user can edit the result.
- **`controller.ts` stays vscode-free** (node `fs` is allowed; the `vscode` module is not).
- **All commits** end with the `Co-Authored-By: Claude Opus 4.8` + `Claude-Session:` trailers (see CLAUDE.md). One logical change per commit.
- **Per-task green bar:** Python tasks run `ruff check . && mypy agentd && pytest <touched>`; TS tasks run `npm run -w crucible-vscode-extension typecheck` + the relevant vitest.

---

## File Structure

**Backend (`services/agentd-py/`):**
- Create `agentd/instructions/__init__.py` — package marker.
- Create `agentd/instructions/loader.py` — `ProjectInstructionsLoader` (mtime-cached AGENTS.md reader).
- Modify `agentd/chat/controller_factory.py` — add `is_project_instructions_enabled()`; build the loader in `select_chat_handler`.
- Modify `agentd/chat/controller_prompts.py` — add `_INSTRUCTIONS_BLOCK_TEMPLATE` + `project_instructions` param to `format_controller_system_prompt`.
- Modify `agentd/reasoning/engine.py` — `DefaultReasoningEngine` takes a loader; `create_controller_step` resolves + passes the text.
- Create `tests/test_project_instructions_loader.py`, `tests/test_controller_prompt_instructions.py`, `tests/test_project_instructions_wiring.py`.

**Frontend (`apps/vscode-extension/`):**
- Create `src/prompt-files.ts` — vscode-free helpers (`substitutePrompt`, `parseSlashCommand`, `listPromptNames`, `loadPromptBody`).
- Modify `src/controller.ts` — add `listPrompts()` + `expandPrompt(name, args)`.
- Modify `src/chat-panel.ts` — route `listPrompts`/`expandPrompt` messages; post back `promptList`/`promptExpanded`; two new handler callbacks.
- Modify `src/extension.ts` — wire the two new handlers to the controller.
- Modify `webview-ui/src/components/InputArea.tsx` — slash autocomplete + expand-before-send + receive `promptList`/`promptExpanded`.
- Create `src/prompt-files.test.ts`, `webview-ui/src/components/InputArea.test.tsx`.

---

## Task 1: ProjectInstructionsLoader

**Files:**
- Create: `services/agentd-py/agentd/instructions/__init__.py`
- Create: `services/agentd-py/agentd/instructions/loader.py`
- Test: `services/agentd-py/tests/test_project_instructions_loader.py`

**Interfaces:**
- Produces: `ProjectInstructionsLoader(workspace_path: Path | str)` with `.load() -> str | None`. Returns the (size-capped, non-blank) AGENTS.md text, or `None` when the file is absent/blank/unreadable. mtime-cached + thread-safe.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_project_instructions_loader.py
from pathlib import Path

from agentd.instructions.loader import ProjectInstructionsLoader


def test_absent_file_returns_none(tmp_path: Path) -> None:
    assert ProjectInstructionsLoader(tmp_path).load() is None


def test_reads_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Always use tabs.", encoding="utf-8")
    assert ProjectInstructionsLoader(tmp_path).load() == "Always use tabs."


def test_blank_file_returns_none(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("   \n\t\n", encoding="utf-8")
    assert ProjectInstructionsLoader(tmp_path).load() is None


def test_mtime_cache_serves_cached_until_changed(tmp_path: Path) -> None:
    f = tmp_path / "AGENTS.md"
    f.write_text("v1", encoding="utf-8")
    loader = ProjectInstructionsLoader(tmp_path)
    assert loader.load() == "v1"
    # Rewrite with a forced-newer mtime so the change is detected deterministically.
    import os, time
    f.write_text("v2", encoding="utf-8")
    future = time.time() + 5
    os.utime(f, (future, future))
    assert loader.load() == "v2"


def test_oversize_is_truncated_with_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_INSTRUCTIONS_MAX_CHARS", "10")
    (tmp_path / "AGENTS.md").write_text("0123456789ABCDEF", encoding="utf-8")
    out = ProjectInstructionsLoader(tmp_path).load()
    assert out is not None
    assert out.startswith("0123456789")
    assert "truncated at 10 chars" in out


def test_disappearing_file_after_load_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "AGENTS.md"
    f.write_text("hi", encoding="utf-8")
    loader = ProjectInstructionsLoader(tmp_path)
    assert loader.load() == "hi"
    f.unlink()
    assert loader.load() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_project_instructions_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.instructions'`.

- [ ] **Step 3: Create the package marker and the loader**

```python
# agentd/instructions/__init__.py
```
(empty file)

```python
# agentd/instructions/loader.py
"""mtime-cached reader for a workspace AGENTS.md (project instructions).

Mirrors the GraphWalker mtime-cache discipline: a cheap NOOP when the file
has not moved, a single re-read when it has. Best-effort — any IO error
degrades to None so a controller turn is never broken by instructions.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CHARS = 16000


def _max_chars() -> int:
    raw = os.getenv("CRUCIBLE_INSTRUCTIONS_MAX_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_MAX_CHARS


class ProjectInstructionsLoader:
    """Reads `<workspace>/AGENTS.md`, size-capped, returns text or None.

    Thread-safe. The text is re-read only when the file's mtime changes, so
    repeated `load()` calls across turns are cheap and the returned bytes are
    identical until the user edits the file (KV-cache-friendly upstream)."""

    FILENAME = "AGENTS.md"

    def __init__(self, workspace_path: Path | str) -> None:
        self._path = Path(workspace_path) / self.FILENAME
        self._lock = threading.Lock()
        self._cached_mtime_ns: int | None = None
        self._cached_text: str | None = None  # capped; "" means present-but-blank

    def load(self) -> str | None:
        with self._lock:
            try:
                mtime_ns = self._path.stat().st_mtime_ns
            except (FileNotFoundError, NotADirectoryError):
                self._cached_mtime_ns = None
                self._cached_text = None
                return None
            except OSError as exc:  # permission, etc. — keep any prior text
                logger.warning("[instructions] cannot stat %s: %s", self._path, exc)
                return self._nonblank(self._cached_text)

            if self._cached_mtime_ns == mtime_ns and self._cached_text is not None:
                return self._nonblank(self._cached_text)

            try:
                text = self._path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[instructions] cannot read %s: %s", self._path, exc)
                return self._nonblank(self._cached_text)

            self._cached_text = self._cap(text)
            self._cached_mtime_ns = mtime_ns
            return self._nonblank(self._cached_text)

    @staticmethod
    def _nonblank(text: str | None) -> str | None:
        return text if (text and text.strip()) else None

    @staticmethod
    def _cap(text: str) -> str:
        limit = _max_chars()
        if len(text) <= limit:
            return text
        logger.warning(
            "[instructions] AGENTS.md exceeds %d chars; truncating (was %d)",
            limit,
            len(text),
        )
        return text[:limit] + f"\n\n[... AGENTS.md truncated at {limit} chars ...]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_project_instructions_loader.py -v && ruff check agentd/instructions && mypy agentd/instructions`
Expected: all PASS; ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/instructions services/agentd-py/tests/test_project_instructions_loader.py
git commit -m "feat(instructions): mtime-cached ProjectInstructionsLoader for AGENTS.md"
```

---

## Task 2: CRUCIBLE_PROJECT_INSTRUCTIONS flag (default ON)

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_factory.py`
- Test: `services/agentd-py/tests/test_project_instructions_loader.py` (append)

**Interfaces:**
- Produces: `is_project_instructions_enabled() -> bool` in `controller_factory` — default ON; off only when explicitly set to a falsy value.

- [ ] **Step 1: Write the failing tests** (append to the loader test file)

```python
# tests/test_project_instructions_loader.py (append)
from agentd.chat.controller_factory import is_project_instructions_enabled


def test_instructions_flag_default_on(monkeypatch) -> None:
    monkeypatch.delenv("CRUCIBLE_PROJECT_INSTRUCTIONS", raising=False)
    assert is_project_instructions_enabled() is True


def test_instructions_flag_explicit_off(monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_PROJECT_INSTRUCTIONS", "0")
    assert is_project_instructions_enabled() is False
    monkeypatch.setenv("CRUCIBLE_PROJECT_INSTRUCTIONS", "false")
    assert is_project_instructions_enabled() is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/agentd-py && pytest tests/test_project_instructions_loader.py -k flag -v`
Expected: FAIL — `ImportError: cannot import name 'is_project_instructions_enabled'`.

- [ ] **Step 3: Add the flag resolver**

In `agentd/chat/controller_factory.py`, add after `is_memory_enabled` (the module already has `_TRUTHY = {"1", "true", "yes", "on"}` and `import os`):

```python
def is_project_instructions_enabled() -> bool:
    """Whether a workspace AGENTS.md is injected into the controller system
    prompt. Default ON — reading the project's AGENTS.md is table-stakes parity.
    Kill-switch only: CRUCIBLE_PROJECT_INSTRUCTIONS=0 (or false/no/off)."""
    return os.getenv("CRUCIBLE_PROJECT_INSTRUCTIONS", "1").strip().lower() in _TRUTHY
```

- [ ] **Step 4: Run to verify pass**

Run: `cd services/agentd-py && pytest tests/test_project_instructions_loader.py -k flag -v && ruff check agentd/chat/controller_factory.py`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_factory.py services/agentd-py/tests/test_project_instructions_loader.py
git commit -m "feat(instructions): CRUCIBLE_PROJECT_INSTRUCTIONS flag (default on)"
```

---

## Task 3: Append instructions to the controller system prompt

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py`
- Test: `services/agentd-py/tests/test_controller_prompt_instructions.py`

**Interfaces:**
- Consumes: instructions text (a `str | None`) from Task 1's loader.
- Produces: `format_controller_system_prompt(tool_definitions, *, task_subsystem_enabled=None, memory_enabled=None, project_instructions: str | None = None) -> str` — appends a labeled instructions block when `project_instructions` is non-blank.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controller_prompt_instructions.py
from agentd.chat.controller_prompts import format_controller_system_prompt

TOOLS: list[dict[str, object]] = []


def test_no_instructions_no_block() -> None:
    out = format_controller_system_prompt(
        TOOLS, task_subsystem_enabled=False, memory_enabled=False
    )
    assert "PROJECT INSTRUCTIONS" not in out


def test_instructions_appended_when_present() -> None:
    out = format_controller_system_prompt(
        TOOLS,
        task_subsystem_enabled=False,
        memory_enabled=False,
        project_instructions="Always use tabs, never spaces.",
    )
    assert "PROJECT INSTRUCTIONS" in out
    assert "Always use tabs, never spaces." in out
    # Appended at the end (after any memory block), mirroring _MEMORY_BLOCK.
    assert out.rstrip().endswith("Always use tabs, never spaces.")


def test_blank_instructions_no_block() -> None:
    out = format_controller_system_prompt(
        TOOLS, task_subsystem_enabled=False, memory_enabled=False,
        project_instructions="   \n  ",
    )
    assert "PROJECT INSTRUCTIONS" not in out


def test_instructions_value_with_braces_does_not_crash() -> None:
    # AGENTS.md may contain literal { } — must not be treated as format fields.
    out = format_controller_system_prompt(
        TOOLS, task_subsystem_enabled=False, memory_enabled=False,
        project_instructions="Use {curly} braces in JSON examples.",
    )
    assert "Use {curly} braces in JSON examples." in out
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/agentd-py && pytest tests/test_controller_prompt_instructions.py -v`
Expected: FAIL — `TypeError: format_controller_system_prompt() got an unexpected keyword argument 'project_instructions'`.

- [ ] **Step 3: Add the block template and parameter**

In `agentd/chat/controller_prompts.py`, add the template next to `_MEMORY_BLOCK`:

```python
_INSTRUCTIONS_BLOCK_TEMPLATE = """

PROJECT INSTRUCTIONS (from this workspace's AGENTS.md — always-on guidance from \
the user; follow it unless it conflicts with a safety rule):
{instructions}"""
```

Then update the signature + body (current body shown for context):

```python
def format_controller_system_prompt(
    tool_definitions: list[dict[str, object]],
    *,
    task_subsystem_enabled: bool | None = None,
    memory_enabled: bool | None = None,
    project_instructions: str | None = None,
) -> str:
    from agentd.chat.controller_factory import is_memory_enabled, is_task_subsystem_enabled

    if task_subsystem_enabled is None:
        task_subsystem_enabled = is_task_subsystem_enabled()
    if memory_enabled is None:
        memory_enabled = is_memory_enabled()
    modes = _PROPOSE_MODE_MODES_ENABLED if task_subsystem_enabled else _PROPOSE_MODE_MODES_DISABLED
    base = (
        CONTROLLER_SYSTEM_PROMPT
        .replace("{propose_mode_modes}", modes)
        .replace("{tools_json}", json.dumps(tool_definitions, indent=2, sort_keys=True))
    )
    base = base + (_MEMORY_BLOCK if memory_enabled else "")
    # .replace (not .format): AGENTS.md text may contain literal { } that
    # str.format would misparse as fields.
    if project_instructions and project_instructions.strip():
        base += _INSTRUCTIONS_BLOCK_TEMPLATE.replace(
            "{instructions}", project_instructions.strip()
        )
    return base
```

- [ ] **Step 4: Run to verify pass**

Run: `cd services/agentd-py && pytest tests/test_controller_prompt_instructions.py -v && ruff check agentd/chat/controller_prompts.py && mypy agentd/chat/controller_prompts.py`
Expected: all PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/tests/test_controller_prompt_instructions.py
git commit -m "feat(instructions): append AGENTS.md block to controller system prompt"
```

---

## Task 4: Wire the loader through the engine + factory

**Files:**
- Modify: `services/agentd-py/agentd/reasoning/engine.py:61-69` (init) and `:248-258` (`create_controller_step`)
- Modify: `services/agentd-py/agentd/chat/controller_factory.py` (`select_chat_handler`)
- Test: `services/agentd-py/tests/test_project_instructions_wiring.py`

**Interfaces:**
- Consumes: `ProjectInstructionsLoader` (Task 1), `is_project_instructions_enabled` (Task 2), `format_controller_system_prompt(..., project_instructions=...)` (Task 3).
- Produces: `DefaultReasoningEngine(*, model, transport, project_instructions_loader: ProjectInstructionsLoader | None = None)`; `create_controller_step` injects `loader.load()` into the system prompt. `select_chat_handler` builds the loader from the **frozen** `workspace_path` when the flag is on.

- [ ] **Step 1: Write the failing test** (drives the engine seam directly with a fake transport that captures the system prompt)

```python
# tests/test_project_instructions_wiring.py
import asyncio
from pathlib import Path

from agentd.instructions.loader import ProjectInstructionsLoader
from agentd.reasoning.engine import DefaultReasoningEngine


class _CapturingTransport:
    supports_oneof_grammar = False

    def __init__(self) -> None:
        self.system_instructions = ""

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload, on_thinking=None):
        self.system_instructions = system_instructions
        return {"type": "answer", "thought": "", "message": "ok"}


def _run(coro):
    return asyncio.run(coro)


def test_engine_injects_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Prefix replies with FOX.", encoding="utf-8")
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(
        model="m", transport=transport,
        project_instructions_loader=ProjectInstructionsLoader(tmp_path),
    )
    _run(engine.create_controller_step(
        plan_context={"goal": "hi", "workspace_path": str(tmp_path)},
        history=[],
        tool_definitions=[],
        phase="DECIDE",
    ))
    assert "Prefix replies with FOX." in transport.system_instructions


def test_engine_without_loader_has_no_block(tmp_path: Path) -> None:
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    _run(engine.create_controller_step(
        plan_context={"goal": "hi", "workspace_path": str(tmp_path)},
        history=[],
        tool_definitions=[],
        phase="DECIDE",
    ))
    assert "PROJECT INSTRUCTIONS" not in transport.system_instructions
```

> NOTE: `create_controller_step` writes a per-iteration debug artifact keyed by thread/turn from `plan_context`. If the test trips on a missing key, pass `plan_context={"goal": "hi", "workspace_path": str(tmp_path), "thread_id": "t", "turn_id": "u"}` — match the keys the artifact writer reads (check the method body around line 272).

- [ ] **Step 2: Run to verify failure**

Run: `cd services/agentd-py && pytest tests/test_project_instructions_wiring.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'project_instructions_loader'`.

- [ ] **Step 3: Add the engine param + injection**

In `agentd/reasoning/engine.py`, update `DefaultReasoningEngine.__init__`:

```python
class DefaultReasoningEngine(ReasoningEngine):
    def __init__(
        self,
        *,
        model: str,
        transport: ModelJsonTransport,
        project_instructions_loader: "ProjectInstructionsLoader | None" = None,
    ) -> None:
        self._model = model
        self._transport = transport
        self._project_instructions_loader = project_instructions_loader
```

Add the type-only import at the top of the file (guard against a runtime cycle):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from agentd.instructions.loader import ProjectInstructionsLoader
```

In `create_controller_step`, replace the system-prompt line:

```python
        instructions = (
            self._project_instructions_loader.load()
            if self._project_instructions_loader is not None
            else None
        )
        system_instructions = format_controller_system_prompt(
            tool_definitions, project_instructions=instructions
        )
```

- [ ] **Step 4: Build the loader in the factory**

In `agentd/chat/controller_factory.py`, inside `select_chat_handler`'s `if is_controller_enabled():` branch, after `memory_harness = build_memory_harness(...)`:

```python
        from agentd.instructions.loader import ProjectInstructionsLoader

        project_instructions_loader = (
            ProjectInstructionsLoader(workspace_path)
            if is_project_instructions_enabled()
            else None
        )
```

and pass it into the engine in the `ChatController(...)` construction:

```python
            reasoning_engine=DefaultReasoningEngine(
                model=model,
                transport=transport,
                project_instructions_loader=project_instructions_loader,
            ),
```

- [ ] **Step 5: Run to verify pass + full controller-prompt suite**

Run: `cd services/agentd-py && pytest tests/test_project_instructions_wiring.py tests/test_controller_prompt_instructions.py -v && ruff check agentd/reasoning/engine.py agentd/chat/controller_factory.py && mypy agentd`
Expected: all PASS; ruff + mypy clean.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/reasoning/engine.py services/agentd-py/agentd/chat/controller_factory.py services/agentd-py/tests/test_project_instructions_wiring.py
git commit -m "feat(instructions): wire AGENTS.md loader through engine + controller factory"
```

---

## Task 5: Prompt-file helpers (vscode-free)

**Files:**
- Create: `apps/vscode-extension/src/prompt-files.ts`
- Test: `apps/vscode-extension/src/prompt-files.test.ts`

**Interfaces:**
- Produces:
  - `substitutePrompt(body: string, args: string): string` — replaces `$ARGUMENTS` (full) + `$1..$N` (positional).
  - `parseSlashCommand(text: string): { name: string; args: string } | null` — parses a leading `/name [args]`; `null` if not a slash command.
  - `listPromptNames(promptsDir: string): Promise<string[]>` — sorted basenames of `*.md` (empty on any error).
  - `loadPromptBody(promptsDir: string, name: string): Promise<string | null>` — file body, or `null` if missing/invalid name.

- [ ] **Step 1: Write the failing tests**

```typescript
// src/prompt-files.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { promises as fsp } from "fs";
import * as os from "os";
import * as path from "path";
import {
  substitutePrompt,
  parseSlashCommand,
  listPromptNames,
  loadPromptBody,
} from "./prompt-files";

describe("substitutePrompt", () => {
  it("replaces $ARGUMENTS with the full arg string", () => {
    expect(substitutePrompt("Review: $ARGUMENTS", "src/a.py src/b.py")).toBe(
      "Review: src/a.py src/b.py"
    );
  });
  it("replaces positional $1 $2", () => {
    expect(substitutePrompt("Compare $1 to $2", "old new")).toBe("Compare old to new");
  });
  it("blanks unfilled positionals", () => {
    expect(substitutePrompt("X=$1 Y=$2", "only")).toBe("X=only Y=");
  });
  it("no-arg prompt is unchanged except blanked tokens", () => {
    expect(substitutePrompt("Just do it.", "")).toBe("Just do it.");
  });
});

describe("parseSlashCommand", () => {
  it("parses name and args", () => {
    expect(parseSlashCommand("/review src/a.py")).toEqual({ name: "review", args: "src/a.py" });
  });
  it("parses name with no args", () => {
    expect(parseSlashCommand("/review")).toEqual({ name: "review", args: "" });
  });
  it("returns null for non-slash text", () => {
    expect(parseSlashCommand("hello /review")).toBeNull();
  });
  it("returns null for a bare slash", () => {
    expect(parseSlashCommand("/")).toBeNull();
  });
});

describe("listPromptNames / loadPromptBody", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await fsp.mkdtemp(path.join(os.tmpdir(), "prompts-"));
  });
  afterEach(async () => {
    await fsp.rm(dir, { recursive: true, force: true });
  });

  it("lists sorted *.md basenames", async () => {
    await fsp.writeFile(path.join(dir, "review.md"), "body", "utf8");
    await fsp.writeFile(path.join(dir, "ask.md"), "body", "utf8");
    await fsp.writeFile(path.join(dir, "notes.txt"), "ignore", "utf8");
    expect(await listPromptNames(dir)).toEqual(["ask", "review"]);
  });
  it("returns [] when the dir is missing", async () => {
    expect(await listPromptNames(path.join(dir, "nope"))).toEqual([]);
  });
  it("loads a body by name", async () => {
    await fsp.writeFile(path.join(dir, "review.md"), "Review $1", "utf8");
    expect(await loadPromptBody(dir, "review")).toBe("Review $1");
  });
  it("returns null for a missing prompt", async () => {
    expect(await loadPromptBody(dir, "ghost")).toBeNull();
  });
  it("rejects path-traversal names", async () => {
    expect(await loadPromptBody(dir, "../secret")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- src/prompt-files.test.ts`
Expected: FAIL — cannot find module `./prompt-files`.

- [ ] **Step 3: Implement the helpers**

```typescript
// src/prompt-files.ts
// vscode-free helpers for .crucible/prompts/<name>.md. Pure substitution +
// node-fs reads; no `vscode` import so this stays unit-testable in vitest.
import { promises as fsp } from "fs";
import * as path from "path";

/** Substitute $ARGUMENTS (full string) and $1..$N (whitespace-split positional). */
export function substitutePrompt(body: string, args: string): string {
  const trimmed = args.trim();
  const positional = trimmed.length > 0 ? trimmed.split(/\s+/) : [];
  let out = body.split("$ARGUMENTS").join(trimmed);
  out = out.replace(/\$(\d+)/g, (_match, digits: string) => {
    const idx = Number(digits) - 1;
    return idx >= 0 && idx < positional.length ? positional[idx] : "";
  });
  return out;
}

/** Parse a leading "/name [args]". Returns null when `text` is not a slash command. */
export function parseSlashCommand(text: string): { name: string; args: string } | null {
  const match = /^\/([A-Za-z0-9._-]+)(?:\s+([\s\S]*))?$/.exec(text.trimStart());
  if (!match) return null;
  return { name: match[1], args: (match[2] ?? "").trim() };
}

/** Sorted basenames (sans .md) of prompt files; [] on any error. */
export async function listPromptNames(promptsDir: string): Promise<string[]> {
  try {
    const entries = await fsp.readdir(promptsDir);
    return entries
      .filter((e) => e.endsWith(".md"))
      .map((e) => e.slice(0, -3))
      .sort();
  } catch {
    return [];
  }
}

/** Body of <promptsDir>/<name>.md, or null if missing or the name is unsafe. */
export async function loadPromptBody(promptsDir: string, name: string): Promise<string | null> {
  if (!/^[A-Za-z0-9._-]+$/.test(name)) return null;
  try {
    return await fsp.readFile(path.join(promptsDir, `${name}.md`), "utf8");
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm run -w crucible-vscode-extension test -- src/prompt-files.test.ts && npm run -w crucible-vscode-extension typecheck`
Expected: all PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/prompt-files.ts apps/vscode-extension/src/prompt-files.test.ts
git commit -m "feat(prompt-files): vscode-free substitute/parse/list/load helpers"
```

---

## Task 6: Controller prompt-file methods

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts` (add two methods near `memoryWorkspacePath()` at :234)
- Test: `apps/vscode-extension/src/controller.test.ts` (or the existing controller test file — append)

**Interfaces:**
- Consumes: `listPromptNames`, `loadPromptBody`, `substitutePrompt` (Task 5); `this.memoryWorkspacePath()` (existing, returns the workspace path or "").
- Produces on `AiEditorController`:
  - `listPrompts(): Promise<string[]>`
  - `expandPrompt(name: string, args: string): Promise<{ found: boolean; text: string }>`

- [ ] **Step 1: Write the failing test**

```typescript
// append to the existing controller test file (mirror its setup of AiEditorController
// with a stub ControllerUI whose getWorkspacePath() returns a real tmp dir).
import { promises as fsp } from "fs";
import * as os from "os";
import * as path from "path";

it("lists and expands prompt files from the workspace", async () => {
  const ws = await fsp.mkdtemp(path.join(os.tmpdir(), "ctl-prompts-"));
  await fsp.mkdir(path.join(ws, ".crucible", "prompts"), { recursive: true });
  await fsp.writeFile(path.join(ws, ".crucible", "prompts", "review.md"), "Review $1", "utf8");
  // Build the controller with a stub UI whose getWorkspacePath() returns `ws`.
  const controller = makeControllerWithWorkspace(ws); // helper in the existing test file
  expect(await controller.listPrompts()).toEqual(["review"]);
  expect(await controller.expandPrompt("review", "src/a.py")).toEqual({
    found: true,
    text: "Review src/a.py",
  });
  expect(await controller.expandPrompt("ghost", "")).toEqual({ found: false, text: "" });
  await fsp.rm(ws, { recursive: true, force: true });
});
```

> NOTE: reuse the existing test's controller-construction helper. If none exists, construct `AiEditorController` exactly as the other tests in that file do, with a stub `ControllerUI` returning `ws` from `getWorkspacePath()`. The two new methods depend only on `memoryWorkspacePath()`, so no backend/session setup is needed.

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- src/controller.test.ts -t "prompt files"`
Expected: FAIL — `controller.listPrompts is not a function`.

- [ ] **Step 3: Implement the methods**

At the top of `src/controller.ts`, add to the imports:

```typescript
import * as path from "path";
import { listPromptNames, loadPromptBody, substitutePrompt } from "./prompt-files";
```

Add the methods next to `memoryWorkspacePath()`:

```typescript
  private promptsDir(): string {
    return path.join(this.memoryWorkspacePath(), ".crucible", "prompts");
  }

  /** Names of available `.crucible/prompts/*.md` for composer `/` autocomplete. */
  async listPrompts(): Promise<string[]> {
    const ws = this.memoryWorkspacePath();
    if (!ws) return [];
    return listPromptNames(this.promptsDir());
  }

  /** Expand `/name args` to its substituted body; `found=false` when no such prompt. */
  async expandPrompt(name: string, args: string): Promise<{ found: boolean; text: string }> {
    const ws = this.memoryWorkspacePath();
    if (!ws) return { found: false, text: "" };
    const body = await loadPromptBody(this.promptsDir(), name);
    if (body === null) return { found: false, text: "" };
    return { found: true, text: substitutePrompt(body, args) };
  }
```

- [ ] **Step 4: Run to verify pass**

Run: `npm run -w crucible-vscode-extension test -- src/controller.test.ts -t "prompt files" && npm run -w crucible-vscode-extension typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/controller.ts apps/vscode-extension/src/controller.test.ts
git commit -m "feat(prompt-files): controller listPrompts + expandPrompt"
```

---

## Task 7: Host plumbing (chat-panel + extension wiring)

**Files:**
- Modify: `apps/vscode-extension/src/chat-panel.ts` (constructor params + `onDidReceiveMessage` routing + post-back)
- Modify: `apps/vscode-extension/src/extension.ts` (wire the two new handlers to the controller)

**Interfaces:**
- Consumes: `controller.listPrompts()` / `controller.expandPrompt(name, args)` (Task 6).
- Produces: webview messages `{ type: "listPrompts" }` → host replies `{ type: "promptList", names }`; `{ type: "expandPrompt", name, args }` → host replies `{ type: "promptExpanded", name, found, text }`.

- [ ] **Step 1: Add the handler callback types + constructor params to ChatPanel**

In `src/chat-panel.ts`, add near the other handler type aliases:

```typescript
export type ListPromptsHandler = () => Promise<string[]>;
export type ExpandPromptHandler = (
  name: string,
  args: string
) => Promise<{ found: boolean; text: string }>;
```

Add two parameters to the constructor (place them before the trailing `onReady` default param so existing positional wiring is unaffected up to that point — update `extension.ts` in Step 3 accordingly):

```typescript
    private readonly onSetReviewPref: SetReviewPrefHandler,
    private readonly onListPrompts: ListPromptsHandler,
    private readonly onExpandPrompt: ExpandPromptHandler,
    private readonly onReady: () => Promise<void> = async () => {}
```

- [ ] **Step 2: Route the two messages in `onDidReceiveMessage`**

In the `else if` chain (before the final `else { return; }`), add:

```typescript
      } else if (m["type"] === "listPrompts") {
        p = (async () => {
          const names = await this.onListPrompts();
          this.panel?.webview.postMessage({ type: "promptList", names });
        })();
      } else if (m["type"] === "expandPrompt") {
        const name = m["name"] as string;
        const args = (m["args"] as string) ?? "";
        p = (async () => {
          const result = await this.onExpandPrompt(name, args);
          this.panel?.webview.postMessage({
            type: "promptExpanded",
            name,
            found: result.found,
            text: result.text,
          });
        })();
```

- [ ] **Step 3: Wire the handlers in `extension.ts`**

Find the `new ChatPanel(...)` construction and add the two handlers in the matching positions (immediately after the `onSetReviewPref` argument, before `onReady` if present):

```typescript
    (name, args) => controller.expandPrompt(name, args), // placeholder ordering — see below
```

Concretely, locate the `onSetReviewPref` argument in the `new ChatPanel(` call and insert, in order, right after it:

```typescript
    () => controller.listPrompts(),
    (name: string, args: string) => controller.expandPrompt(name, args),
```

- [ ] **Step 4: Run typecheck to verify the wiring compiles**

Run: `npm run -w @crucible/editor-client build && npm run -w crucible-vscode-extension typecheck`
Expected: PASS — no arity/type errors on the `new ChatPanel(...)` call. (If the call site relied on a trailing `onReady`, confirm it still lands in the last position.)

- [ ] **Step 5: Run the extension test suite**

Run: `npm run -w crucible-vscode-extension test`
Expected: PASS — no regressions in existing panel/controller tests.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts
git commit -m "feat(prompt-files): host plumbing for listPrompts/expandPrompt messages"
```

---

## Task 8: Composer slash-command expansion

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`
- Test: `apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx`

**Interfaces:**
- Consumes: `parseSlashCommand` (Task 5); host messages `promptList` / `promptExpanded` (Task 7).
- Produces: composer behavior — Enter on an un-expanded `/name [args]` posts `{ type: "expandPrompt", name, args }` instead of sending; a `promptExpanded` reply replaces the draft (or, when `found=false`, leaves the draft and shows a soft inline notice).

- [ ] **Step 1: Write the failing test**

```tsx
// webview-ui/src/components/InputArea.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { InputArea } from "./InputArea";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const availability = {
  disabled: false,
  showStop: false,
  taskStop: false,
  placeholder: "Message",
} as const;

function Harness() {
  const [draft, setDraft] = (require("react") as typeof import("react")).useState("/review src/a.py");
  return <InputArea availability={availability} draft={draft} onDraftChange={setDraft} />;
}

describe("InputArea slash-command expansion", () => {
  beforeEach(() => vi.clearAllMocks());

  it("Enter on a /name command posts expandPrompt, not sendMessage", () => {
    render(<Harness />);
    const ta = screen.getByLabelText("Chat input");
    fireEvent.keyDown(ta, { key: "Enter" });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "expandPrompt", name: "review", args: "src/a.py" });
    expect(calls.find((c) => c.type === "sendMessage")).toBeUndefined();
  });

  it("a promptExpanded message replaces the draft", () => {
    render(<Harness />);
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "promptExpanded", name: "review", found: true, text: "Review src/a.py" },
      })
    );
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    expect(ta.value).toBe("Review src/a.py");
  });
});
```

> NOTE: follow the render/mocking conventions already used in `components/messages/gates/ModeGate.test.tsx` (same vitest + @testing-library setup). Adjust the `Harness` if that file uses a cleaner state helper.

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- webview-ui/src/components/InputArea.test.tsx`
Expected: FAIL — Enter sends `sendMessage` (no `expandPrompt`).

- [ ] **Step 3: Implement expansion in InputArea**

Add the import and a message listener; intercept slash commands in `doSend`. In `src/components/InputArea.tsx`:

```typescript
import { parseSlashCommand } from "../../../src/prompt-files";
```

> If the relative import to `src/prompt-files` is awkward under the webview tsconfig, copy `parseSlashCommand` into a tiny `webview-ui/src/slash.ts` mirror (the webview already keeps local mirror types — see `webview-ui/src/memory/types.tsx`) and import from there; unit-test the mirror identically.

Add a listener for host replies (component-local, alongside the existing effects):

```typescript
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "promptExpanded") {
        if (m["found"] === true) {
          onDraftChange(m["text"] as string);
        }
        // found=false → leave the draft as typed (soft no-op; optional toast).
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onDraftChange]);
```

Change `doSend` to intercept an un-expanded slash command:

```typescript
  function doSend() {
    if (availability.disabled) return;
    const trimmed = draft.trim();
    if (!trimmed) return;
    const slash = parseSlashCommand(trimmed);
    if (slash) {
      // Expand first; the host replies with promptExpanded which fills the draft.
      // The user then reviews/edits and sends again (now non-slash → real send).
      vscode.postMessage({ type: "expandPrompt", name: slash.name, args: slash.args });
      return;
    }
    vscode.postMessage({ type: "sendMessage", text: trimmed, stepReview });
    onDraftChange("");
    const el = textareaRef.current;
    if (el) el.style.height = "auto";
  }
```

> The `/` autocomplete suggestion list is optional polish; the expand-before-send behavior above is the testable core. If you add suggestions, request them once with `vscode.postMessage({ type: "listPrompts" })` when the draft becomes a lone `/`, render the `promptList` names, and fill the draft with `/<name> ` on click.

- [ ] **Step 4: Run to verify pass**

Run: `npm run -w crucible-vscode-extension test -- webview-ui/src/components/InputArea.test.tsx && npm run -w crucible-vscode-extension typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/InputArea.tsx apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx
git commit -m "feat(prompt-files): expand /name commands inline in the composer"
```

---

## Task 9: Full-suite green + live smoke

**Files:** none (verification only) — but fix any cross-cutting break here.

- [ ] **Step 1: Full Python suite**

Run: `cd services/agentd-py && ruff check . && mypy agentd && pytest`
Expected: all green. (Read the actual `FAILED`/summary lines — never trust a piped exit code.)

- [ ] **Step 2: Full TS suite + typecheck + build**

Run: `npm run build && npm run typecheck && npm run test`
Expected: all green across editor-client + vscode-extension.

- [ ] **Step 3: Rebuild the webview bundle** (frontend smoke requires a fresh `webview-ui/dist`)

Run: `npm run -w crucible-vscode-extension build`
Expected: `webview-ui/dist` rebuilt.

- [ ] **Step 4: Live smoke — instructions steer a run + self-update**

Start the backend with the controller + a workspace that has an `AGENTS.md`:
```bash
cd "<repo>" && export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
printf 'Begin every reply with the literal token FOX.\n' > "$PWD/workspaces/crucible-stress/AGENTS.md"
CRUCIBLE_CHAT_CONTROLLER=1 bash scripts/stress/start-backend.sh \
  --backend gemini --workspace "$PWD/workspaces/crucible-stress" --validation-profile none
```
Open the dev host, send a chat message. **Expected:** the reply begins with `FOX`.
Now edit `AGENTS.md` to a different token (e.g. `OWL`) mid-session and send again. **Expected:** the next reply begins with `OWL` — no backend restart (self-updating mtime cache).
Kill-switch check: restart with `CRUCIBLE_PROJECT_INSTRUCTIONS=0`; the token is ignored.

- [ ] **Step 5: Live smoke — prompt-file expansion**

```bash
mkdir -p "$PWD/workspaces/crucible-stress/.crucible/prompts"
printf 'Summarize the file $1 and list its exported symbols.\n' \
  > "$PWD/workspaces/crucible-stress/.crucible/prompts/summarize.md"
```
In the composer type `/summarize src/foo.py` and press Enter. **Expected:** the draft is replaced inline with `Summarize the file src/foo.py and list its exported symbols.`; pressing Enter again sends it.

- [ ] **Step 6: Update CLAUDE.md**

Add a short subsection under the chat/controller architecture notes documenting: the `instructions/loader.py` mtime-cache, the `CRUCIBLE_PROJECT_INSTRUCTIONS` (default-on) + `CRUCIBLE_INSTRUCTIONS_MAX_CHARS` env vars, controller-only injection, and the `.crucible/prompts/<name>.md` + `/name` composer flow (frontend-only, expand-before-send). Commit.

```bash
git add CLAUDE.md
git commit -m "docs: document project instructions + prompt files (P1)"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §3.1 instructions → Tasks 1–4; §3.2 prompt files → Tasks 5–8; §6 testing → each task's TDD steps + Task 9; exit criteria §7 → Task 9 smoke. Default-on flag → Task 2; size cap → Task 1; mtime self-update → Task 1 + Task 9 step 4; controller-only → Global Constraints + no planning-prompt task.
- **No `.github/copilot-instructions.md`** anywhere — honored (AGENTS.md only).
- **Type consistency:** `project_instructions_loader` (engine + factory), `project_instructions` (prompt fn kwarg), `ProjectInstructionsLoader.load()`, `expandPrompt → {found,text}`, message types `promptList`/`promptExpanded`/`listPrompts`/`expandPrompt` — used identically across Tasks 4/6/7/8.
- **Known soft spot (flagged in-task):** Task 7's positional `new ChatPanel(...)` argument insertion and Task 4's `create_controller_step` artifact-key assumption — both carry a NOTE telling the implementer to verify the exact call site rather than guess.
