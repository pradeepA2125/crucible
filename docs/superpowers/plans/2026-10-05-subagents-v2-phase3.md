# Sub-agents v2 Phase 3 — Settings › Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Settings section that lists every agent definition the backend sees (with warnings, trust and shadowing), and lets the user create, edit, duplicate, delete and trust definitions.

**Architecture:** The catalog loader gains a `report()` that keeps what it used to only log (warnings, skipped files, shadowed and inactive definitions). A new `agentd/api/agents_routes.py` serves `GET/PUT/DELETE /v1/agents…` and the trust routes, writing `.crucible/agents/<name>.md` through a symlink-safe writer and recording trust in `~/.crucible/trust.json`. editor-client gets four methods; the vscode-free settings handler gets five message cases; the webview gets an `AgentsSection` that loads its data separately from the settings snapshot.

**Tech Stack:** Python 3.13 / FastAPI / PyYAML; TypeScript (editor-client with Zod, VS Code extension, React webview with vitest + Testing Library).

**Spec:** `docs/superpowers/specs/2026-10-02-subagents-v2-design.md` rev 11 — §10 (this phase), §3.12 (trust model, already implemented in Phase 1), §11.3 phase 3. Read §10 in full before starting.

## Part index

| Part | Tasks | Area |
|---|---|---|
| A | 1–3 | Backend: catalog report, safe writer, routes |
| B | 4 | editor-client: schemas + four methods |
| C | 5–7 | Extension: host handler + deps, Agents list, editor form + trust dialog |
| D | 8 | Docs, full suites, live smoke |

## Global Constraints

- Name validation: the loader's `_NAME_RE` — `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` — for `name` and `rename_from`.
- Size caps: description 1 024 characters; persona 20 000 characters; `max_turns` 1…200 (the loader also clamps to 200); trust refused for files over 64 KB (`content` omitted for them).
- `tools: null` = all tools, distinct from `[]` — through Python, JSON, Zod and the webview mirror types.
- Writes go only to `<workspace>/.crucible/agents/`; `.claude` files and built-ins are read-only through the API.
- The workspace is the **backend's** (`CRUCIBLE_WORKSPACE_PATH`, frozen at startup, the same one the controller's catalog uses) — never a client-supplied path. (Deliberate: the write routes must not take a directory from the request.)
- Trust records are keyed by the **unresolved** path string the loader produces (`str(Path(workspace) / ".crucible" / "agents" / "<name>.md")` or the `.claude` equivalent) — the loader looks them up by exactly that string.
- §10 and §3.12 have no flag of their own: they apply whenever `CRUCIBLE_SUBAGENTS_ENABLED` is on. With it off, `GET /v1/agents` returns empty lists and the write routes answer 409.
- Every new GET route joins `tests/test_get_routes_read_only.py`'s reviewed list.
- Settings design system: `.surface-card`, `.menu-item`, semantic tints (`reference_webview_design_system`); never `--color-bg-1/2` (undefined).
- Pytest: never `-q`, never piped; `pytest --color=no … > out.txt 2>&1; echo exit=$?`.
- Commits: `type(scope): short description`, ending with the two attribution lines. Never push.

## Review Focus

1. **Saving an agent whose name is already defined by a capped `.claude/agents` file** — the `.crucible` file is written, trusted and wins; the `.claude` row shows `overridden by <path>`. Pinned in Task 3 (`test_crucible_save_overrides_claude_definition`).
2. **A name that exists only in a nested `.crucible/agents/sub/x.md`** — PUT and DELETE answer 409 naming that file, never write a second, losing definition. Pinned in Task 3 (`test_put_refuses_name_defined_in_subdirectory`).
3. **Rename (`rename_from`) to a name that already exists** — 409, and the old file stays. Pinned in Task 3 (`test_rename_onto_existing_name_is_refused`).
4. **The trust dialog after the file changed underneath it** — 409 "the file changed since you reviewed it", and the list refreshes so the user sees the new content. Pinned in Task 3 (`test_trust_rejects_changed_file`) and Task 5 (`trustAgent conflict refreshes the list`).
5. **An agent named `trust`** — must not collide with `DELETE /v1/agents/trust`; the name is refused by the write route with a clear message. Pinned in Task 3 (`test_reserved_name_trust_is_refused`).

---
# Part A — Backend (`services/agentd-py`)

### Task 1: The catalog report — keep what the loader used to only log

**Files:**
- Modify: `agentd/subagents/definitions.py` (add `warnings` to `AgentDefinition`)
- Modify: `agentd/subagents/agent_files.py` (full replacement below)
- Test: `tests/test_agent_catalog_report.py`

**Interfaces:**
- Produces:
  - `AgentDefinition.warnings: tuple[str, ...] = ()`
  - `agent_files.NAME_RE` (public alias of `_NAME_RE`), `DESC_MAX = 1024`, `MAX_TURNS_CAP = 200`, `CONTENT_MAX_BYTES = 65536`
  - `agent_files.NATIVE_TOOL_NAMES: frozenset[str]`, `agent_files.CC_TOOLS: dict[str, str]` (Claude Code → Crucible)
  - `parse_agent_file(path: Path, warnings: list[str] | None = None) -> AgentDefinition | None` — on `None` the last entry appended to `warnings` is the reason
  - `trust_clamped_permission(d: AgentDefinition) -> Permission` — `acceptEdits` → `default` when capped; anything else unchanged
  - `@dataclass(frozen=True) class CatalogEntry: definition: AgentDefinition; source_kind: str; path: str | None; active: bool; shadowed_by: str | None; content: str | None` (`source_kind` ∈ `crucible`, `claude`, `user_claude`, `builtin`)
  - `@dataclass(frozen=True) class SkippedFile: path: str; reason: str`
  - `@dataclass(frozen=True) class CatalogReport: entries: tuple[CatalogEntry, ...]; skipped: tuple[SkippedFile, ...]`
  - `AgentCatalogLoader.report() -> CatalogReport` (same cache and signature as `load()`; `load()` is unchanged in behaviour)

- [ ] **Step 1: Write the failing tests** (`tests/test_agent_catalog_report.py`)

```python
"""The catalog report behind Settings › Agents (spec §10.1): warnings are kept, skipped
files are listed, shadowed and inactive definitions stay visible."""
from __future__ import annotations

import hashlib
from pathlib import Path

from agentd.subagents.agent_files import (
    AgentCatalogLoader,
    parse_agent_file,
    trust_clamped_permission,
)
from agentd.subagents.trust import TrustStore


def _md(name: str, extra: str = "", body: str = "Persona.") -> str:
    return f"---\nname: {name}\ndescription: d {name}\n{extra}---\n{body}\n"


def _put(root: Path, name: str, text: str, filename: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / (filename or f"{name}.md")
    path.write_text(text, encoding="utf-8")
    return path


def _loader(tmp_path: Path) -> tuple[AgentCatalogLoader, Path, TrustStore]:
    ws = tmp_path / "ws"
    ws.mkdir()
    store = TrustStore(tmp_path / "trust.json")
    return AgentCatalogLoader(ws, user_agents_dir=tmp_path / "home", trust_store=store), ws, store


def _trust(store: TrustStore, ws: Path, path: Path) -> None:
    store.trust(str(ws), str(path), hashlib.sha256(path.read_bytes()).hexdigest())


def _entry(report, name: str, kind: str):
    return next(e for e in report.entries
                if e.definition.name == name and e.source_kind == kind)


def test_parse_records_warnings(tmp_path: Path) -> None:
    extra = ("tools: Read, WebFetch, frobnicate\nmodel: sonnet\n"
             "permissionMode: yolo\nmaxTurns: 500\n")
    path = _put(tmp_path, "w", _md("w", extra))
    warnings: list[str] = []
    d = parse_agent_file(path, warnings)
    assert d is not None
    assert d.max_turns == 200
    text = " | ".join(d.warnings)
    assert "WebFetch" in text and "frobnicate" in text and "sonnet" in text
    assert "yolo" in text and "clamped to 200" in text
    assert list(d.warnings) == warnings


def test_mcp_and_native_tools_are_not_unknown(tmp_path: Path) -> None:
    path = _put(tmp_path, "m", _md("m", "tools: mcp__github, query_graph, Bash, recall\n"))
    d = parse_agent_file(path)
    assert d is not None and d.warnings == ()


def test_skipped_files_carry_a_reason(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    root = ws / ".crucible" / "agents"
    _put(root, "nofront", "just text\n")
    _put(root, "noname", "---\ndescription: d\n---\nx\n")
    _put(root, "dup", _md("dup"))
    _put(root, "dup", _md("dup"), filename="dup-copy.md")
    report = loader.report()
    reasons = {Path(s.path).name: s.reason for s in report.skipped}
    assert "frontmatter" in reasons["nofront.md"]
    assert "name" in reasons["noname.md"]
    assert reasons["dup.md" if "dup.md" in reasons else "dup-copy.md"].startswith("duplicate of ")


def test_capped_file_shadowing_a_builtin_is_listed_inactive(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    _put(ws / ".claude" / "agents", "explore", _md("explore"))
    report = loader.report()
    file_row = _entry(report, "explore", "claude")
    assert file_row.active is False
    assert any("shadows the built-in" in w for w in file_row.definition.warnings)
    assert _entry(report, "explore", "builtin").active is True
    assert loader.load()["explore"].source == "built-in"


def test_trusted_file_overriding_a_builtin(tmp_path: Path) -> None:
    loader, ws, store = _loader(tmp_path)
    path = _put(ws / ".crucible" / "agents", "explore", _md("explore"))
    _trust(store, ws, path)
    report = loader.report()
    assert _entry(report, "explore", "builtin").shadowed_by == str(path)
    file_row = _entry(report, "explore", "crucible")
    assert file_row.active
    assert any("overrides the built-in" in w for w in file_row.definition.warnings)


def test_lower_precedence_definition_is_shadowed(tmp_path: Path) -> None:
    loader, ws, store = _loader(tmp_path)
    top = _put(ws / ".crucible" / "agents", "helper", _md("helper"))
    _put(ws / ".claude" / "agents", "helper", _md("helper"))
    _trust(store, ws, top)
    report = loader.report()
    low = _entry(report, "helper", "claude")
    assert (low.active, low.shadowed_by) == (False, str(top))


def test_capped_accept_edits_is_clamped_and_warned(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    _put(ws / ".claude" / "agents", "fast", _md("fast", "permissionMode: acceptEdits\n"))
    row = _entry(loader.report(), "fast", "claude")
    assert row.definition.trust == "capped"
    assert trust_clamped_permission(row.definition) == "default"
    assert any("acceptEdits runs as default" in w for w in row.definition.warnings)


def test_content_is_the_exact_hashed_text_and_omitted_when_huge(tmp_path: Path) -> None:
    loader, ws, _ = _loader(tmp_path)
    small = _put(ws / ".crucible" / "agents", "small", _md("small"))
    _put(ws / ".crucible" / "agents", "big", _md("big", body="x" * 70000))
    report = loader.report()
    row = _entry(report, "small", "crucible")
    assert row.content == small.read_text(encoding="utf-8")
    assert hashlib.sha256(row.content.encode()).hexdigest() == row.definition.content_sha256
    assert _entry(report, "big", "crucible").content is None


def test_builtins_have_no_path_and_user_files_are_user_claude(tmp_path: Path) -> None:
    loader, _ws, _ = _loader(tmp_path)
    _put(tmp_path / "home", "mine", _md("mine"))
    report = loader.report()
    assert _entry(report, "general-purpose", "builtin").path is None
    assert _entry(report, "mine", "user_claude").definition.trust == "trusted"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_agent_catalog_report.py --color=no > /tmp/p3t1.txt 2>&1; echo exit=$?; tail -5 /tmp/p3t1.txt`
Expected: exit≠0 (`cannot import name 'trust_clamped_permission'`).

- [ ] **Step 3: `definitions.py`**

Add the last field of `AgentDefinition`:

```python
    content_sha256: str = ""      # the file's hash when loaded; empty for built-ins
    # What the loader noticed (spec §10.1): shown in Settings, never fatal.
    warnings: tuple[str, ...] = ()
```

- [ ] **Step 4: Replace `agentd/subagents/agent_files.py`**

```python
"""Agent definition files (spec §9): Claude Code–compatible Markdown with YAML frontmatter.

Parsing is best-effort by design: a malformed file is skipped (or a malformed field
ignored) with a warning, and loading never raises into a turn. Warnings are logged AND
kept on the definition, and skipped files are kept with a reason, so Settings › Agents can
show them (spec §10.1).
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from agentd.prompting.tagged import Permission
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition
from agentd.subagents.trust import TrustStore

logger = logging.getLogger(__name__)

DESC_MAX = 1024
MAX_TURNS_CAP = 200
# Trust shows the user the exact bytes it records; past this the dialog would be unusable.
CONTENT_MAX_BYTES = 65536
# The name lands in a JSON-schema enum, the UI chip, log lines and a file name.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NAME_RE = NAME_RE  # pre-Phase-3 name, kept for existing imports
_PERMISSIONS: dict[str, Permission] = {
    "default": "default", "acceptEdits": "acceptEdits", "dontAsk": "dontAsk", "plan": "plan",
    "bypassPermissions": "default",  # spec §5.6: there is no bypass mode here
}
# Claude Code family aliases name Anthropic models; on any other provider they mean
# nothing, and imported Claude Code agents use them routinely (spec §5.4).
_CC_MODEL_ALIASES = frozenset({"sonnet", "opus", "haiku", "fable"})
# Claude Code tool name → Crucible name (spec §9.3). "edit" is the edit ACTION type, not a
# registry tool: permissions.definition_allows_edit reads it.
CC_TOOLS: dict[str, str] = {
    "Read": "read_file", "Grep": "search_code", "Glob": "list_directory",
    "LS": "list_directory", "Bash": "run_command", "Edit": "edit", "Write": "edit",
    "MultiEdit": "edit", "NotebookEdit": "edit", "Agent": "dispatch_agents",
    "Task": "dispatch_agents", "TodoWrite": "write_todos", "Skill": "read_skill",
}
_UNMAPPED_CC_TOOLS = frozenset({"WebFetch", "WebSearch"})
# Every Crucible-native tool name a definition may list (spec §10.1 "known" names). MCP
# tools are matched by the mcp__ prefix: their exact names exist only at runtime.
NATIVE_TOOL_NAMES = frozenset({
    "read_file", "search_code", "list_directory", "run_command", "search_semantic",
    "query_graph", "write_todos", "read_skill", "remember", "recall", "edit",
    "find_binary", "init_workspace", "read_env_profile", "setup_env",
    "start_session", "write_stdin", "kill_session", "list_sessions",
    "dispatch_agents", "wait_agents", "message_agent", "stop_agent",
})
_SOURCE_KINDS = ("crucible", "claude", "user_claude")


def _warn(warnings: list[str] | None, path: Path, message: str) -> None:
    logger.warning("[agents] %s: %s", path, message)
    if warnings is not None:
        warnings.append(message)


def _split_frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    """(frontmatter mapping, body) — None when the file has no valid YAML mapping on top."""
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None, text
    return (data if isinstance(data, dict) else None), parts[2]


def _name_list(
    raw: object, field: str, path: Path, warnings: list[str] | None,
) -> list[str] | None:
    """A Claude Code list field: a YAML list or a comma-separated string; None if absent
    or unusable (a wrong type is ignored with a warning, not fatal to the file)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, list):
        items = [str(item) for item in raw]
    else:
        _warn(warnings, path, f"{field!r} must be a list or a comma-separated string — ignored")
        return None
    return [item.strip() for item in items if item.strip()]


def map_tool_names(
    names: list[str], path: Path, warnings: list[str] | None = None,
) -> frozenset[str]:
    """Claude Code tool names → Crucible names; Crucible-native names pass through.
    `mcp__<server>` and `mcp__<server>__*` both become the wildcard `mcp__<server>__*`."""
    mapped: set[str] = set()
    for name in names:
        if name in _UNMAPPED_CC_TOOLS:
            _warn(warnings, path, f"tool {name!r} has no Crucible equivalent — ignored "
                                  "(list the MCP tool names instead)")
            continue
        if name.startswith("mcp__") and name.count("__") == 1:
            name += "__*"
        crucible = CC_TOOLS.get(name, name)
        if not crucible.startswith("mcp__") and crucible not in NATIVE_TOOL_NAMES:
            _warn(warnings, path, f"unknown tool {name!r} — kept, but no tool has that name")
        mapped.add(crucible)
    return frozenset(mapped)


def _permission(raw: object, path: Path, warnings: list[str] | None) -> Permission:
    if raw is None:
        return "default"
    permission = _PERMISSIONS.get(str(raw).strip())
    if permission is None:
        _warn(warnings, path, f"unknown permissionMode {raw!r} — using default")
        return "default"
    return permission


def _model(raw: object, path: Path, warnings: list[str] | None) -> str:
    model = str(raw).strip() if raw is not None else ""
    if not model or model == "inherit":
        return "inherit"
    if model in _CC_MODEL_ALIASES:
        _warn(warnings, path, f"model {model!r} is a Claude Code alias — inheriting the "
                              "dispatcher's model")
        return "inherit"
    return model


def _max_turns(raw: object, path: Path, warnings: list[str] | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = 0
    if value < 1:
        _warn(warnings, path, f"maxTurns {raw!r} is not a positive integer — ignored")
        return None
    if value > MAX_TURNS_CAP:
        _warn(warnings, path, f"maxTurns {value} clamped to {MAX_TURNS_CAP}")
        return MAX_TURNS_CAP
    return value


def parse_agent_file(path: Path, warnings: list[str] | None = None) -> AgentDefinition | None:
    """One agent definition, or None when the file can't define one — the last message
    appended to `warnings` is then the reason. Honored frontmatter (spec §9.2): name,
    description, tools, disallowedTools, model, permissionMode, maxTurns, skills."""
    collected: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _warn(collected, path, f"cannot read the file: {exc}")
        return _skip(warnings, collected)
    front, body = _split_frontmatter(text)
    if front is None:
        _warn(collected, path, "missing or invalid YAML frontmatter — skipped")
        return _skip(warnings, collected)
    name, description = front.get("name"), front.get("description")
    if not isinstance(name, str) or not NAME_RE.match(name.strip()):
        _warn(collected, path, "'name' is missing or not [A-Za-z0-9_.-] — skipped")
        return _skip(warnings, collected)
    if not isinstance(description, str) or not description.strip():
        _warn(collected, path, "missing 'description' — skipped")
        return _skip(warnings, collected)
    tools = _name_list(front.get("tools"), "tools", path, collected)
    disallowed = _name_list(front.get("disallowedTools"), "disallowedTools", path, collected)
    skills = _name_list(front.get("skills"), "skills", path, collected)
    definition = AgentDefinition(
        name=name.strip(),
        description=" ".join(description.split())[:DESC_MAX],
        permission=_permission(front.get("permissionMode"), path, collected),
        tools=map_tool_names(tools, path, collected) if tools is not None else None,
        persona=body.strip(),
        model=_model(front.get("model"), path, collected),
        max_turns=_max_turns(front.get("maxTurns"), path, collected),
        disallowed_tools=(
            map_tool_names(disallowed, path, collected) if disallowed is not None
            else frozenset()),
        skills=tuple(skills or ()),
        source=str(path),
        warnings=tuple(collected))
    if warnings is not None:
        warnings.extend(collected)
    return definition


def _skip(warnings: list[str] | None, collected: list[str]) -> None:
    if warnings is not None:
        warnings.extend(collected)
    return None


def trust_clamped_permission(d: AgentDefinition) -> Permission:
    """The permission a definition runs with today (spec §3.12): an untrusted acceptEdits
    file runs as default. dontAsk keeps its no-ask commands (its edits ask anyway)."""
    if d.trust == "capped" and d.permission == "acceptEdits":
        return "default"
    return d.permission


def _trust_warning(d: AgentDefinition) -> str | None:
    if d.trust != "capped":
        return None
    if d.permission == "acceptEdits":
        return ("untrusted: acceptEdits runs as default and every edit asks until you trust "
                "this file")
    if d.permission == "dontAsk":
        return "untrusted: dontAsk keeps its no-ask commands, but every edit asks until trusted"
    return "untrusted: every edit asks until you trust this file"


@dataclass(frozen=True)
class CatalogEntry:
    definition: AgentDefinition
    source_kind: str            # crucible | claude | user_claude | builtin
    path: str | None            # None for built-ins
    active: bool                # False: shadowed, or a capped file hiding behind a built-in
    shadowed_by: str | None     # the path (or "built-in") that wins this name
    content: str | None         # the exact hashed text; None for built-ins and huge files


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str


@dataclass(frozen=True)
class CatalogReport:
    entries: tuple[CatalogEntry, ...]
    skipped: tuple[SkippedFile, ...]


class AgentCatalogLoader:
    """The agents `dispatch_agents` offers (spec §9.1): `.crucible/agents/`, then
    `.claude/agents/`, then `~/.claude/agents/`, then the built-ins. A name wins at its
    highest-precedence source. Directories are scanned recursively for `*.md`.

    Cached on the (path, mtime_ns) of every file, not the roots' mtimes: a directory's
    mtime moves only when its direct entries change, so a root-keyed cache (the
    SkillCatalogLoader discipline) would never see an edit to a nested file."""

    def __init__(
        self, workspace_path: Path | str, *, user_agents_dir: Path | None = None,
        trust_store: TrustStore | None = None,
    ) -> None:
        workspace = Path(workspace_path)
        self._workspace = str(workspace)
        self._trust = trust_store or TrustStore()
        self._roots: tuple[Path, ...] = (
            workspace / ".crucible" / "agents",
            workspace / ".claude" / "agents",
            user_agents_dir if user_agents_dir is not None else Path.home() / ".claude" / "agents",
        )
        self._lock = threading.Lock()
        self._signature: tuple[tuple[str, int], ...] | None = None
        self._cached: tuple[dict[str, AgentDefinition], CatalogReport] | None = None

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def load(self) -> dict[str, AgentDefinition]:
        """The catalog, sorted by name. Treat it as read-only: it is the cached object."""
        return self._current()[0]

    def report(self) -> CatalogReport:
        """Everything the loader saw, for Settings (spec §10.1). Same cache as load()."""
        return self._current()[1]

    def _current(self) -> tuple[dict[str, AgentDefinition], CatalogReport]:
        with self._lock:
            files = self._files()
            # Trust changes do not move file mtimes, so the trust store's version is part of
            # the signature (spec §3.12).
            signature = (*((str(path), mtime) for _, path, mtime in files),
                         ("trust", self._trust.version()))
            if self._cached is None or signature != self._signature:
                self._cached = self._build(files)
                self._signature = signature
            return self._cached

    def _files(self) -> list[tuple[int, Path, int]]:
        found: list[tuple[int, Path, int]] = []
        for index, root in enumerate(self._roots):
            if not root.is_dir():
                continue
            try:
                paths = sorted(root.rglob("*.md"))
            except OSError as exc:
                logger.warning("[agents] cannot scan %s: %s", root, exc)
                continue
            for path in paths:
                try:
                    if path.is_file():
                        found.append((index, path, path.stat().st_mtime_ns))
                except OSError:
                    continue
        return found

    def _build(
        self, files: list[tuple[int, Path, int]],
    ) -> tuple[dict[str, AgentDefinition], CatalogReport]:
        catalog: dict[str, AgentDefinition] = {}
        origin: dict[str, int] = {}
        entries: list[CatalogEntry] = []
        skipped: list[SkippedFile] = []
        for index, path, _ in files:
            reasons: list[str] = []
            definition = parse_agent_file(path, reasons)
            if definition is None:
                skipped.append(SkippedFile(str(path), reasons[-1] if reasons
                                           else "not an agent definition"))
                continue
            try:
                raw = path.read_bytes()
            except OSError as exc:
                skipped.append(SkippedFile(str(path), f"cannot read the file: {exc}"))
                continue
            digest = hashlib.sha256(raw).hexdigest()
            in_workspace = index < 2  # .crucible/agents and .claude/agents (spec §3.12)
            trusted = (not in_workspace) or self._trust.is_trusted(
                self._workspace, str(path), digest)
            definition = replace(definition, content_sha256=digest,
                                 trust="trusted" if trusted else "capped")
            content = raw.decode("utf-8") if len(raw) <= CONTENT_MAX_BYTES else None
            extra = [w for w in (_trust_warning(definition),) if w]
            name = definition.name
            if definition.trust == "capped" and name in BUILTIN_AGENTS:
                logger.warning("[agents] %s: untrusted definition shadows the built-in %r — the "
                               "built-in stays active until the file is trusted", path, name)
                extra.append(f"inactive — shadows the built-in {name!r}, untrusted")
                entries.append(CatalogEntry(
                    replace(definition, warnings=definition.warnings + tuple(extra)),
                    _SOURCE_KINDS[index], str(path), False, "built-in", content))
                continue
            if name in catalog:
                if origin[name] == index:
                    reason = f"duplicate of {catalog[name].source}"
                    logger.warning("[agents] %s: %s — skipped", path, reason)
                    skipped.append(SkippedFile(str(path), reason))
                else:  # a lower-precedence root never overrides a higher one
                    entries.append(CatalogEntry(
                        replace(definition, warnings=definition.warnings + tuple(extra)),
                        _SOURCE_KINDS[index], str(path), False, catalog[name].source, content))
                continue
            if name in BUILTIN_AGENTS:
                extra.append(f"overrides the built-in {name!r}")
            definition = replace(definition, warnings=definition.warnings + tuple(extra))
            catalog[name] = definition
            origin[name] = index
            entries.append(CatalogEntry(
                definition, _SOURCE_KINDS[index], str(path), True, None, content))
        for name, builtin in BUILTIN_AGENTS.items():
            winner = catalog.setdefault(name, builtin)
            shadowed = winner is not builtin
            entries.append(CatalogEntry(
                builtin, "builtin", None, not shadowed,
                winner.source if shadowed else None, None))
        entries.sort(key=lambda e: (e.definition.name, not e.active, e.source_kind))
        return dict(sorted(catalog.items())), CatalogReport(tuple(entries), tuple(skipped))
```

- [ ] **Step 5: Run the new tests and every existing agent-files test**

Run: `.venv/bin/pytest tests/test_agent_catalog_report.py tests/test_agent_files.py tests/test_agent_trust.py --color=no > /tmp/p3t1.txt 2>&1; echo exit=$?; tail -5 /tmp/p3t1.txt`
Expected: `exit=0`. An existing test that asserted a `caplog` message text should still pass — `_warn` logs `[agents] <path>: <message>`; if one checks the exact old wording, update it to the new message (the wording moved into the message strings above, unchanged in meaning).

- [ ] **Step 6: Commit**

```bash
git add agentd/subagents/definitions.py agentd/subagents/agent_files.py tests/test_agent_catalog_report.py
git commit -m "feat(subagents): catalog report keeps warnings, skipped and shadowed definitions"
```

### Task 2: The definition writer — render and write `.crucible/agents/<name>.md` safely

**Files:**
- Create: `agentd/subagents/agent_writer.py`
- Test: `tests/test_agent_writer.py`

**Interfaces:**
- Consumes: `NAME_RE`, `DESC_MAX`, `MAX_TURNS_CAP`, `parse_agent_file` (Task 1).
- Produces:
  - `class AgentInputError(ValueError)` (→ HTTP 400), `class AgentConflictError(RuntimeError)` (→ 409), `class AgentNotFoundError(LookupError)` (→ 404)
  - `PERSONA_MAX = 20000`, `RESERVED_NAMES = frozenset({"trust"})`, `PERMISSIONS = ("default", "acceptEdits", "plan", "dontAsk")`
  - `@dataclass(frozen=True) class AgentFields: name: str; description: str; persona: str; tools: tuple[str, ...] | None; disallowed_tools: tuple[str, ...]; permission: str; model: str; max_turns: int | None; skills: tuple[str, ...]`
  - `validate_fields(f: AgentFields) -> None` (raises `AgentInputError`)
  - `render_agent_markdown(f: AgentFields) -> str`
  - `agents_dir(workspace: Path) -> Path` — creates and checks `<workspace>/.crucible/agents`
  - `write_agent_file(workspace: Path, name: str, text: str) -> Path` — returns the unresolved `workspace/.crucible/agents/<name>.md`
  - `delete_agent_file(workspace: Path, name: str) -> Path`

- [ ] **Step 1: Write the failing tests** (`tests/test_agent_writer.py`)

```python
"""Writing agent definitions from Settings (spec §10.1): Claude Code names in the file,
symlink-safe writes confined to <workspace>/.crucible/agents."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentd.subagents.agent_files import parse_agent_file
from agentd.subagents.agent_writer import (
    AgentConflictError,
    AgentFields,
    AgentInputError,
    AgentNotFoundError,
    delete_agent_file,
    render_agent_markdown,
    validate_fields,
    write_agent_file,
)


def _fields(**over) -> AgentFields:
    base = dict(name="helper", description="Helps.", persona="You help.\n\nCarefully.",
                tools=None, disallowed_tools=(), permission="default", model="inherit",
                max_turns=None, skills=())
    base.update(over)
    return AgentFields(**base)


def test_render_round_trips_through_the_parser(tmp_path: Path) -> None:
    f = _fields(tools=("read_file", "search_code", "edit", "query_graph", "mcp__github__*"),
                disallowed_tools=("run_command",), permission="acceptEdits",
                model="gpt-5", max_turns=30, skills=("tdd",))
    text = render_agent_markdown(f)
    assert "Read" in text and "Grep" in text and "Edit" in text and "mcp__github" in text
    assert "mcp__github__*" not in text
    path = tmp_path / "helper.md"
    path.write_text(text, encoding="utf-8")
    d = parse_agent_file(path)
    assert d is not None
    assert d.tools == frozenset(
        {"read_file", "search_code", "edit", "query_graph", "mcp__github__*"})
    assert d.disallowed_tools == frozenset({"run_command"})
    assert (d.permission, d.model, d.max_turns, d.skills) == ("acceptEdits", "gpt-5", 30, ("tdd",))
    assert d.persona == "You help.\n\nCarefully."
    assert d.warnings == ()


def test_all_tools_omits_the_key_and_empty_list_is_kept(tmp_path: Path) -> None:
    assert "tools:" not in render_agent_markdown(_fields(tools=None))
    path = tmp_path / "none.md"
    path.write_text(render_agent_markdown(_fields(tools=())), encoding="utf-8")
    d = parse_agent_file(path)
    assert d is not None and d.tools == frozenset()


@pytest.mark.parametrize("over", [
    {"name": "bad name"}, {"name": ""}, {"name": "x" * 65}, {"name": "trust"},
    {"description": "  "}, {"description": "d" * 1025}, {"description": "a---b"},
    {"persona": "p" * 20001}, {"permission": "bypassPermissions"},
    {"max_turns": 0}, {"max_turns": 201}, {"model": "a\nb"},
    {"tools": ("read_file\n",)},
])
def test_validation_rejects(over) -> None:
    with pytest.raises(AgentInputError):
        validate_fields(_fields(**over))


def test_write_and_delete(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    path = write_agent_file(ws, "helper", "---\nname: helper\ndescription: d\n---\nx\n")
    assert path == ws / ".crucible" / "agents" / "helper.md"
    assert path.read_text().startswith("---")
    assert [p.name for p in path.parent.iterdir()] == ["helper.md"]  # no temp left behind
    write_agent_file(ws, "helper", "---\nname: helper\ndescription: e\n---\ny\n")
    assert "description: e" in path.read_text()
    assert delete_agent_file(ws, "helper") == path
    assert not path.exists()
    with pytest.raises(AgentNotFoundError):
        delete_agent_file(ws, "helper")


def test_refuses_a_symlinked_agents_dir(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".crucible").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws / ".crucible" / "agents")
    with pytest.raises(AgentConflictError):
        write_agent_file(ws, "helper", "x")
    assert list(elsewhere.iterdir()) == []


def test_refuses_a_symlinked_crucible_dir(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws / ".crucible")
    with pytest.raises(AgentConflictError):
        write_agent_file(ws, "helper", "x")
    assert list(elsewhere.iterdir()) == []


def test_refuses_a_symlinked_target(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    agents = ws / ".crucible" / "agents"
    agents.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    os.symlink(victim, agents / "helper.md")
    with pytest.raises(AgentConflictError):
        write_agent_file(ws, "helper", "x")
    with pytest.raises(AgentConflictError):
        delete_agent_file(ws, "helper")
    assert victim.read_text() == "keep"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_agent_writer.py --color=no > /tmp/p3t2.txt 2>&1; echo exit=$?; tail -5 /tmp/p3t2.txt`
Expected: exit≠0 (`No module named 'agentd.subagents.agent_writer'`).

- [ ] **Step 3: Implement `agentd/subagents/agent_writer.py`**

```python
"""Writing agent definitions from Settings (spec §10.1).

Files are written in Claude Code's dialect (Read/Grep/Bash…, `mcp__server`) so they stay
portable; Crucible-native names with no Claude Code equivalent are written verbatim. Writes
are confined to <workspace>/.crucible/agents and never follow a symlink.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import yaml

from agentd.subagents.agent_files import DESC_MAX, MAX_TURNS_CAP, NAME_RE

PERSONA_MAX = 20000
# "trust" would collide with the /v1/agents/trust routes.
RESERVED_NAMES = frozenset({"trust"})
PERMISSIONS = ("default", "acceptEdits", "plan", "dontAsk")
# Crucible → Claude Code (the reverse of agent_files.CC_TOOLS, one name each).
_TO_CC: dict[str, str] = {
    "read_file": "Read", "search_code": "Grep", "list_directory": "Glob",
    "run_command": "Bash", "edit": "Edit", "write_todos": "TodoWrite",
    "dispatch_agents": "Agent", "read_skill": "Skill",
}


class AgentInputError(ValueError):
    """The request's fields are invalid (HTTP 400)."""


class AgentConflictError(RuntimeError):
    """The write would be unsafe or would lose to another definition (HTTP 409)."""


class AgentNotFoundError(LookupError):
    """No such .crucible/agents file (HTTP 404)."""


@dataclass(frozen=True)
class AgentFields:
    name: str
    description: str
    persona: str
    tools: tuple[str, ...] | None   # None = all tools; () = none
    disallowed_tools: tuple[str, ...]
    permission: str
    model: str
    max_turns: int | None
    skills: tuple[str, ...]


def _one_line(value: str, what: str) -> None:
    # "---" would end the YAML frontmatter early (agent_files splits on it).
    if "\n" in value or "\r" in value or "---" in value:
        raise AgentInputError(f"{what} must be a single line without '---'")


def validate_fields(f: AgentFields) -> None:
    if not NAME_RE.match(f.name):
        raise AgentInputError("name must be 1-64 characters of A-Z a-z 0-9 _ . - "
                              "and start with a letter or digit")
    if f.name in RESERVED_NAMES:
        raise AgentInputError(f"{f.name!r} is a reserved name")
    if not f.description.strip():
        raise AgentInputError("description is required")
    if len(f.description) > DESC_MAX:
        raise AgentInputError(f"description is limited to {DESC_MAX} characters")
    _one_line(f.description, "description")
    if len(f.persona) > PERSONA_MAX:
        raise AgentInputError(f"persona is limited to {PERSONA_MAX} characters")
    if f.permission not in PERMISSIONS:
        raise AgentInputError(f"permission must be one of {', '.join(PERMISSIONS)}")
    if f.max_turns is not None and not 1 <= f.max_turns <= MAX_TURNS_CAP:
        raise AgentInputError(f"max_turns must be between 1 and {MAX_TURNS_CAP}")
    _one_line(f.model, "model")
    for item in (*(f.tools or ()), *f.disallowed_tools, *f.skills):
        if not item.strip():
            raise AgentInputError("tool and skill names must not be empty")
        _one_line(item, f"{item!r}")


def _cc_name(tool: str) -> str:
    if tool.startswith("mcp__") and tool.endswith("__*"):
        return tool[:-3]
    return _TO_CC.get(tool, tool)


def render_agent_markdown(f: AgentFields) -> str:
    front: dict[str, object] = {"name": f.name, "description": " ".join(f.description.split())}
    if f.tools is not None:  # absent = all tools; [] is a real, empty allow-list
        front["tools"] = sorted({_cc_name(t) for t in f.tools})
    if f.disallowed_tools:
        front["disallowedTools"] = sorted({_cc_name(t) for t in f.disallowed_tools})
    if f.permission != "default":
        front["permissionMode"] = f.permission
    if f.model and f.model != "inherit":
        front["model"] = f.model
    if f.max_turns is not None:
        front["maxTurns"] = f.max_turns
    if f.skills:
        front["skills"] = list(f.skills)
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{header}---\n{f.persona.strip()}\n"


def agents_dir(workspace: Path) -> Path:
    crucible = workspace / ".crucible"
    if crucible.is_symlink():
        raise AgentConflictError(f"{crucible} is a symlink; refusing to write through it")
    target = crucible / "agents"
    if target.is_symlink():
        raise AgentConflictError(f"{target} is a symlink; refusing to write through it")
    target.mkdir(parents=True, exist_ok=True)
    expected = workspace.resolve(strict=True) / ".crucible" / "agents"
    if target.resolve(strict=True) != expected:
        raise AgentConflictError(f"{target} resolves outside the workspace")
    return target


def write_agent_file(workspace: Path, name: str, text: str) -> Path:
    directory = agents_dir(workspace)
    target = directory / f"{name}.md"
    if target.is_symlink():
        raise AgentConflictError(f"{target} is a symlink; refusing to replace it")
    tmp = directory / f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, target)
    return target


def delete_agent_file(workspace: Path, name: str) -> Path:
    directory = agents_dir(workspace)
    target = directory / f"{name}.md"
    if target.is_symlink():
        raise AgentConflictError(f"{target} is a symlink; refusing to delete through it")
    if not target.is_file():
        raise AgentNotFoundError(f"no agent file {target}")
    target.unlink()
    return target
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_agent_writer.py --color=no > /tmp/p3t2.txt 2>&1; echo exit=$?; tail -3 /tmp/p3t2.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/agent_writer.py tests/test_agent_writer.py
git commit -m "feat(subagents): render and safely write .crucible/agents definitions"
```

### Task 3: `/v1/agents` routes

**Files:**
- Create: `agentd/api/agents_routes.py`
- Modify: `agentd/main.py` (include the router, before `install_auth(app, AUTH_STATE)`)
- Modify: `tests/test_get_routes_read_only.py` (add `("api/agents_routes.py", "/agents")`)
- Test: `tests/test_agents_routes.py`

**Interfaces:**
- Consumes: `AgentCatalogLoader.report()`, `CatalogEntry`, `trust_clamped_permission`, `NATIVE_TOOL_NAMES`, `CONTENT_MAX_BYTES`, `NAME_RE` (Task 1); everything in Task 2; `TrustStore` (`agentd/subagents/trust.py`); `CHILD_EXCLUDED_TOOLS` (`agentd/subagents/permissions.py`).
- Produces (all under `/v1`):
  - `GET /agents` → `{agents: [AgentJson…], skipped: [{path, reason}…], available_tools: [str…]}`; `AgentJson` = `name, description, tools (list|null), disallowed_tools, permission (effective), declared_permission, model, max_turns, skills, source, path, sha256, trust, active, warnings, shadowed_by, persona, content`
  - `PUT /agents/{name}` body `{description, persona, tools?, disallowed_tools?, permission?, model?, max_turns?, skills?, rename_from?}` → `{agent: AgentJson}`
  - `DELETE /agents/{name}` → `{ok: true}`
  - `POST /agents/trust` body `{path, sha256}` → `{ok: true}`; `DELETE /agents/trust` body `{path}` → `{ok: true}`
  - `build_agents_router(*, workspace: str, trust_store: TrustStore | None = None, user_agents_dir: Path | None = None, mcp_server_names: Callable[[], Iterable[str]] = lambda: (), enabled: Callable[[], bool] = is_subagents_enabled) -> APIRouter`
  - Status codes: 400 invalid input, 404 unknown file, 409 conflict (with `detail` naming the reason), 409 for every write while sub-agents are disabled.

- [ ] **Step 1: Write the failing tests** (`tests/test_agents_routes.py`)

```python
"""Settings › Agents routes (spec §10.1)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.agents_routes import build_agents_router
from agentd.subagents.trust import TrustStore


@pytest.fixture()
def env(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = TrustStore(tmp_path / "trust.json")
    enabled = {"on": True}
    app = FastAPI()
    app.include_router(build_agents_router(
        workspace=str(ws), trust_store=store, user_agents_dir=tmp_path / "home",
        mcp_server_names=lambda: ["github"], enabled=lambda: enabled["on"]))
    return TestClient(app), ws, store, enabled


def _body(**over):
    body = {"description": "Helps.", "persona": "You help.", "tools": None,
            "disallowed_tools": [], "permission": "default", "model": "inherit",
            "max_turns": None, "skills": []}
    body.update(over)
    return body


def _row(client, name: str, source: str | None = None) -> dict:
    agents = client.get("/v1/agents").json()["agents"]
    return next(a for a in agents
                if a["name"] == name and (source is None or a["source"] == source))


def test_list_shape_and_available_tools(env) -> None:
    client, _ws, _store, _ = env
    body = client.get("/v1/agents").json()
    assert set(body) == {"agents", "skipped", "available_tools"}
    gp = _row(client, "general-purpose")
    assert gp["source"] == "builtin" and gp["path"] is None and gp["tools"] is None
    assert "mcp__github__*" in body["available_tools"] and "edit" in body["available_tools"]
    assert "remember" not in body["available_tools"]  # children never get it


def test_put_writes_a_trusted_definition(env) -> None:
    client, ws, _store, _ = env
    resp = client.put("/v1/agents/helper", json=_body(
        tools=["read_file", "edit"], permission="acceptEdits"))
    assert resp.status_code == 200, resp.text
    row = resp.json()["agent"]
    assert row["trust"] == "trusted" and row["active"] and row["permission"] == "acceptEdits"
    assert row["tools"] == ["edit", "read_file"]
    path = ws / ".crucible" / "agents" / "helper.md"
    assert row["path"] == str(path)
    assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert row["content"] == path.read_text()


def test_tools_null_is_distinct_from_empty(env) -> None:
    client, _ws, _store, _ = env
    client.put("/v1/agents/a", json=_body(tools=None))
    client.put("/v1/agents/b", json=_body(tools=[]))
    assert _row(client, "a")["tools"] is None
    assert _row(client, "b")["tools"] == []


@pytest.mark.parametrize("name,over", [
    ("bad name", {}), ("ok", {"max_turns": 0}), ("ok", {"max_turns": 201}),
    ("ok", {"persona": "p" * 20001}), ("ok", {"permission": "yolo"}),
])
def test_put_validation_400(env, name, over) -> None:
    client, _ws, _store, _ = env
    assert client.put(f"/v1/agents/{name}", json=_body(**over)).status_code == 400


def test_reserved_name_trust_is_refused(env) -> None:
    client, _ws, _store, _ = env
    resp = client.put("/v1/agents/trust", json=_body())
    assert resp.status_code in (400, 405)
    assert not (env[1] / ".crucible" / "agents" / "trust.md").exists()


def test_crucible_save_overrides_claude_definition(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    (claude / "helper.md").write_text("---\nname: helper\ndescription: d\n---\nold\n")
    assert _row(client, "helper", "claude")["trust"] == "capped"
    assert client.put("/v1/agents/helper", json=_body()).status_code == 200
    assert _row(client, "helper", "crucible")["active"] is True
    low = _row(client, "helper", "claude")
    assert low["active"] is False and low["shadowed_by"] == str(ws / ".crucible/agents/helper.md")


def test_put_refuses_name_defined_in_subdirectory(env) -> None:
    client, ws, store, _ = env
    sub = ws / ".crucible" / "agents" / "team"
    sub.mkdir(parents=True)
    nested = sub / "x.md"
    nested.write_text("---\nname: x\ndescription: d\n---\nbody\n")
    resp = client.put("/v1/agents/x", json=_body())
    assert resp.status_code == 409 and str(nested) in resp.json()["detail"]
    assert client.delete("/v1/agents/x").status_code == 409
    assert not (ws / ".crucible" / "agents" / "x.md").exists()


def test_rename_moves_the_file_and_trust(env) -> None:
    client, ws, store, _ = env
    client.put("/v1/agents/old", json=_body())
    old = ws / ".crucible" / "agents" / "old.md"
    resp = client.put("/v1/agents/new", json=_body(rename_from="old"))
    assert resp.status_code == 200
    assert not old.exists()
    assert store._read().get(str(ws), {}).get(str(old)) is None
    assert _row(client, "new")["trust"] == "trusted"


def test_rename_onto_existing_name_is_refused(env) -> None:
    client, ws, _store, _ = env
    client.put("/v1/agents/a", json=_body())
    client.put("/v1/agents/b", json=_body(description="B"))
    resp = client.put("/v1/agents/b", json=_body(rename_from="a"))
    assert resp.status_code == 409
    assert (ws / ".crucible" / "agents" / "a.md").exists()
    assert _row(client, "b")["description"] == "B"


def test_delete_removes_file_and_trust(env) -> None:
    client, ws, store, _ = env
    client.put("/v1/agents/gone", json=_body())
    path = ws / ".crucible" / "agents" / "gone.md"
    assert client.delete("/v1/agents/gone").json() == {"ok": True}
    assert not path.exists() and store._read().get(str(ws), {}).get(str(path)) is None
    assert client.delete("/v1/agents/gone").status_code == 404


def test_builtins_and_claude_files_are_read_only(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    (claude / "c.md").write_text("---\nname: c\ndescription: d\n---\nbody\n")
    assert client.delete("/v1/agents/explore").status_code == 409
    assert client.delete("/v1/agents/c").status_code == 409


def test_trust_records_the_reviewed_hash(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    path = claude / "c.md"
    path.write_text("---\nname: c\ndescription: d\npermissionMode: acceptEdits\n---\nbody\n")
    row = _row(client, "c")
    assert row["trust"] == "capped" and row["permission"] == "default"
    assert row["declared_permission"] == "acceptEdits"
    resp = client.post("/v1/agents/trust", json={"path": row["path"], "sha256": row["sha256"]})
    assert resp.json() == {"ok": True}
    assert _row(client, "c")["trust"] == "trusted"
    untrust = client.request("DELETE", "/v1/agents/trust", json={"path": row["path"]})
    assert untrust.json() == {"ok": True}
    assert _row(client, "c")["trust"] == "capped"


def test_trust_rejects_changed_file(env) -> None:
    client, ws, _store, _ = env
    claude = ws / ".claude" / "agents"
    claude.mkdir(parents=True)
    path = claude / "c.md"
    path.write_text("---\nname: c\ndescription: d\n---\nbody\n")
    row = _row(client, "c")
    path.write_text("---\nname: c\ndescription: d\n---\nswapped\n")
    resp = client.post("/v1/agents/trust", json={"path": row["path"], "sha256": row["sha256"]})
    assert resp.status_code == 409 and "changed since you reviewed it" in resp.json()["detail"]
    assert _row(client, "c")["trust"] == "capped"


def test_trust_refuses_unknown_and_user_files(env, tmp_path) -> None:
    client, _ws, _store, _ = env
    resp = client.post("/v1/agents/trust", json={"path": "/etc/passwd", "sha256": "0" * 64})
    assert resp.status_code == 404


def test_disabled(env) -> None:
    client, _ws, _store, enabled = env
    enabled["on"] = False
    assert client.get("/v1/agents").json() == {"agents": [], "skipped": [], "available_tools": []}
    assert client.put("/v1/agents/helper", json=_body()).status_code == 409
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_agents_routes.py --color=no > /tmp/p3t3.txt 2>&1; echo exit=$?; tail -5 /tmp/p3t3.txt`
Expected: exit≠0 (`No module named 'agentd.api.agents_routes'`).

- [ ] **Step 3: Implement `agentd/api/agents_routes.py`**

```python
"""Settings › Agents routes (spec §10.1).

The workspace is the backend's own (CRUCIBLE_WORKSPACE_PATH, the one the controller's
catalog reads) — never a path from the request, since these routes write files. Trust is
keyed by the path string the catalog loader produces, so a recorded hash is found again.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentd.chat.controller_factory import is_subagents_enabled
from agentd.subagents.agent_files import (
    CONTENT_MAX_BYTES,
    NAME_RE,
    NATIVE_TOOL_NAMES,
    AgentCatalogLoader,
    CatalogEntry,
    CatalogReport,
    trust_clamped_permission,
)
from agentd.subagents.agent_writer import (
    AgentConflictError,
    AgentFields,
    AgentInputError,
    AgentNotFoundError,
    delete_agent_file,
    render_agent_markdown,
    validate_fields,
    write_agent_file,
)
from agentd.subagents.permissions import CHILD_EXCLUDED_TOOLS
from agentd.subagents.trust import TrustStore


class AgentBody(BaseModel):
    description: str
    persona: str = ""
    tools: list[str] | None = None
    disallowed_tools: list[str] = []
    permission: str = "default"
    model: str = "inherit"
    max_turns: int | None = None
    skills: list[str] = []
    rename_from: str | None = None


class TrustBody(BaseModel):
    path: str
    sha256: str


class UntrustBody(BaseModel):
    path: str


def agent_json(entry: CatalogEntry) -> dict[str, object]:
    d = entry.definition
    return {
        "name": d.name,
        "description": d.description,
        "tools": sorted(d.tools) if d.tools is not None else None,
        "disallowed_tools": sorted(d.disallowed_tools),
        "permission": trust_clamped_permission(d),
        "declared_permission": d.permission,
        "model": d.model,
        "max_turns": d.max_turns,
        "skills": list(d.skills),
        "source": entry.source_kind,
        "path": entry.path,
        "sha256": d.content_sha256 or None,
        "trust": d.trust,
        "active": entry.active,
        "warnings": list(d.warnings),
        "shadowed_by": entry.shadowed_by,
        "persona": d.persona,
        "content": entry.content,
    }


def build_agents_router(
    *,
    workspace: str,
    trust_store: TrustStore | None = None,
    user_agents_dir: Path | None = None,
    mcp_server_names: Callable[[], Iterable[str]] = lambda: (),
    enabled: Callable[[], bool] = is_subagents_enabled,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["agents"])
    ws = Path(workspace)
    trust = trust_store or TrustStore()
    loader = AgentCatalogLoader(ws, user_agents_dir=user_agents_dir, trust_store=trust)
    agents_root = ws / ".crucible" / "agents"

    def _require_enabled() -> None:
        if not enabled():
            raise HTTPException(status_code=409, detail="sub-agents are disabled")

    def _crucible_paths(report: CatalogReport, name: str) -> list[Path]:
        return [Path(e.path) for e in report.entries
                if e.definition.name == name and e.source_kind == "crucible" and e.path]

    def _refuse_nested(report: CatalogReport, name: str) -> None:
        for path in _crucible_paths(report, name):
            if path.parent != agents_root:
                raise HTTPException(
                    status_code=409,
                    detail=f"{name!r} is defined by {path}; edit or delete that file instead")

    @router.get("/agents")
    async def list_agents() -> dict[str, object]:
        if not enabled():
            return {"agents": [], "skipped": [], "available_tools": []}
        report = loader.report()
        tools = sorted(NATIVE_TOOL_NAMES - CHILD_EXCLUDED_TOOLS)
        tools += [f"mcp__{name}__*" for name in sorted(set(mcp_server_names()))]
        return {
            "agents": [agent_json(e) for e in report.entries],
            "skipped": [{"path": s.path, "reason": s.reason} for s in report.skipped],
            "available_tools": tools,
        }

    # The /agents/trust routes are declared before /agents/{name} so "trust" never
    # reaches the name routes (and "trust" is also a reserved agent name).
    @router.post("/agents/trust")
    async def trust_agent(body: TrustBody) -> dict[str, bool]:
        _require_enabled()
        report = loader.report()
        entry = next((e for e in report.entries if e.path == body.path
                      and e.source_kind in ("crucible", "claude")), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="not a workspace agent file")
        path = Path(body.path)
        if path.is_symlink():
            raise HTTPException(status_code=409, detail="refusing to trust a symlink")
        raw = path.read_bytes()
        if len(raw) > CONTENT_MAX_BYTES:
            raise HTTPException(status_code=409, detail="file too large to review and trust")
        if hashlib.sha256(raw).hexdigest() != body.sha256:
            raise HTTPException(status_code=409,
                                detail="the file changed since you reviewed it")
        trust.trust(str(ws), body.path, body.sha256)
        return {"ok": True}

    @router.delete("/agents/trust")
    async def untrust_agent(body: UntrustBody) -> dict[str, bool]:
        _require_enabled()
        trust.revoke(str(ws), body.path)
        return {"ok": True}

    @router.put("/agents/{name}")
    async def save_agent(name: str, body: AgentBody) -> dict[str, object]:
        _require_enabled()
        fields = AgentFields(
            name=name, description=body.description, persona=body.persona,
            tools=tuple(body.tools) if body.tools is not None else None,
            disallowed_tools=tuple(body.disallowed_tools), permission=body.permission,
            model=body.model, max_turns=body.max_turns, skills=tuple(body.skills))
        try:
            validate_fields(fields)
        except AgentInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        report = loader.report()
        _refuse_nested(report, name)
        renaming = body.rename_from is not None and body.rename_from != name
        if renaming:
            assert body.rename_from is not None
            if not NAME_RE.match(body.rename_from):
                raise HTTPException(status_code=400, detail="rename_from is not a valid name")
            if _crucible_paths(report, name):
                raise HTTPException(status_code=409,
                                    detail=f"an agent named {name!r} already exists")
            _refuse_nested(report, body.rename_from)
        try:
            target = write_agent_file(ws, name, render_agent_markdown(fields))
        except AgentConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        trust.trust(str(ws), str(target), hashlib.sha256(target.read_bytes()).hexdigest())
        if renaming:
            assert body.rename_from is not None
            try:
                old = delete_agent_file(ws, body.rename_from)
                trust.revoke(str(ws), str(old))
            except AgentNotFoundError:
                pass  # renaming a definition that was never saved here
            except AgentConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        saved = next(e for e in loader.report().entries if e.path == str(target))
        return {"agent": agent_json(saved)}

    @router.delete("/agents/{name}")
    async def delete_agent(name: str) -> dict[str, bool]:
        _require_enabled()
        report = loader.report()
        _refuse_nested(report, name)
        if not _crucible_paths(report, name):
            if any(e.definition.name == name for e in report.entries):
                raise HTTPException(status_code=409, detail=(
                    f"{name!r} is not a .crucible/agents file; built-ins and .claude files "
                    "are read-only here — duplicate it to .crucible to edit"))
            raise HTTPException(status_code=404, detail=f"no agent named {name!r}")
        try:
            path = delete_agent_file(ws, name)
        except AgentConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        trust.revoke(str(ws), str(path))
        return {"ok": True}

    return router
```

`test_reserved_name_trust_is_refused` accepts 405 as well as 400: `PUT /v1/agents/trust` may match the trust route's path first (no PUT there → 405) — either way nothing is written.

- [ ] **Step 4: Mount it (`agentd/main.py`) and update the GET allowlist**

In `main.py`, with the other `agentd` imports: `from agentd.api.agents_routes import build_agents_router`. Immediately after the existing `app.include_router(build_router(...))` call and before `install_auth(app, AUTH_STATE)`:

```python
# Settings › Agents (spec §10): writes .crucible/agents in THIS backend's workspace only.
app.include_router(build_agents_router(
    workspace=_chat_workspace_path,
    mcp_server_names=lambda: [s.name for s in _mcp_manager.statuses()] if _mcp_manager else []))
```

(`McpConnectionManager.statuses()` returns `agentd/mcp/models.py::McpServerStatus`, whose `name` is the server name.)

In `tests/test_get_routes_read_only.py`, add to `REVIEWED_READ_ONLY_GET_ROUTES`:

```python
    ("api/agents_routes.py", "/agents"),  # reads the catalog; trust/writes are POST/PUT/DELETE
```

- [ ] **Step 5: Run**

Run: `.venv/bin/pytest tests/test_agents_routes.py tests/test_get_routes_read_only.py tests/test_main_auth_wiring.py --color=no --timeout=120 > /tmp/p3t3.txt 2>&1; echo exit=$?; tail -5 /tmp/p3t3.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/api/agents_routes.py agentd/main.py tests/test_agents_routes.py tests/test_get_routes_read_only.py
git commit -m "feat(api): /v1/agents list, save, delete and trust routes"
```

---

# Part B — editor-client (`apps/editor-client`)

### Task 4: Agent definition schemas and four client methods

**Files:**
- Modify: `src/contracts/task-contracts.ts` (schemas + `BackendTaskClient` methods)
- Modify: `src/client/http-backend-client.ts` (methods + mapping + a detail-preserving request helper)
- Test: `test/agent-definitions-client.test.ts`

**Interfaces:**
- Consumes: the Task 3 routes.
- Produces (exported from the package):
  - `AgentDefinitionViewSchema` / `type AgentDefinitionView` — `name, description, tools: string[] | null, disallowedTools, permission, declaredPermission, model, maxTurns: number | null, skills, source: "crucible" | "claude" | "user_claude" | "builtin", path: string | null, sha256: string | null, trust: "trusted" | "capped", active, warnings, shadowedBy: string | null, persona, content: string | null`
  - `AgentCatalogSchema` / `type AgentCatalog` — `{ agents: AgentDefinitionView[]; skipped: { path: string; reason: string }[]; availableTools: string[] }`
  - `interface AgentDefinitionInput { description: string; persona: string; tools: string[] | null; disallowedTools: string[]; permission: string; model: string; maxTurns: number | null; skills: string[]; renameFrom?: string }`
  - `BackendTaskClient`: `listAgentDefinitions(): Promise<AgentCatalog>`, `saveAgentDefinition(name: string, input: AgentDefinitionInput): Promise<AgentDefinitionView>`, `deleteAgentDefinition(name: string): Promise<void>`, `trustAgentDefinition(path: string, sha256: string): Promise<void>`
  - These four reject with an `Error` whose `message` is the backend's `detail` (e.g. `the file changed since you reviewed it`) and whose `status` is the HTTP status — the UI shows it verbatim.

- [ ] **Step 1: Write the failing tests** (`test/agent-definitions-client.test.ts`)

```ts
import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";

const wireAgent = {
  name: "helper", description: "d", tools: null, disallowed_tools: ["run_command"],
  permission: "default", declared_permission: "acceptEdits", model: "inherit", max_turns: null,
  skills: [], source: "claude", path: "/ws/.claude/agents/helper.md", sha256: "ab",
  trust: "capped", active: true, warnings: ["w"], shadowed_by: null, persona: "p", content: "c",
};

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("agent definition client", () => {
  it("lists and maps snake_case, keeping tools null distinct from []", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok({
      agents: [wireAgent, { ...wireAgent, name: "none", tools: [] }],
      skipped: [{ path: "/x.md", reason: "missing 'description' — skipped" }],
      available_tools: ["read_file", "edit"],
    }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const catalog = await c.listAgentDefinitions();
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/agents");
    expect(catalog.agents[0]).toMatchObject({ tools: null, disallowedTools: ["run_command"],
      declaredPermission: "acceptEdits", shadowedBy: null, trust: "capped" });
    expect(catalog.agents[1].tools).toEqual([]);
    expect(catalog.availableTools).toEqual(["read_file", "edit"]);
    expect(catalog.skipped[0].reason).toContain("description");
  });

  it("saves with snake_case fields and returns the mapped row", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok({ agent: { ...wireAgent, source: "crucible" } }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const row = await c.saveAgentDefinition("new name", {
      description: "d", persona: "p", tools: null, disallowedTools: [], permission: "plan",
      model: "inherit", maxTurns: 30, skills: [], renameFrom: "old" });
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/agents/new%20name");
    const init = fetchFn.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      description: "d", persona: "p", tools: null, disallowed_tools: [], permission: "plan",
      model: "inherit", max_turns: 30, skills: [], rename_from: "old" });
    expect(row.source).toBe("crucible");
  });

  it("surfaces the backend's detail on failure", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: "Conflict",
      json: async () => ({ detail: "the file changed since you reviewed it" }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const err = await c.trustAgentDefinition("/p.md", "ab").catch((e: unknown) => e) as Error & { status?: number };
    expect(err.message).toBe("the file changed since you reviewed it");
    expect(err.status).toBe(409);
    expect(JSON.parse(String((fetchFn.mock.calls[0][1] as RequestInit).body)))
      .toEqual({ path: "/p.md", sha256: "ab" });
  });

  it("deletes", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok({ ok: true }));
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    await c.deleteAgentDefinition("helper");
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/agents/helper");
    expect((fetchFn.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @crucible/editor-client test -- test/agent-definitions-client.test.ts`
Expected: FAIL (`listAgentDefinitions is not a function`).

- [ ] **Step 3: Schemas (`task-contracts.ts`, after `SkillSummarySchema`)**

```ts
// ── Settings › Agents (sub-agents v2 §10). tools: null = all tools, distinct from [].
export const AgentDefinitionViewSchema = z.object({
  name: z.string(),
  description: z.string(),
  tools: z.array(z.string()).nullable(),
  disallowedTools: z.array(z.string()),
  permission: z.string(),
  declaredPermission: z.string(),
  model: z.string(),
  maxTurns: z.number().nullable(),
  skills: z.array(z.string()),
  source: z.enum(["crucible", "claude", "user_claude", "builtin"]),
  path: z.string().nullable(),
  sha256: z.string().nullable(),
  trust: z.enum(["trusted", "capped"]),
  active: z.boolean(),
  warnings: z.array(z.string()),
  shadowedBy: z.string().nullable(),
  persona: z.string(),
  content: z.string().nullable(),
});
export type AgentDefinitionView = z.infer<typeof AgentDefinitionViewSchema>;

export const AgentCatalogSchema = z.object({
  agents: z.array(AgentDefinitionViewSchema),
  skipped: z.array(z.object({ path: z.string(), reason: z.string() })),
  availableTools: z.array(z.string()),
});
export type AgentCatalog = z.infer<typeof AgentCatalogSchema>;

export interface AgentDefinitionInput {
  description: string;
  persona: string;
  tools: string[] | null;
  disallowedTools: string[];
  permission: string;
  model: string;
  maxTurns: number | null;
  skills: string[];
  renameFrom?: string;
}
```

And in `interface BackendTaskClient`, next to `listSkills`:

```ts
  listAgentDefinitions(): Promise<AgentCatalog>;
  saveAgentDefinition(name: string, input: AgentDefinitionInput): Promise<AgentDefinitionView>;
  deleteAgentDefinition(name: string): Promise<void>;
  trustAgentDefinition(path: string, sha256: string): Promise<void>;
```

- [ ] **Step 4: Client (`http-backend-client.ts`)**

Import `AgentCatalogSchema`, `AgentDefinitionViewSchema` and the types. Add:

```ts
  /** Like fetchJson, but a failure carries the backend's `detail` as its message: the
   * Settings › Agents UI shows it verbatim ("the file changed since you reviewed it"). */
  private async fetchJsonDetail(path: string, init: RequestInit = {}): Promise<unknown> {
    const response = await this.fetchFn(`${this.options.baseUrl}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...((init.headers as Record<string, string>) ?? {}) },
    });
    if (!response.ok) {
      let detail = `Backend request failed (${response.status} ${response.statusText}) for ${path}`;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch { /* not JSON — keep the generic message */ }
      const error = new Error(detail) as Error & { status?: number };
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  private static toAgentView(raw: Record<string, unknown>): AgentDefinitionView {
    return AgentDefinitionViewSchema.parse({
      name: raw["name"], description: raw["description"],
      tools: raw["tools"] ?? null,
      disallowedTools: raw["disallowed_tools"] ?? [],
      permission: raw["permission"], declaredPermission: raw["declared_permission"],
      model: raw["model"], maxTurns: raw["max_turns"] ?? null, skills: raw["skills"] ?? [],
      source: raw["source"], path: raw["path"] ?? null, sha256: raw["sha256"] ?? null,
      trust: raw["trust"], active: raw["active"], warnings: raw["warnings"] ?? [],
      shadowedBy: raw["shadowed_by"] ?? null, persona: raw["persona"] ?? "",
      content: raw["content"] ?? null,
    });
  }

  async listAgentDefinitions(): Promise<AgentCatalog> {
    const raw = await this.fetchJsonDetail("/v1/agents") as Record<string, unknown>;
    const agents = Array.isArray(raw["agents"]) ? raw["agents"] as Record<string, unknown>[] : [];
    return AgentCatalogSchema.parse({
      agents: agents.map((a) => HttpBackendClient.toAgentView(a)),
      skipped: raw["skipped"] ?? [],
      availableTools: raw["available_tools"] ?? [],
    });
  }

  async saveAgentDefinition(name: string, input: AgentDefinitionInput): Promise<AgentDefinitionView> {
    const raw = await this.fetchJsonDetail(`/v1/agents/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({
        description: input.description, persona: input.persona, tools: input.tools,
        disallowed_tools: input.disallowedTools, permission: input.permission,
        model: input.model, max_turns: input.maxTurns, skills: input.skills,
        ...(input.renameFrom !== undefined ? { rename_from: input.renameFrom } : {}),
      }),
    }) as { agent: Record<string, unknown> };
    return HttpBackendClient.toAgentView(raw.agent);
  }

  async deleteAgentDefinition(name: string): Promise<void> {
    await this.fetchJsonDetail(`/v1/agents/${encodeURIComponent(name)}`, { method: "DELETE" });
  }

  async trustAgentDefinition(path: string, sha256: string): Promise<void> {
    await this.fetchJsonDetail("/v1/agents/trust", {
      method: "POST", body: JSON.stringify({ path, sha256 }) });
  }
```

- [ ] **Step 5: Run, build, update stubs, commit**

Run: `npm run -w @crucible/editor-client test && npm run -w @crucible/editor-client build`
Expected: PASS and a clean build. `npm run -w crucible-vscode-extension typecheck` stays clean: no object in the extension's `src/` implements `BackendTaskClient` by hand, and the test stubs (`test/controller.test.ts`) are partial by design (`tsc` does not check `test/`), so nothing else changes.

```bash
git add apps/editor-client
git commit -m "feat(editor-client): agent definition list, save, delete and trust"
```

---

# Part C — VS Code extension (`apps/vscode-extension`)

### Task 5: Host handler and deps

**Files:**
- Modify: `src/settings-data.ts` (message types, deps, five cases)
- Modify: `src/settings-deps.ts` (`listAgentDefinitions`, `saveAgentDefinition`, `deleteAgentDefinition`, `trustAgentDefinition` on `client`; `openFile`; `listModels`)
- Test: `test/settings-data.test.ts` (extend `deps()`, add cases)

**Interfaces:**
- Consumes: Task 4's client methods and types.
- Produces:
  - `SettingsInMsg` adds `{ type: "settings/listAgents" }`, `{ type: "settings/saveAgent"; name: string; input: AgentDefinitionInput }`, `{ type: "settings/deleteAgent"; name: string }`, `{ type: "settings/trustAgent"; path: string; sha256: string }`, `{ type: "settings/openFile"; path: string }`, `{ type: "settings/listModels" }`
  - `SettingsOutMsg` adds `{ type: "settings/agents"; catalog: AgentCatalog }`, `{ type: "settings/agentsError"; message: string }`, `{ type: "settings/models"; models: string[] }`
  - `SettingsDeps.client` adds the four agent methods; `SettingsDeps` adds `openFile(path: string): Promise<void>` and `listModels(): Promise<string[]>`
  - Rules: agent messages never post `settings/error` (the panel-wide banner) — failures go to `settings/agentsError`; every successful mutation re-posts `settings/agents`; a failed **trust** also re-posts the list (the file changed — show the new content); the agents catalog is never part of `buildState`.

- [ ] **Step 1: Write the failing tests** (append to `test/settings-data.test.ts`; extend `deps()`'s `client` with the four methods and add `openFile`/`listModels`)

In `deps()` add:

```ts
      listAgentDefinitions: vi.fn(async () => CATALOG),
      saveAgentDefinition: vi.fn(async () => CATALOG.agents[0]!),
      deleteAgentDefinition: vi.fn(async () => {}),
      trustAgentDefinition: vi.fn(async () => {}),
```

inside `client`, and at the top level:

```ts
    openFile: vi.fn(async () => {}),
    listModels: async () => ["gpt-5", "m2"],
```

with, above `deps()`:

```ts
const CATALOG = {
  agents: [{
    name: "helper", description: "d", tools: null, disallowedTools: [], permission: "default",
    declaredPermission: "default", model: "inherit", maxTurns: null, skills: [],
    source: "crucible" as const, path: "/ws/.crucible/agents/helper.md", sha256: "ab",
    trust: "trusted" as const, active: true, warnings: [], shadowedBy: null, persona: "p",
    content: "c",
  }],
  skipped: [],
  availableTools: ["read_file", "edit"],
};
```

Tests:

```ts
describe("agents messages", () => {
  const run = async (d: ReturnType<typeof deps>, msg: Parameters<ReturnType<typeof createSettingsHandler>>[0]) => {
    const posted: SettingsOutMsg[] = [];
    await createSettingsHandler(d, (m) => posted.push(m))(msg);
    return posted;
  };

  it("listAgents posts the catalog, not a settings snapshot", async () => {
    const posted = await run(deps(), { type: "settings/listAgents" });
    expect(posted).toEqual([{ type: "settings/agents", catalog: CATALOG }]);
  });

  it("a list failure stays inside the section", async () => {
    const d = deps();
    d.client.listAgentDefinitions = async () => { throw new Error("backend down"); };
    const posted = await run(d, { type: "settings/listAgents" });
    expect(posted).toEqual([{ type: "settings/agentsError", message: "backend down" }]);
  });

  it("save and delete re-post the list", async () => {
    const d = deps();
    const input = { description: "d", persona: "p", tools: null, disallowedTools: [],
      permission: "default", model: "inherit", maxTurns: null, skills: [] };
    expect((await run(d, { type: "settings/saveAgent", name: "helper", input })).at(-1))
      .toEqual({ type: "settings/agents", catalog: CATALOG });
    expect(d.client.saveAgentDefinition).toHaveBeenCalledWith("helper", input);
    expect((await run(d, { type: "settings/deleteAgent", name: "helper" })).at(-1)?.type)
      .toBe("settings/agents");
  });

  it("trustAgent conflict refreshes the list after the error", async () => {
    const d = deps();
    d.client.trustAgentDefinition = async () => { throw new Error("the file changed since you reviewed it"); };
    const posted = await run(d, { type: "settings/trustAgent", path: "/p.md", sha256: "ab" });
    expect(posted.map((m) => m.type)).toEqual(["settings/agentsError", "settings/agents"]);
    expect(posted[0]).toEqual({ type: "settings/agentsError",
      message: "the file changed since you reviewed it" });
  });

  it("openFile and listModels", async () => {
    const d = deps();
    expect(await run(d, { type: "settings/openFile", path: "/a.md" })).toEqual([]);
    expect(d.openFile).toHaveBeenCalledWith("/a.md");
    expect(await run(d, { type: "settings/listModels" }))
      .toEqual([{ type: "settings/models", models: ["gpt-5", "m2"] }]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/settings-data.test.ts`
Expected: FAIL (unknown message types).

- [ ] **Step 3: Implement in `settings-data.ts`**

Imports: `import type { AgentCatalog, AgentDefinitionInput, AgentDefinitionView, McpServerList, McpServerView } from "@crucible/editor-client";`. Add the message variants and deps listed under **Interfaces**, then these cases before `settings/restartBackend`:

```ts
        case "settings/listAgents": {
          await postAgents();
          return;
        }
        case "settings/saveAgent": {
          await agentAction(() => deps.client.saveAgentDefinition(msg.name, msg.input));
          return;
        }
        case "settings/deleteAgent": {
          await agentAction(() => deps.client.deleteAgentDefinition(msg.name));
          return;
        }
        case "settings/trustAgent": {
          // On failure (typically: the file changed under the dialog) the list is
          // re-posted too, so the dialog can only ever show the current bytes.
          await agentAction(() => deps.client.trustAgentDefinition(msg.path, msg.sha256),
                            { refreshOnError: true });
          return;
        }
        case "settings/openFile": {
          await deps.openFile(msg.path);
          return;
        }
        case "settings/listModels": {
          post({ type: "settings/models", models: await deps.listModels() });
          return;
        }
```

and, next to `postState` inside `createSettingsHandler`:

```ts
  // The agents catalog is loaded separately from buildState's Promise.all: one failing
  // route must not blank the whole panel (spec §10.2). Its errors stay in its section.
  const postAgents = async (): Promise<void> => {
    try {
      post({ type: "settings/agents", catalog: await deps.client.listAgentDefinitions() });
    } catch (err) {
      post({ type: "settings/agentsError", message: err instanceof Error ? err.message : String(err) });
    }
  };

  const agentAction = async (
    action: () => Promise<unknown>, opts: { refreshOnError?: boolean } = {},
  ): Promise<void> => {
    try {
      await action();
    } catch (err) {
      post({ type: "settings/agentsError", message: err instanceof Error ? err.message : String(err) });
      if (opts.refreshOnError) await postAgents();
      return;
    }
    await postAgents();
  };
```

(`AgentDefinitionView` is used by the deps signature `saveAgentDefinition(name, input): Promise<AgentDefinitionView>`.)

- [ ] **Step 4: Wire the real deps (`settings-deps.ts`)**

```ts
      listAgentDefinitions: () => client().listAgentDefinitions(),
      saveAgentDefinition: (name, input) => client().saveAgentDefinition(name, input),
      deleteAgentDefinition: (name) => client().deleteAgentDefinition(name),
      trustAgentDefinition: (path, sha256) => client().trustAgentDefinition(path, sha256),
```

in `client`, and at the top level:

```ts
    // Absolute paths: ~/.claude/agents files live outside the workspace (spec §10.2).
    openFile: async (path) => {
      await vscode.window.showTextDocument(vscode.Uri.file(path));
    },
    // The same option list as the composer's model menu (spec §10.2): providers with a
    // stored key, plus the active one.
    listModels: async () => {
      const config = await client().getConfig();
      const keyed: string[] = [];
      for (const p of PROVIDERS) {
        if (p.keyEnvVar && (await runtimeManager.getProviderKey(p.id)) !== undefined) keyed.push(p.id);
      }
      const options = buildModelOptions(config.provider ?? null, keyed, PROVIDERS);
      return [...new Set(options.map((o) => o.model).filter((m) => m))];
    },
```

Imports: `import { buildModelOptions } from "./composer-models.js";` and `import { PROVIDERS } from "./setup-data.js";`. If `vscode-shim.d.ts` lacks `showTextDocument(uri: Uri)`, add the overload (it already declares `Uri.file`).

- [ ] **Step 5: Run tests and typecheck, commit**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS.

```bash
git add apps/vscode-extension/src apps/vscode-extension/test
git commit -m "feat(extension): settings host messages for agent definitions"
```

### Task 6: The Agents section — list, open, trust, delete

**Files:**
- Modify: `webview-ui/src/settings/types.ts` (mirror types + message variants)
- Modify: `webview-ui/src/settings/sections/meta.ts` (`SectionId` + `SECTIONS` entry)
- Modify: `src/settings-sections.ts` + `test/settings-sections.test.ts` (the drift guard)
- Modify: `webview-ui/src/settings/SettingsApp.tsx` (render the section)
- Create: `webview-ui/src/settings/sections/AgentsSection.tsx`, `webview-ui/src/settings/sections/TrustDialog.tsx`
- Test: `webview-ui/src/settings/sections/AgentsSection.test.tsx`

**Interfaces:**
- Consumes: Task 5's messages (`settings/listAgents`, `settings/listModels`, `settings/trustAgent`, `settings/deleteAgent`, `settings/openFile` in; `settings/agents`, `settings/agentsError`, `settings/models` out).
- Produces:
  - webview mirror types `AgentView`, `AgentCatalog`, `AgentInput` (same shapes as editor-client's `AgentDefinitionView`, `AgentCatalog`, `AgentDefinitionInput`; `tools: string[] | null`)
  - `SectionId` gains `"agents"` (nav order: after `skills`)
  - `AgentsSection(props: SectionProps)`; it holds its own catalog/models/error state, listening for its three message types itself
  - `TrustDialog({ agent, onTrust, onCancel }: { agent: AgentView; onTrust(): void; onCancel(): void })`
  - `AgentsSection` renders Task 7's `AgentForm` for New/Edit/Duplicate (Task 7 creates it; this task renders a placeholder `null` until then — see Step 5)

- [ ] **Step 1: Write the failing tests** (`webview-ui/src/settings/sections/AgentsSection.test.tsx`)

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentsSection } from "./AgentsSection";
import type { AgentView, SettingsState } from "../types";

const STATE: SettingsState = {
  provider: null, runtime: null, mcp: { enabled: true, servers: [] },
  skills: [], envFlags: {}, restartRequired: false,
};

function agent(over: Partial<AgentView>): AgentView {
  return {
    name: "a", description: "desc", tools: null, disallowedTools: [], permission: "default",
    declaredPermission: "default", model: "inherit", maxTurns: null, skills: [],
    source: "crucible", path: "/ws/.crucible/agents/a.md", sha256: "aa", trust: "trusted",
    active: true, warnings: [], shadowedBy: null, persona: "p", content: "---\nname: a\n---\np\n",
    ...over,
  };
}

const CATALOG = {
  agents: [
    agent({ name: "mine" }),
    agent({ name: "repo", source: "claude", path: "/ws/.claude/agents/repo.md", trust: "capped",
            permission: "default", declaredPermission: "acceptEdits", sha256: "bb",
            content: "RAW FILE TEXT", warnings: ["untrusted: acceptEdits runs as default"] }),
    agent({ name: "old", source: "claude", path: "/ws/.claude/agents/old.md", active: false,
            shadowedBy: "/ws/.crucible/agents/old.md" }),
    agent({ name: "general-purpose", source: "builtin", path: null, sha256: null, content: null }),
  ],
  skipped: [{ path: "/ws/.crucible/agents/broken.md", reason: "missing 'description' — skipped" }],
  availableTools: ["read_file", "edit"],
};

function deliver(data: unknown) {
  act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
}

function setup() {
  const send = vi.fn();
  render(<AgentsSection state={STATE} busy={false} send={send} />);
  deliver({ type: "settings/agents", catalog: CATALOG });
  return send;
}

describe("AgentsSection", () => {
  it("requests its data on mount", () => {
    const send = vi.fn();
    render(<AgentsSection state={STATE} busy={false} send={send} />);
    expect(send).toHaveBeenCalledWith({ type: "settings/listAgents" });
    expect(send).toHaveBeenCalledWith({ type: "settings/listModels" });
  });

  it("groups rows by source and shows clamps, shadowing and skipped files", () => {
    setup();
    expect(screen.getByText(".crucible/agents")).toBeTruthy();
    expect(screen.getByText("Built-in")).toBeTruthy();
    expect(screen.getByText("capped — declared acceptEdits")).toBeTruthy();
    expect(screen.getByText("overridden by /ws/.crucible/agents/old.md")).toBeTruthy();
    expect(screen.getAllByText("all tools").length).toBeGreaterThan(0);
    expect(screen.getByText(/broken\.md/)).toBeTruthy();
    expect(screen.getByText("missing 'description' — skipped")).toBeTruthy();
  });

  it("expands warnings", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "1 warning on repo" }));
    expect(screen.getByText("untrusted: acceptEdits runs as default")).toBeTruthy();
  });

  it("trust shows the exact file text and sends its sha256", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "Trust repo" }));
    expect(screen.getByText("RAW FILE TEXT")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Trust this file" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/trustAgent",
      path: "/ws/.claude/agents/repo.md", sha256: "bb" });
  });

  it("delete asks twice and only on .crucible rows", () => {
    const send = setup();
    expect(screen.queryByRole("button", { name: "Delete repo" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Delete mine" }));
    expect(send).not.toHaveBeenCalledWith({ type: "settings/deleteAgent", name: "mine" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete mine" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/deleteAgent", name: "mine" });
  });

  it("opens a file row and says built-ins have none", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "Open mine" }));
    expect(send).toHaveBeenCalledWith({ type: "settings/openFile", path: "/ws/.crucible/agents/a.md" });
    expect(screen.getByText("built-in — no file")).toBeTruthy();
  });

  it("shows its own error without needing the settings snapshot", () => {
    setup();
    deliver({ type: "settings/agentsError", message: "the file changed since you reviewed it" });
    expect(screen.getByText("the file changed since you reviewed it")).toBeTruthy();
  });
});
```

Add a `test/settings-sections.test.ts` change: the expected list becomes `["overview", "provider", "mcp", "skills", "agents", "instructions", "policies", "runtime"]` and the test name says "eight".

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/settings-sections.test.ts` and `cd apps/vscode-extension/webview-ui && npx vitest run src/settings/sections/AgentsSection.test.tsx`
Expected: both FAIL. (`webview-ui` is its own npm package — not a root workspace — so a fresh checkout or worktree needs `npm install` inside `apps/vscode-extension/webview-ui` first; its scripts are `test` = `vitest run`, `typecheck` = `tsc --noEmit`.)

- [ ] **Step 3: Types, registry, meta, shell**

`webview-ui/src/settings/types.ts` — add (and add the six `settings/*` in-variants and three out-variants from Task 5 to `SettingsInMsg` / `SettingsOutMsg`, typed with these):

```ts
// Mirror of editor-client's AgentDefinitionView / AgentCatalog / AgentDefinitionInput.
// tools: null = all tools, distinct from [] (no tools).
export interface AgentView {
  name: string;
  description: string;
  tools: string[] | null;
  disallowedTools: string[];
  permission: string;
  declaredPermission: string;
  model: string;
  maxTurns: number | null;
  skills: string[];
  source: "crucible" | "claude" | "user_claude" | "builtin";
  path: string | null;
  sha256: string | null;
  trust: "trusted" | "capped";
  active: boolean;
  warnings: string[];
  shadowedBy: string | null;
  persona: string;
  content: string | null;
}

export interface AgentCatalog {
  agents: AgentView[];
  skipped: { path: string; reason: string }[];
  availableTools: string[];
}

export interface AgentInput {
  description: string;
  persona: string;
  tools: string[] | null;
  disallowedTools: string[];
  permission: string;
  model: string;
  maxTurns: number | null;
  skills: string[];
  renameFrom?: string;
}
```

`meta.ts`: `SectionId` adds `"agents"`; in `SECTIONS`, after the `skills` entry:

```ts
  { id: "agents", label: "Agents", icon: "fork", blurb: "Sub-agents the main agent can dispatch: tools, permissions and trust.", tint: "var(--color-accent-ink)" },
```

`src/settings-sections.ts`: `SettingsSectionId` adds `| "agents"`; `SETTINGS_SECTIONS` gets `{ id: "agents", label: "Agents" }` after `skills`.

`SettingsApp.tsx`: `import { AgentsSection } from "./sections/AgentsSection";` and `{section === "agents" && <AgentsSection {...props} />}` after the skills line.

- [ ] **Step 4: `TrustDialog.tsx`**

```tsx
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import type { AgentView } from "../types";

/** Shows the exact bytes the trust record will cover (spec §10.2), then trusts that hash. */
export function TrustDialog({ agent, onTrust, onCancel }: {
  agent: AgentView; onTrust: () => void; onCancel: () => void;
}) {
  return (
    <div className="surface-card anim-slide-down mb-3 flex flex-col gap-2 p-3" role="dialog"
         aria-label={`Trust ${agent.name}`}>
      <div className="text-xs font-medium text-text">Trust {agent.name}?</div>
      <div className="text-[11px] text-text-3">
        Trusted, this definition runs with its declared permission
        ({agent.declaredPermission}) and its edits follow your review setting. Any later change
        to the file revokes the trust. Read the whole file first:
      </div>
      <div className="font-mono text-[10px] text-text-3">{agent.path}</div>
      {agent.content === null ? (
        <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>
          This file is over 64 KB and cannot be reviewed or trusted here.
        </div>
      ) : (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-border-strong
                        bg-surface-2 p-2 font-mono text-[11px] text-text">{agent.content}</pre>
      )}
      <div className="flex justify-end gap-2">
        <BtnGhost onClick={onCancel}>Cancel</BtnGhost>
        <BtnPrimary disabled={agent.content === null || agent.sha256 === null} onClick={onTrust}>
          Trust this file
        </BtnPrimary>
      </div>
    </div>
  );
}
```

(`BtnPrimary`/`BtnGhost` render a `<button>` whose accessible name is their children — the test's `{ name: "Trust this file" }` relies on that.)

- [ ] **Step 5: `AgentsSection.tsx`**

```tsx
import { useEffect, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { SectionHeader } from "../SectionHeader";
import type { AgentCatalog, AgentView, SettingsOutMsg } from "../types";
import type { SectionProps } from "./meta";
import { AgentForm, type AgentFormMode } from "./AgentForm";
import { TrustDialog } from "./TrustDialog";

const GROUPS: { source: AgentView["source"]; title: string }[] = [
  { source: "crucible", title: ".crucible/agents" },
  { source: "claude", title: ".claude/agents" },
  { source: "user_claude", title: "~/.claude/agents" },
  { source: "builtin", title: "Built-in" },
];

function permissionLabel(a: AgentView): string {
  return a.permission !== a.declaredPermission
    ? `capped — declared ${a.declaredPermission}`
    : a.permission;
}

function AgentRow({ agent, onOpen, onTrust, onEdit, onDuplicate, onDelete }: {
  agent: AgentView;
  onOpen: () => void;
  onTrust: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const [showWarnings, setShowWarnings] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const capped = agent.trust === "capped";
  return (
    <li className="flex flex-col gap-1 border-b py-2 last:border-b-0"
        style={{ borderColor: "var(--hairline)", opacity: agent.active ? 1 : 0.55 }}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {agent.path ? (
          <button type="button" aria-label={`Open ${agent.name}`} onClick={onOpen}
                  className="whitespace-nowrap text-xs font-medium text-text hover:underline">{agent.name}</button>
        ) : (
          <span className="whitespace-nowrap text-xs font-medium text-text">{agent.name}</span>
        )}
        <span className="whitespace-nowrap rounded px-1.5 text-[10px]"
              style={{ background: capped ? "var(--amber-bg)" : "var(--surface-3, transparent)",
                       color: capped ? "var(--color-amber)" : "var(--color-text-3)" }}>
          {permissionLabel(agent)}
        </span>
        {capped && <span className="text-[10px]" style={{ color: "var(--color-amber)" }}>untrusted</span>}
        <span className="min-w-0 max-w-[140px] truncate text-[10px] text-text-4" title={agent.model}>{agent.model}</span>
        <span className="whitespace-nowrap text-[10px] text-text-4">
          {agent.tools === null ? "all tools" : `${agent.tools.length} tools`}
        </span>
        {agent.warnings.length > 0 && (
          <button type="button" className="text-[10px]" style={{ color: "var(--color-amber)" }}
                  aria-label={`${agent.warnings.length} warning${agent.warnings.length === 1 ? "" : "s"} on ${agent.name}`}
                  onClick={() => setShowWarnings((v) => !v)}>
            ⚠ {agent.warnings.length}
          </button>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
        {capped && agent.path && agent.source !== "user_claude" && (
          <BtnGhost onClick={onTrust} className="!text-[10px]">
            <span aria-label={`Trust ${agent.name}`}>Trust</span>
          </BtnGhost>
        )}
        {agent.source === "crucible" ? (
          <>
            <BtnGhost onClick={onEdit}><span aria-label={`Edit ${agent.name}`}>Edit</span></BtnGhost>
            {confirming ? (
              <BtnDanger onClick={onDelete}>
                <span aria-label={`Confirm delete ${agent.name}`}>Confirm delete</span>
              </BtnDanger>
            ) : (
              <BtnGhost onClick={() => setConfirming(true)}>
                <span aria-label={`Delete ${agent.name}`}>Delete</span>
              </BtnGhost>
            )}
          </>
        ) : (
          <BtnGhost onClick={onDuplicate}>
            <span aria-label={`Duplicate ${agent.name}`} title="Duplicate to .crucible/agents"
                  className="whitespace-nowrap">Duplicate</span>
          </BtnGhost>
        )}
        </div>
      </div>
      <div className="truncate text-[11px] text-text-3">{agent.description}</div>
      {!agent.path && <div className="text-[10px] text-text-4">built-in — no file</div>}
      {!agent.active && agent.shadowedBy && (
        <div className="text-[10px] text-text-4">overridden by {agent.shadowedBy}</div>
      )}
      {showWarnings && (
        <ul className="ml-3 list-disc text-[10px]" style={{ color: "var(--color-amber)" }}>
          {agent.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
    </li>
  );
}

/** Settings › Agents (spec §10.2). Loads its own data so a failing catalog route never
 * blanks the rest of the panel; its errors stay inside this section. */
export function AgentsSection({ state, busy, send }: SectionProps) {
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [trusting, setTrusting] = useState<AgentView | null>(null);
  const [form, setForm] = useState<{ mode: AgentFormMode; agent: AgentView | null } | null>(null);

  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "settings/agents") {
        setCatalog(msg.catalog);
        setError(null);
        setTrusting(null);
        setForm(null);
      } else if (msg.type === "settings/agentsError") {
        setError(msg.message);
      } else if (msg.type === "settings/models") {
        setModels(msg.models);
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "settings/listAgents" });
    send({ type: "settings/listModels" });
    return () => window.removeEventListener("message", onMessage);
    // send is stable for the panel's lifetime; load once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const crucibleNames = new Set(
    (catalog?.agents ?? []).filter((a) => a.source === "crucible").map((a) => a.name));

  return (
    <div>
      <SectionHeader
        title="Agents"
        description="Definitions the main agent can dispatch. Files in .crucible/agents win over .claude/agents, then ~/.claude/agents, then the built-ins. Workspace files run capped until you trust them."
        action={
          <div className="flex gap-2">
            <BtnGhost onClick={() => send({ type: "settings/listAgents" })}>Refresh</BtnGhost>
            <BtnPrimary disabled={!catalog} onClick={() => setForm({ mode: "new", agent: null })}>
              New agent
            </BtnPrimary>
          </div>
        }
      />
      {error && (
        <div className="anim-slide-down mb-3 rounded-[10px] border px-3 py-2 text-xs"
             style={{ borderColor: "var(--red-brd)", background: "var(--red-bg)", color: "var(--color-red)" }}>
          {error}
        </div>
      )}
      {trusting && (
        <TrustDialog
          agent={trusting}
          onCancel={() => setTrusting(null)}
          onTrust={() => send({ type: "settings/trustAgent", path: trusting.path ?? "",
                                sha256: trusting.sha256 ?? "" })}
        />
      )}
      {form && catalog && (
        <AgentForm
          mode={form.mode}
          initial={form.agent}
          availableTools={catalog.availableTools}
          models={models}
          skillsEnabled={state.skills.length > 0}
          existingNames={crucibleNames}
          busy={busy}
          onCancel={() => setForm(null)}
          onSave={(name, input) => send({ type: "settings/saveAgent", name, input })}
        />
      )}
      {!catalog && !error && <div className="p-4 text-xs text-text-3">Loading agents…</div>}
      {catalog && GROUPS.map(({ source, title }) => {
        const rows = catalog.agents.filter((a) => a.source === source);
        if (rows.length === 0) return null;
        return (
          <div key={source} className="mb-3">
            <CardShell icon="fork" title={title}
                       trailing={<span className="text-[10px] text-text-3">{rows.length}</span>}>
              <ul className="flex flex-col px-3 pb-2 pt-1">
                {rows.map((a) => (
                  <AgentRow
                    key={`${a.source}:${a.path ?? a.name}`}
                    agent={a}
                    onOpen={() => a.path && send({ type: "settings/openFile", path: a.path })}
                    onTrust={() => setTrusting(a)}
                    onEdit={() => setForm({ mode: "edit", agent: a })}
                    onDuplicate={() => setForm({ mode: "duplicate", agent: a })}
                    onDelete={() => send({ type: "settings/deleteAgent", name: a.name })}
                  />
                ))}
              </ul>
            </CardShell>
          </div>
        );
      })}
      {catalog && catalog.skipped.length > 0 && (
        <CardShell icon="warn" title="Skipped files"
                   trailing={<span className="text-[10px] text-text-3">{catalog.skipped.length}</span>}>
          <ul className="flex flex-col px-3 pb-2 pt-1">
            {catalog.skipped.map((s) => (
              <li key={s.path} className="border-b py-1.5 last:border-b-0" style={{ borderColor: "var(--hairline)" }}>
                <div className="font-mono text-[10px] text-text-3">{s.path}</div>
                <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>{s.reason}</div>
              </li>
            ))}
          </ul>
        </CardShell>
      )}
    </div>
  );
}
```

The aria-labels sit on a `<span>` inside the buttons, because `BtnGhost`/`BtnDanger` take only children; a button's accessible name is then the span's label. If the test's `getByRole("button", { name })` does not resolve through the span in this Testing Library version, give `BtnGhost`, `BtnDanger` and `BtnPrimary` an optional `ariaLabel` prop that sets `aria-label` on the `<button>`, and use it here instead of the spans.

Until Task 7 lands, create `webview-ui/src/settings/sections/AgentForm.tsx` as a stub so this compiles:

```tsx
import type { AgentInput, AgentView } from "../types";
export type AgentFormMode = "new" | "edit" | "duplicate";
export function AgentForm(_props: {
  mode: AgentFormMode; initial: AgentView | null; availableTools: string[]; models: string[];
  skillsEnabled: boolean; existingNames: Set<string>; busy: boolean;
  onCancel: () => void; onSave: (name: string, input: AgentInput) => void;
}) {
  return null;
}
```

- [ ] **Step 6: Run the webview and extension tests**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run && cd .. && npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS. (`SettingsApp.test.tsx` / `NavRail.test.tsx` may count sections; update their expected lists to include Agents.)

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src apps/vscode-extension/src/settings-sections.ts apps/vscode-extension/test/settings-sections.test.ts
git commit -m "feat(settings): Agents section — list, trust dialog, open and delete"
```

### Task 7: The agent form — New, Edit, Duplicate

**Files:**
- Modify: `webview-ui/src/settings/sections/AgentForm.tsx` (replace the stub)
- Test: `webview-ui/src/settings/sections/AgentForm.test.tsx`

**Interfaces:**
- Consumes: `AgentView`, `AgentInput` (Task 6).
- Produces: `type AgentFormMode = "new" | "edit" | "duplicate"`; `AgentForm({ mode, initial, availableTools, models, skillsEnabled, existingNames, busy, onCancel, onSave })` — `onSave(name: string, input: AgentInput)`; for `edit`, a changed name sends `renameFrom: initial.name`.
- Rules: name must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` and not be `trust` (Save disabled otherwise); description required; "All tools" checked ⇒ `tools: null`, unchecked ⇒ the checked list (possibly `[]`); `maxTurns` empty ⇒ `null`, else 1…200; New/Duplicate onto a name already in `.crucible/agents` shows "replaces the existing .crucible/agents/<name>.md"; Duplicate keeps the source's name (that is how a `.claude` or built-in definition is overridden).

- [ ] **Step 1: Write the failing tests** (`AgentForm.test.tsx`)

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentForm } from "./AgentForm";
import type { AgentView } from "../types";

const TOOLS = ["read_file", "search_code", "edit", "mcp__github__*"];

function base(over: Partial<AgentView> = {}): AgentView {
  return {
    name: "repo", description: "Repo helper", tools: ["read_file"], disallowedTools: [],
    permission: "plan", declaredPermission: "plan", model: "inherit", maxTurns: 40,
    skills: [], source: "claude", path: "/ws/.claude/agents/repo.md", sha256: "bb",
    trust: "capped", active: true, warnings: [], shadowedBy: null, persona: "Be careful.",
    content: "x", ...over,
  };
}

function renderForm(props: Partial<Parameters<typeof AgentForm>[0]> = {}) {
  const onSave = vi.fn();
  render(<AgentForm mode="new" initial={null} availableTools={TOOLS} models={["gpt-5"]}
                    skillsEnabled={false} existingNames={new Set(["taken"])} busy={false}
                    onCancel={vi.fn()} onSave={onSave} {...props} />);
  return onSave;
}

describe("AgentForm", () => {
  it("new agent with all tools sends tools: null", () => {
    const onSave = renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "helper" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Helps" } });
    fireEvent.change(screen.getByLabelText("Role / persona"), { target: { value: "You help." } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave).toHaveBeenCalledWith("helper", {
      description: "Helps", persona: "You help.", tools: null, disallowedTools: [],
      permission: "default", model: "inherit", maxTurns: null, skills: [] });
  });

  it("an explicit tool list, and an empty one, are kept", () => {
    const onSave = renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "d" } });
    fireEvent.click(screen.getByLabelText("All tools"));
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave.mock.calls[0][1].tools).toEqual([]);
    fireEvent.click(screen.getByLabelText("Allow read_file"));
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave.mock.calls[1][1].tools).toEqual(["read_file"]);
  });

  it("invalid or reserved names disable save", () => {
    renderForm();
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "d" } });
    for (const bad of ["bad name", "trust", ""]) {
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: bad } });
      expect((screen.getByRole("button", { name: "Save agent" }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("warns when a new name replaces an existing .crucible file", () => {
    renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "taken" } });
    expect(screen.getByText(/replaces the existing \.crucible\/agents\/taken\.md/)).toBeTruthy();
  });

  it("duplicate pre-fills from the source and keeps its name", () => {
    const onSave = renderForm({ mode: "duplicate", initial: base() });
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("repo");
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave).toHaveBeenCalledWith("repo", {
      description: "Repo helper", persona: "Be careful.", tools: ["read_file"], disallowedTools: [],
      permission: "plan", model: "inherit", maxTurns: 40, skills: [] });
  });

  it("edit with a new name sends renameFrom", () => {
    const onSave = renderForm({ mode: "edit", initial: base({ source: "crucible" }) });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "repo2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(onSave.mock.calls[0][0]).toBe("repo2");
    expect(onSave.mock.calls[0][1].renameFrom).toBe("repo");
  });

  it("offers inherit, the provider models, and the definition's own model", () => {
    renderForm({ mode: "duplicate", initial: base({ model: "custom-model" }) });
    const options = Array.from((screen.getByLabelText("Model") as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(["inherit", "gpt-5", "custom-model"]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/settings/sections/AgentForm.test.tsx`
Expected: FAIL (the stub renders nothing).

- [ ] **Step 3: Implement `AgentForm.tsx`**

```tsx
import { useState } from "react";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { FIELD } from "../ui";
import type { AgentInput, AgentView } from "../types";

export type AgentFormMode = "new" | "edit" | "duplicate";

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const PERMISSIONS = ["default", "acceptEdits", "plan", "dontAsk"];

function ToolChecks({ label, tools, checked, onToggle }: {
  label: string; tools: string[]; checked: Set<string>; onToggle: (t: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {tools.map((t) => (
        <label key={t} className="flex items-center gap-1 text-[11px] text-text-2">
          <input type="checkbox" aria-label={`${label} ${t}`} checked={checked.has(t)}
                 onChange={() => onToggle(t)} />
          <span className="font-mono">{t}</span>
        </label>
      ))}
    </div>
  );
}

/** New / Edit / Duplicate form for a .crucible/agents definition (spec §10.2). */
export function AgentForm({ mode, initial, availableTools, models, skillsEnabled, existingNames, busy,
                            onCancel, onSave }: {
  mode: AgentFormMode; initial: AgentView | null; availableTools: string[]; models: string[];
  skillsEnabled: boolean; existingNames: Set<string>; busy: boolean;
  onCancel: () => void; onSave: (name: string, input: AgentInput) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [persona, setPersona] = useState(initial?.persona ?? "");
  const [allTools, setAllTools] = useState(initial ? initial.tools === null : true);
  const [tools, setTools] = useState(new Set(initial?.tools ?? []));
  const [disallowed, setDisallowed] = useState(new Set(initial?.disallowedTools ?? []));
  const [permission, setPermission] = useState(initial?.declaredPermission ?? "default");
  const [model, setModel] = useState(initial?.model ?? "inherit");
  const [maxTurns, setMaxTurns] = useState(initial?.maxTurns != null ? String(initial.maxTurns) : "");
  const [skills, setSkills] = useState((initial?.skills ?? []).join(", "));

  const toolOptions = [...new Set([...availableTools, ...(initial?.tools ?? []),
                                    ...(initial?.disallowedTools ?? [])])];
  const modelOptions = [...new Set(["inherit", ...models, ...(initial ? [initial.model] : [])])];
  const turns = maxTurns.trim() === "" ? null : Number(maxTurns);
  const turnsOk = turns === null || (Number.isInteger(turns) && turns >= 1 && turns <= 200);
  const nameOk = NAME_RE.test(name) && name !== "trust";
  const valid = nameOk && description.trim() !== "" && turnsOk;
  const replaces = mode !== "edit" && existingNames.has(name);
  const toggle = (set: Set<string>, update: (s: Set<string>) => void, t: string) => {
    const next = new Set(set);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    update(next);
  };

  const save = () => {
    const input: AgentInput = {
      description: description.trim(),
      persona,
      tools: allTools ? null : toolOptions.filter((t) => tools.has(t)),
      disallowedTools: toolOptions.filter((t) => disallowed.has(t)),
      permission,
      model,
      maxTurns: turns,
      skills: skills.split(",").map((s) => s.trim()).filter((s) => s),
    };
    if (mode === "edit" && initial && initial.name !== name) input.renameFrom = initial.name;
    onSave(name, input);
  };

  const title = mode === "new" ? "New agent" : mode === "edit" ? `Edit ${initial?.name ?? ""}`
    : `Duplicate ${initial?.name ?? ""} to .crucible/agents`;

  return (
    <div className="surface-card anim-slide-down mb-3 flex flex-col gap-2.5 p-3" role="form" aria-label={title}>
      <div className="text-xs font-medium text-text">{title}</div>
      <label className="flex flex-col gap-1 text-[11px] text-text-3">
        Name
        <input aria-label="Name" className={FIELD} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {!nameOk && name !== "" && (
        <div className="text-[10px]" style={{ color: "var(--color-red)" }}>
          1–64 characters of letters, digits, _ . - starting with a letter or digit; “trust” is reserved.
        </div>
      )}
      {replaces && (
        <div className="text-[10px]" style={{ color: "var(--color-amber)" }}>
          Saving replaces the existing .crucible/agents/{name}.md
        </div>
      )}
      <label className="flex flex-col gap-1 text-[11px] text-text-3">
        Description
        <input aria-label="Description" className={FIELD} value={description}
               onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-text-3">
        Role / persona
        <textarea aria-label="Role / persona" className={`${FIELD} min-h-[96px] font-mono`}
                  value={persona} onChange={(e) => setPersona(e.target.value)} />
      </label>
      <div className="flex flex-col gap-1 text-[11px] text-text-3">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" aria-label="All tools" checked={allTools}
                 onChange={() => setAllTools((v) => !v)} />
          All tools
        </label>
        {!allTools && (
          <ToolChecks label="Allow" tools={toolOptions} checked={tools}
                      onToggle={(t) => toggle(tools, setTools, t)} />
        )}
      </div>
      <div className="flex flex-col gap-1 text-[11px] text-text-3">
        Disallowed tools
        <ToolChecks label="Disallow" tools={toolOptions} checked={disallowed}
                    onToggle={(t) => toggle(disallowed, setDisallowed, t)} />
      </div>
      <div className="flex gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Permission
          <select aria-label="Permission" className={FIELD} value={permission}
                  onChange={(e) => setPermission(e.target.value)}>
            {PERMISSIONS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Model
          <select aria-label="Model" className={FIELD} value={model} onChange={(e) => setModel(e.target.value)}>
            {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Max turns
          <input aria-label="Max turns" className={`${FIELD} w-20`} inputMode="numeric"
                 value={maxTurns} onChange={(e) => setMaxTurns(e.target.value)} />
        </label>
      </div>
      {(skillsEnabled || (initial?.skills.length ?? 0) > 0) && (
        <label className="flex flex-col gap-1 text-[11px] text-text-3">
          Skills (comma-separated)
          <input aria-label="Skills" className={FIELD} value={skills} onChange={(e) => setSkills(e.target.value)} />
        </label>
      )}
      <div className="flex justify-end gap-2">
        <BtnGhost onClick={onCancel}>Cancel</BtnGhost>
        <BtnPrimary disabled={!valid || busy} onClick={save}>Save agent</BtnPrimary>
      </div>
    </div>
  );
}
```

Note `useState(new Set(...))` for `tools`/`disallowed`: typed `Set<string>`, which matches `ToolChecks`.

- [ ] **Step 4: Add one integration test to `AgentsSection.test.tsx`**

```tsx
  it("New agent opens the form and saves through settings/saveAgent", () => {
    const send = setup();
    fireEvent.click(screen.getByRole("button", { name: "New agent" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "fresh" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "d" } });
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ type: "settings/saveAgent", name: "fresh" }));
  });
```

- [ ] **Step 5: Run and commit**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run && npx tsc --noEmit -p .` (use the webview's own typecheck script if `package.json` names one) and `npm run build` from the repo root.
Expected: PASS, clean build.

```bash
git add apps/vscode-extension/webview-ui/src
git commit -m "feat(settings): agent form for new, edit and duplicate"
```

---

# Part D — Verification

### Task 8: Docs, full suites, live smoke

- [ ] **Step 1: CLAUDE.md** — under "Sub-agents (P5)", add a **Settings › Agents (v2 Phase 3)** bullet: `GET/PUT/DELETE /v1/agents`, `POST/DELETE /v1/agents/trust` (`agentd/api/agents_routes.py`; workspace = the backend's own; trust keyed by path + sha256 in `~/.crucible/trust.json`); the loader's `report()` keeps warnings and skipped/shadowed definitions; writes are symlink-safe and confined to `.crucible/agents`, in Claude Code tool names; the webview section loads its catalog separately from the settings snapshot (`settings/listAgents`, errors stay in-section). Commit with `docs(claude): Settings › Agents`.

- [ ] **Step 2: Full suites**

```bash
cd services/agentd-py && .venv/bin/pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -3 /tmp/py.txt
cd ../.. && npm run build && npm run test && npm run typecheck
```

Known pre-existing flakes (not this phase): `test_command_only_step_runs_command_and_verifies`, `test_reap_kills_live_recorded_process` — re-run alone before attributing.

- [ ] **Step 3: Live smoke on the dev host** (second VS Code instance, CDP 9335; `npm run build` then "Developer: Reload Window")

1. Settings → Agents lists the built-ins and every file in `workspaces/subagent-smoke/.crucible|.claude/agents`, with warnings where expected.
2. New agent `smoke-reviewer` (plan permission, read tools only) → `.crucible/agents/smoke-reviewer.md` appears with Claude Code tool names, row shows trusted.
3. Ask the chat to dispatch `smoke-reviewer` — it runs read-only.
4. Put a `.claude/agents/repo-helper.md` with `permissionMode: acceptEdits` in the workspace → row shows "capped — declared acceptEdits"; Trust shows the file text; Trust → row trusted. Edit the file in an editor → row capped again on Refresh.
5. With the Trust dialog open, change the file on disk, click Trust → the section shows "the file changed since you reviewed it" and the dialog/list shows the new content.
6. Duplicate a built-in → form pre-filled; save → `.crucible/agents/<name>.md` and the built-in row reads "overridden by …".
7. Delete the smoke files through the UI; confirm they and their trust records are gone (`~/.crucible/trust.json`).

Record any failure as a finding, fix it with a regression test, re-run the step.
