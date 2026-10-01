# Sub-agents Phase 3 — Agent Definitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users define their own sub-agents as Claude Code–compatible Markdown files. The files are discovered from `.crucible/agents/`, `.claude/agents/` and `~/.claude/agents/`, sit in front of the two built-ins, and are offered by `dispatch_agents`. Phase 3 also honors the definition fields Phase 2 left inert (`disallowedTools`, `skills`, `edit` absent from `tools`, `mcp__<server>` wildcards). Finally, the `subagent-driven-development` skill becomes executable when the flag is on.

**Architecture:**
- A new `agentd/subagents/agent_files.py` holds:
  - the pure parse and Claude Code → Crucible mapping (`parse_agent_file`);
  - an mtime-cached `AgentCatalogLoader`, using the same discipline as `SkillCatalogLoader`, but signed over every file because discovery is recursive.
- `ChatController` builds one loader when sub-agents are enabled. `_dispatch_source` asks it for the catalog on every call, so a new file takes effect on the next turn (or the next nested dispatch) with no restart.
- Tool and type filtering stay in `subagents/permissions.py`, gaining a pattern matcher and a `can_edit` input.

**Tech stack:** Python 3.13, PyYAML (already a hard dependency via skills), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-29-subagents-design.md` rev 11 — §5.2 (skills pre-seed), §5.3, §5.4 (model aliases), §5.6 (permissions), §9 (definitions), §16 phase 3.

**Branch:** `feat/subagents`, on top of Plan 2 (`1bf21c6`).

## Ground truth this plan is written against (verified at `1bf21c6`)

- `agentd/subagents/definitions.py:10-19` — `AgentDefinition(name, description, permission, tools: frozenset[str] | None, persona, model="inherit", max_turns=None)`, frozen dataclass. `BUILTIN_AGENTS` (`:27`) defines:
  - `explore`: permission `plan`; tools `search_code, read_file, list_directory, query_graph, search_semantic`.
  - `general-purpose`: permission `default`; tools `None`.
- `agentd/subagents/permissions.py`:
  - `child_allowed_types(permission)` (`:26`) drops `edit` only for `plan`.
  - `child_tool_names(available, *, definition_tools, permission, may_dispatch)` (`:32`) intersects with `definition_tools` exactly, with no pattern support.
- `agentd/subagents/tool_source.py:26` — `SubAgentToolSource(catalog: dict[str, AgentDefinition], dispatch)`. It builds the description lines and the `agent` enum from `catalog` in iteration order. `tests/test_dispatch_tool_source.py:29` pins the built-ins' text and the enum `["explore", "general-purpose"]`.
- `agentd/chat/controller.py`:
  - `:56` imports `BUILTIN_AGENTS`.
  - `:207-209` builds `self._subagents` only when `is_subagents_enabled()`.
  - `:1448-1452` `_dispatch_source` passes `BUILTIN_AGENTS`.
  - `:1466-1473` `_dispatch` builds `AgentContext(..., allowed_types=child_allowed_types(permission), persona=request.agent.persona, max_iters=request.agent.max_turns or subagent_max_iters())`.
  - `:1536` `_run_child` creates `active_skills: dict[str, str] = {}`.
  - `:1567-1569` `_run_child` calls `child_tool_names(available, definition_tools=handle.definition.tools, permission=ctx.permission, may_dispatch=may_dispatch)`.
  - `:1590-1591` the engine is `self._reasoning` when `definition.model == "inherit"`, else `with_model(model)`.
- `agentd/skills/loader.py` — `SkillCatalogLoader` keys its cache on the skills ROOT dir's `st_mtime_ns`. A directory's mtime changes only when its direct entries change, so that discipline can't see an edit to a nested file. Agent discovery is recursive (§9.1), so this loader signs `(path, mtime_ns)` of every `*.md` under the three roots.
- `agentd/skills/tool_source.py:67-69` caps a skill body at `skills_body_max_chars()` with the marker `\n\n[... skill '{name}' truncated at {cap} chars ...]`.
- `agentd/chat/controller_loop.py:503` defines `_NON_EXECUTABLE_SUBSKILLS = frozenset({"subagent-driven-development"})`, consumed only by `_pick_executable_required_subskill` (`:526-530`). `tests/test_controller_required_subskill_autoload.py:60-68` pins the flag-off behavior.
- `agentd/prompting/tagged.py:28` — `Permission = Literal["default", "acceptEdits", "dontAsk", "plan"]`.

## Decisions this plan makes (and why)

1. **`edit` is a pseudo tool name in `tools`/`disallowedTools`.** Claude Code's `Edit|Write|MultiEdit|NotebookEdit` map to `edit`, the edit *action type* (§9.3). It never matches a registry tool. `definition_allows_edit(tools, disallowed)` turns it into the `can_edit` input of `child_allowed_types`, which implements §5.3's "if `edit` isn't in the filtered set, `edit` is removed from `allowed_types`".
2. **MCP wildcards are normalized at parse time.** Both `mcp__<server>` and `mcp__<server>__*` become `mcp__<server>__*`. `tool_matches` treats a trailing `__*` as a prefix and everything else as exact.
3. **Catalog order is by name**, built-ins included, so the tool description and enum are deterministic (cache-stable) whatever the scan order. The built-ins are already alphabetical, so the existing pinned enum is unchanged.
4. **Agent names must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`.** Otherwise the file is skipped with a warning. The name lands in a JSON-schema enum, the UI chip and log lines; Claude Code's own names already fit.
5. **`description` is whitespace-collapsed and capped at 1024 characters.** It is one catalog line, and Claude Code descriptions often embed multi-line `<example>` blocks.
6. **`skills:` pre-seed is a no-op (with one warning) when `CRUCIBLE_SKILLS_ENABLED` is off.** There is no catalog to read from and no `read_skill` tool for the child to use them with.
7. **The parent's tool-description format is unchanged.** Custom agents appear as more `- name: description` lines.

## File structure

| File | Change |
|---|---|
| `agentd/subagents/definitions.py` | `AgentDefinition` gains `disallowed_tools`, `skills`, `source` (defaults keep every existing constructor call valid) |
| `agentd/subagents/agent_files.py` | **new** — mapping tables, `parse_agent_file`, `AgentCatalogLoader` |
| `agentd/subagents/permissions.py` | `tool_matches`, `definition_allows_edit`; `child_allowed_types(..., can_edit=)`, `child_tool_names(..., definition_disallowed=)` |
| `agentd/skills/tool_source.py` | extract `cap_skill_body(name, body)` (shared with the pre-seed) |
| `agentd/chat/controller.py` | own an `AgentCatalogLoader`; `_dispatch_source` uses it; `_dispatch` passes `can_edit`; `_run_child` passes `definition_disallowed` and pre-seeds skills |
| `agentd/chat/controller_loop.py` | `_NON_EXECUTABLE_SUBSKILLS` → `_non_executable_subskills()` (flag-conditional) |
| `tests/test_agent_files.py` | **new** — parse/mapping |
| `tests/test_agent_catalog_loader.py` | **new** — discovery, precedence, cache |
| `tests/test_subagent_permissions.py` | extend — patterns, disallowed, `can_edit` |
| `tests/test_agent_definitions_wiring.py` | **new** — controller wiring (catalog in the tool, skills pre-seed, disallowed reaches the registry) |
| `tests/test_controller_required_subskill_autoload.py` | extend — flag-on case |
| `CLAUDE.md` | update the Sub-agents (P5) section |

Commands below run from `services/agentd-py` with the venv active. Per CLAUDE.md, never pass `-q` (pyproject already sets it) and never pipe pytest. Use `pytest <paths>` and read the summary line.

---

### Task F1: `AgentDefinition` fields and the shared skill-body cap

**Files:**
- Modify: `agentd/subagents/definitions.py`
- Modify: `agentd/skills/tool_source.py`
- Test: `tests/test_agent_files.py` (created here, extended in F2)

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_files.py`:

```python
"""Agent definition files: fields, parse and Claude Code mapping (spec §5.4, §5.6, §9)."""
from agentd.skills.tool_source import cap_skill_body
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition


def test_new_fields_default_so_existing_definitions_are_unchanged() -> None:
    d = AgentDefinition(name="x", description="d", permission="default", tools=None, persona="")
    assert (d.disallowed_tools, d.skills, d.source) == (frozenset(), (), "built-in")
    assert all(b.source == "built-in" for b in BUILTIN_AGENTS.values())


def test_cap_skill_body() -> None:
    assert cap_skill_body("s", "short", cap=10) == "short"
    assert cap_skill_body("s", "x" * 12, cap=10) == (
        "x" * 10 + "\n\n[... skill 's' truncated at 10 chars ...]")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_agent_files.py`
Expected: collection error — `ImportError: cannot import name 'cap_skill_body'`.

- [ ] **Step 3: Implement**

In `agentd/subagents/definitions.py`, replace the dataclass body:

```python
@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str          # one line for the dispatcher's catalog
    permission: Permission
    tools: frozenset[str] | None  # None = every tool the dispatcher's child may have
    persona: str
    model: str = "inherit"
    max_turns: int | None = None
    # Applied before `tools` (spec §9.3). "edit" in either set is the edit ACTION type.
    disallowed_tools: frozenset[str] = frozenset()
    skills: tuple[str, ...] = ()  # pre-seeded into the child's active skills (spec §5.2)
    source: str = "built-in"      # the file it came from, for warnings and logs
```

In `agentd/skills/tool_source.py`, add the module-level helper above `class SkillToolSource`:

```python
def cap_skill_body(name: str, body: str, *, cap: int | None = None) -> str:
    """A skill body as the model sees it: capped at CRUCIBLE_SKILLS_BODY_MAX_CHARS with a
    visible marker. Shared by read_skill and a sub-agent's `skills:` pre-seed."""
    limit = skills_body_max_chars() if cap is None else cap
    if len(body) <= limit:
        return body
    return body[:limit] + f"\n\n[... skill '{name}' truncated at {limit} chars ...]"
```

Then replace the three inline lines in `execute`:

```python
        cap = skills_body_max_chars()
        if len(body) > cap:
            body = body[:cap] + f"\n\n[... skill '{name}' truncated at {cap} chars ...]"
```

with:

```python
        body = cap_skill_body(name, body)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_agent_files.py tests/test_skills_additive.py tests/test_skills_tool_source.py tests/test_skills_forced.py tests/test_subagent_context.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/definitions.py agentd/skills/tool_source.py tests/test_agent_files.py
git commit -m "feat(subagents): definition fields for disallowed tools, skills and source"
```

---

### Task F2: `parse_agent_file` — frontmatter and the Claude Code mapping

**Files:**
- Create: `agentd/subagents/agent_files.py`
- Test: `tests/test_agent_files.py`

- [ ] **Step 1: Write the failing tests.** Replace the import block at the top of `tests/test_agent_files.py` with this one. Imports stay at the top, or ruff's E402 fails the lint step:

```python
import logging
from pathlib import Path

import pytest

from agentd.skills.tool_source import cap_skill_body
from agentd.subagents.agent_files import parse_agent_file
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition
```

Then append to the end of the file:

```python
def _write(tmp_path: Path, text: str, name: str = "a.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_file_inherits_everything(tmp_path: Path) -> None:
    d = parse_agent_file(_write(
        tmp_path, "---\nname: reviewer\ndescription: Reviews diffs\n---\nBe strict.\n"))
    assert d is not None
    assert (d.name, d.description, d.persona) == ("reviewer", "Reviews diffs", "Be strict.")
    assert (d.permission, d.tools, d.disallowed_tools, d.model, d.max_turns, d.skills) == (
        "default", None, frozenset(), "inherit", None, ())
    assert d.source.endswith("a.md")


def test_claude_code_tools_map_to_crucible_names(tmp_path: Path) -> None:
    d = parse_agent_file(_write(tmp_path, (
        "---\nname: cc\ndescription: d\n"
        "tools: Read, Grep, Glob, LS, Bash, Edit, Write, MultiEdit, NotebookEdit, Agent, Task, "
        "TodoWrite, Skill, query_graph, mcp__github, mcp__jira__*, mcp__gh__create_issue\n---\n")))
    assert d is not None and d.tools == frozenset({
        "read_file", "search_code", "list_directory", "run_command", "edit", "dispatch_agents",
        "write_todos", "read_skill", "query_graph", "mcp__github__*", "mcp__jira__*",
        "mcp__gh__create_issue"})


def test_yaml_list_fields_and_disallowed(tmp_path: Path) -> None:
    d = parse_agent_file(_write(tmp_path, (
        "---\nname: l\ndescription: d\ntools: [Read, Bash]\ndisallowedTools: Write\n"
        "skills:\n  - tdd\n  - debugging\nmaxTurns: 12\n---\n")))
    assert d is not None
    assert d.tools == frozenset({"read_file", "run_command"})
    assert d.disallowed_tools == frozenset({"edit"})
    assert (d.skills, d.max_turns) == (("tdd", "debugging"), 12)


def test_web_tools_have_no_mapping(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        d = parse_agent_file(_write(
            tmp_path, "---\nname: w\ndescription: d\ntools: Read, WebFetch, WebSearch\n---\n"))
    assert d is not None and d.tools == frozenset({"read_file"})
    assert "WebFetch" in caplog.text and "WebSearch" in caplog.text


@pytest.mark.parametrize("raw,expected", [
    ("default", "default"), ("acceptEdits", "acceptEdits"), ("dontAsk", "dontAsk"),
    ("plan", "plan"), ("bypassPermissions", "default"), ("yolo", "default")])
def test_permission_mode(tmp_path: Path, raw: str, expected: str) -> None:
    d = parse_agent_file(_write(
        tmp_path, f"---\nname: p\ndescription: d\npermissionMode: {raw}\n---\n"))
    assert d is not None and d.permission == expected


@pytest.mark.parametrize("raw,expected", [
    ("inherit", "inherit"), ("sonnet", "inherit"), ("opus", "inherit"), ("haiku", "inherit"),
    ("fable", "inherit"), ("gpt-5.1", "gpt-5.1"), ("nvidia/nemotron-3-ultra-550b-a55b",
                                                   "nvidia/nemotron-3-ultra-550b-a55b")])
def test_model_aliases_inherit(tmp_path: Path, raw: str, expected: str) -> None:
    d = parse_agent_file(_write(tmp_path, f"---\nname: m\ndescription: d\nmodel: {raw}\n---\n"))
    assert d is not None and d.model == expected


def test_description_is_one_capped_line(tmp_path: Path) -> None:
    long = "word " * 400
    d = parse_agent_file(_write(
        tmp_path, f"---\nname: x\ndescription: |\n  first\n  second\n  {long}\n---\n"))
    assert d is not None
    assert d.description.startswith("first second word") and "\n" not in d.description
    assert len(d.description) == 1024


@pytest.mark.parametrize("text", [
    "no frontmatter at all",
    "---\nname: x\n",                                    # unterminated
    "---\n: [broken\n---\n",                             # YAML error
    "---\n- a list\n---\n",                              # not a mapping
    "---\ndescription: d\n---\n",                        # no name
    "---\nname: x\n---\n",                               # no description
    "---\nname: has space\ndescription: d\n---\n",       # unsafe name
    "---\nname: x\ndescription: d\ntools: 7\n---\n",     # wrong field type is skipped, not fatal
])
def test_malformed_files_are_skipped_or_degraded(tmp_path: Path, text: str) -> None:
    d = parse_agent_file(_write(tmp_path, text))
    if "tools: 7" in text:
        assert d is not None and d.tools is None  # the field is ignored with a warning
    else:
        assert d is None


def test_bad_max_turns_is_ignored(tmp_path: Path) -> None:
    for raw in ("0", "-3", "lots"):
        d = parse_agent_file(_write(
            tmp_path, f"---\nname: t\ndescription: d\nmaxTurns: {raw}\n---\n"))
        assert d is not None and d.max_turns is None


def test_unreadable_file_returns_none(tmp_path: Path) -> None:
    assert parse_agent_file(tmp_path / "missing.md") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_agent_files.py`
Expected: collection error — `ModuleNotFoundError: No module named 'agentd.subagents.agent_files'`.

- [ ] **Step 3: Implement** — create `agentd/subagents/agent_files.py`:

```python
"""Agent definition files (spec §9): Claude Code–compatible Markdown with YAML frontmatter.

Parsing is best-effort by design: a malformed file is skipped (or a malformed field
ignored) with a warning, and loading never raises into a turn.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from agentd.prompting.tagged import Permission
from agentd.subagents.definitions import AgentDefinition

logger = logging.getLogger(__name__)

_DESC_MAX = 1024
# The name lands in a JSON-schema enum, the UI chip and log lines.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PERMISSIONS: dict[str, Permission] = {
    "default": "default", "acceptEdits": "acceptEdits", "dontAsk": "dontAsk", "plan": "plan",
    "bypassPermissions": "default",  # spec §5.6: there is no bypass mode here
}
# Claude Code family aliases name Anthropic models; on any other provider they mean
# nothing, and imported Claude Code agents use them routinely (spec §5.4).
_CC_MODEL_ALIASES = frozenset({"sonnet", "opus", "haiku", "fable"})
# Claude Code tool name → Crucible name (spec §9.3). "edit" is the edit ACTION type, not a
# registry tool: permissions.definition_allows_edit reads it.
_CC_TOOLS: dict[str, str] = {
    "Read": "read_file", "Grep": "search_code", "Glob": "list_directory",
    "LS": "list_directory", "Bash": "run_command", "Edit": "edit", "Write": "edit",
    "MultiEdit": "edit", "NotebookEdit": "edit", "Agent": "dispatch_agents",
    "Task": "dispatch_agents", "TodoWrite": "write_todos", "Skill": "read_skill",
}
_UNMAPPED_CC_TOOLS = frozenset({"WebFetch", "WebSearch"})


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


def _name_list(raw: object, field: str, path: Path) -> list[str] | None:
    """A Claude Code list field: a YAML list or a comma-separated string; None if absent
    or unusable (a wrong type is ignored with a warning, not fatal to the file)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, list):
        items = [str(item) for item in raw]
    else:
        logger.warning("[agents] %s: %r must be a list or a comma-separated string — ignored",
                       path, field)
        return None
    return [item.strip() for item in items if item.strip()]


def map_tool_names(names: list[str], path: Path) -> frozenset[str]:
    """Claude Code tool names → Crucible names; Crucible-native names pass through.
    `mcp__<server>` and `mcp__<server>__*` both become the wildcard `mcp__<server>__*`."""
    mapped: set[str] = set()
    for name in names:
        if name in _UNMAPPED_CC_TOOLS:
            logger.warning("[agents] %s: tool %r has no Crucible equivalent — ignored "
                           "(list the MCP tool names instead)", path, name)
            continue
        if name.startswith("mcp__") and name.count("__") == 1:
            name += "__*"
        mapped.add(_CC_TOOLS.get(name, name))
    return frozenset(mapped)


def _permission(raw: object, path: Path) -> Permission:
    if raw is None:
        return "default"
    permission = _PERMISSIONS.get(str(raw).strip())
    if permission is None:
        logger.warning("[agents] %s: unknown permissionMode %r — using default", path, raw)
        return "default"
    return permission


def _model(raw: object, path: Path) -> str:
    model = str(raw).strip() if raw is not None else ""
    if not model or model == "inherit":
        return "inherit"
    if model in _CC_MODEL_ALIASES:
        logger.warning("[agents] %s: model %r is a Claude Code alias — inheriting the "
                       "dispatcher's model", path, model)
        return "inherit"
    return model


def _max_turns(raw: object, path: Path) -> int | None:
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = 0
    if value < 1:
        logger.warning("[agents] %s: maxTurns %r is not a positive integer — ignored", path, raw)
        return None
    return value


def parse_agent_file(path: Path) -> AgentDefinition | None:
    """One agent definition, or None (with a warning) when the file can't define one.
    Honored frontmatter (spec §9.2): name, description, tools, disallowedTools, model,
    permissionMode, maxTurns, skills. Everything else is parsed and ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("[agents] cannot read %s: %s", path, exc)
        return None
    front, body = _split_frontmatter(text)
    if front is None:
        logger.warning("[agents] %s: missing/invalid YAML frontmatter — skipped", path)
        return None
    name, description = front.get("name"), front.get("description")
    if not isinstance(name, str) or not _NAME_RE.match(name.strip()):
        logger.warning("[agents] %s: 'name' is missing or not [A-Za-z0-9_.-] — skipped", path)
        return None
    if not isinstance(description, str) or not description.strip():
        logger.warning("[agents] %s: missing 'description' — skipped", path)
        return None
    tools = _name_list(front.get("tools"), "tools", path)
    disallowed = _name_list(front.get("disallowedTools"), "disallowedTools", path)
    skills = _name_list(front.get("skills"), "skills", path)
    return AgentDefinition(
        name=name.strip(),
        description=" ".join(description.split())[:_DESC_MAX],
        permission=_permission(front.get("permissionMode"), path),
        tools=map_tool_names(tools, path) if tools is not None else None,
        persona=body.strip(),
        model=_model(front.get("model"), path),
        max_turns=_max_turns(front.get("maxTurns"), path),
        disallowed_tools=(
            map_tool_names(disallowed, path) if disallowed is not None else frozenset()),
        skills=tuple(skills or ()),
        source=str(path))
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_agent_files.py`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `ruff check agentd/subagents/agent_files.py tests/test_agent_files.py` → `All checks passed!`

```bash
git add agentd/subagents/agent_files.py tests/test_agent_files.py
git commit -m "feat(subagents): parse Claude Code agent files and map their tool names"
```

---

### Task F3: `AgentCatalogLoader` — discovery, precedence, cache

**Files:**
- Modify: `agentd/subagents/agent_files.py`
- Test: `tests/test_agent_catalog_loader.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_agent_catalog_loader.py`:

```python
"""Agent discovery: precedence, recursion, built-ins, mtime cache (spec §9.1)."""
import logging
import os
from pathlib import Path

import pytest

from agentd.subagents.agent_files import AgentCatalogLoader
from agentd.subagents.definitions import BUILTIN_AGENTS


def _agent(path: Path, name: str, description: str = "d", body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    workspace, user = tmp_path / "ws", tmp_path / "home-agents"
    workspace.mkdir()
    return workspace, user


def test_no_files_means_the_built_ins(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    catalog = AgentCatalogLoader(workspace, user_agents_dir=user).load()
    assert list(catalog) == ["explore", "general-purpose"]
    assert catalog["explore"] is BUILTIN_AGENTS["explore"]


def test_precedence_crucible_then_claude_then_user_then_built_in(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/a.md", "shared", "from crucible")
    _agent(workspace / ".claude/agents/b.md", "shared", "from claude")
    _agent(user / "c.md", "shared", "from user")
    _agent(workspace / ".claude/agents/d.md", "explore", "project explore")
    _agent(user / "e.md", "only-user", "user only")
    catalog = AgentCatalogLoader(workspace, user_agents_dir=user).load()
    assert catalog["shared"].description == "from crucible"
    assert catalog["explore"].description == "project explore"   # a file overrides a built-in
    assert catalog["only-user"].description == "user only"
    assert list(catalog) == sorted(catalog)                      # deterministic, by name


def test_recursive_and_identity_from_name(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/team/backend/api-expert.md", "api", "nested")
    assert AgentCatalogLoader(workspace, user_agents_dir=user).load()["api"].description == "nested"


def test_duplicate_within_one_root_keeps_first_and_warns(
    roots: tuple[Path, Path], caplog: pytest.LogCaptureFixture,
) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/a.md", "dup", "first")
    _agent(workspace / ".crucible/agents/b.md", "dup", "second")
    with caplog.at_level(logging.WARNING):
        catalog = AgentCatalogLoader(workspace, user_agents_dir=user).load()
    assert catalog["dup"].description == "first" and "dup" in caplog.text


def test_malformed_file_is_skipped(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    (workspace / ".crucible/agents").mkdir(parents=True)
    (workspace / ".crucible/agents/bad.md").write_text("no frontmatter", encoding="utf-8")
    _agent(workspace / ".crucible/agents/good.md", "good")
    assert "good" in AgentCatalogLoader(workspace, user_agents_dir=user).load()


def test_cache_hit_and_nested_edit_invalidates(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    nested = workspace / ".crucible/agents/team/x.md"
    _agent(nested, "x", "v1")
    loader = AgentCatalogLoader(workspace, user_agents_dir=user)
    first = loader.load()
    assert loader.load() is first                       # unchanged → same object
    _agent(nested, "x", "v2")                           # a nested edit: the root dir's mtime
    stat = nested.stat()                                # does not move, the file's does
    os.utime(nested, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert loader.load()["x"].description == "v2"


def test_new_and_deleted_files_take_effect(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    loader = AgentCatalogLoader(workspace, user_agents_dir=user)
    assert "late" not in loader.load()
    _agent(workspace / ".claude/agents/late.md", "late")
    assert "late" in loader.load()
    (workspace / ".claude/agents/late.md").unlink()
    assert "late" not in loader.load()


def test_non_md_files_are_ignored(roots: tuple[Path, Path]) -> None:
    workspace, user = roots
    _agent(workspace / ".crucible/agents/notes.txt", "txt")
    assert "txt" not in AgentCatalogLoader(workspace, user_agents_dir=user).load()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_agent_catalog_loader.py`
Expected: collection error — `ImportError: cannot import name 'AgentCatalogLoader'`.

- [ ] **Step 3: Implement** — append to `agentd/subagents/agent_files.py`. Add `import threading` to the imports and change the definitions import to `from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition`. Then append:

```python
class AgentCatalogLoader:
    """The agents `dispatch_agents` offers (spec §9.1): `.crucible/agents/`, then
    `.claude/agents/`, then `~/.claude/agents/`, then the built-ins. A name wins at its
    highest-precedence source. Directories are scanned recursively for `*.md`.

    Cached on the (path, mtime_ns) of every file, not the roots' mtimes: a directory's
    mtime moves only when its direct entries change, so a root-keyed cache (the
    SkillCatalogLoader discipline) would never see an edit to a nested file."""

    def __init__(self, workspace_path: Path | str, *, user_agents_dir: Path | None = None) -> None:
        workspace = Path(workspace_path)
        self._roots: tuple[Path, ...] = (
            workspace / ".crucible" / "agents",
            workspace / ".claude" / "agents",
            user_agents_dir if user_agents_dir is not None else Path.home() / ".claude" / "agents",
        )
        self._lock = threading.Lock()
        self._signature: tuple[tuple[str, int], ...] | None = None
        self._cached: dict[str, AgentDefinition] | None = None

    def load(self) -> dict[str, AgentDefinition]:
        """The catalog, sorted by name. Treat it as read-only: it is the cached object."""
        with self._lock:
            files = self._files()
            signature = tuple((str(path), mtime) for _, path, mtime in files)
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

    def _build(self, files: list[tuple[int, Path, int]]) -> dict[str, AgentDefinition]:
        catalog: dict[str, AgentDefinition] = {}
        origin: dict[str, int] = {}
        for index, path, _ in files:
            definition = parse_agent_file(path)
            if definition is None:
                continue
            if definition.name in catalog:
                if origin[definition.name] == index:
                    logger.warning("[agents] %s: duplicate agent name %r (already defined in "
                                   "%s) — skipped", path, definition.name,
                                   catalog[definition.name].source)
                continue  # a lower-precedence root never overrides a higher one
            catalog[definition.name] = definition
            origin[definition.name] = index
        for name, builtin in BUILTIN_AGENTS.items():
            catalog.setdefault(name, builtin)
        return dict(sorted(catalog.items()))
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_agent_catalog_loader.py tests/test_agent_files.py`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `ruff check agentd/subagents/agent_files.py tests/test_agent_catalog_loader.py` → `All checks passed!`

```bash
git add agentd/subagents/agent_files.py tests/test_agent_catalog_loader.py
git commit -m "feat(subagents): discover agent files with precedence and a per-file cache"
```

---

### Task F4: Permissions — wildcards, `disallowedTools`, and `edit` absent from `tools`

**Files:**
- Modify: `agentd/subagents/permissions.py`
- Test: `tests/test_subagent_permissions.py`

- [ ] **Step 1: Write the failing tests.** Add `definition_allows_edit` and `tool_matches` to the existing parenthesized `from agentd.subagents.permissions import (...)` block at the top of `tests/test_subagent_permissions.py` (alphabetical order: `AGENT_BASE_TYPES, child_allowed_types, child_tool_names, definition_allows_edit, effective_permission, follows_live_review, tool_matches`). Then append:

```python
def test_tool_matches_exact_and_server_wildcard() -> None:
    patterns = frozenset({"read_file", "mcp__gh__*"})
    assert tool_matches("read_file", patterns)
    assert tool_matches("mcp__gh__create_issue", patterns)
    assert not tool_matches("mcp__ghx__create_issue", patterns)  # the prefix ends at "__"
    assert not tool_matches("search_code", patterns)


def test_definition_tools_accept_mcp_wildcards() -> None:
    names = child_tool_names(_AVAILABLE, definition_tools=frozenset({"read_file", "mcp__gh__*"}),
                             permission="default", may_dispatch=False)
    assert names == frozenset({"read_file", "mcp__gh__create_issue"})


def test_disallowed_is_removed_before_tools() -> None:
    names = child_tool_names(
        _AVAILABLE, definition_tools=None, permission="default", may_dispatch=True,
        definition_disallowed=frozenset({"run_command", "mcp__gh__*"}))
    assert "run_command" not in names and "mcp__gh__create_issue" not in names
    assert {"read_file", "dispatch_agents"} <= names


@pytest.mark.parametrize("tools,disallowed,expected", [
    (None, frozenset(), True),
    (frozenset({"read_file", "edit"}), frozenset(), True),
    (frozenset({"read_file"}), frozenset(), False),     # tools listed without Edit/Write
    (None, frozenset({"edit"}), False),                 # disallowedTools: Write
])
def test_definition_allows_edit(tools, disallowed, expected) -> None:
    assert definition_allows_edit(tools, disallowed) is expected


def test_no_edit_type_when_the_definition_cannot_edit() -> None:
    assert child_allowed_types("default", can_edit=False) == ("tool_call", "progress", "report")
    assert child_allowed_types("default") == AGENT_BASE_TYPES   # unchanged default
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_subagent_permissions.py`
Expected: collection error — `ImportError: cannot import name 'definition_allows_edit'`.

- [ ] **Step 3: Implement** — in `agentd/subagents/permissions.py`, replace `child_allowed_types` and `child_tool_names` with:

```python
def tool_matches(name: str, patterns: frozenset[str]) -> bool:
    """`mcp__<server>__*` matches every tool of that server (spec §9.3); anything else
    is an exact name."""
    if name in patterns:
        return True
    return any(p.endswith("__*") and name.startswith(p[:-1]) for p in patterns)


def definition_allows_edit(tools: frozenset[str] | None, disallowed: frozenset[str]) -> bool:
    """Whether a definition keeps the edit action (spec §5.3): `edit` is the mapped name of
    Claude Code's Edit/Write/MultiEdit/NotebookEdit, which are actions here, not tools."""
    return "edit" not in disallowed and (tools is None or "edit" in tools)


def child_allowed_types(permission: Permission, *, can_edit: bool = True) -> tuple[str, ...]:
    if permission == "plan" or not can_edit:
        return tuple(t for t in AGENT_BASE_TYPES if t != "edit")
    return AGENT_BASE_TYPES


def child_tool_names(
    available: Iterable[str], *, definition_tools: frozenset[str] | None,
    permission: Permission, may_dispatch: bool,
    definition_disallowed: frozenset[str] = frozenset(),
) -> frozenset[str]:
    names = {n for n in set(available) - CHILD_EXCLUDED_TOOLS
             if not tool_matches(n, definition_disallowed)}
    if definition_tools is not None:
        names = {n for n in names if tool_matches(n, definition_tools)}
    if permission == "plan":
        # Filtered out of the tool list entirely, so the MCP teaching block is not
        # appended either; _permission_correction is the defense in depth.
        names = {n for n in names if n not in _READ_ONLY_EXCLUDED and not n.startswith("mcp__")}
    if not may_dispatch:
        names.discard(DISPATCH_TOOL)
    return frozenset(names)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_subagent_permissions.py tests/test_dispatch_integration.py tests/test_agent_loop.py`
Expected: all pass. The old exact-intersection callers are unchanged, since exact names still match exactly.

- [ ] **Step 5: Commit**

```bash
git add agentd/subagents/permissions.py tests/test_subagent_permissions.py
git commit -m "feat(subagents): MCP wildcards, disallowed tools and edit-less definitions"
```

---

### Task F5: Wire the catalog, `disallowedTools`, `can_edit` and `skills:` into the controller

**Files:**
- Modify: `agentd/chat/controller.py`
- Test: `tests/test_agent_definitions_wiring.py`

- [ ] **Step 1: Know the harness you reuse.** `tests/test_dispatch_integration.py` exports module-level helpers this task imports (pytest's `pythonpath=["."]` makes `from tests.…` work):
  - `_controller(ws, tmp_path, store, engine)` builds a real `ChatController` over a real `AgentOrchestrator`/`ShadowWorkspaceManager`;
  - `_dispatch(*(agent, label, prompt))` builds the parent's `dispatch_agents` tool call;
  - `DONE` is the parent's `submit_changes`.

  `ScriptedReasoningEngine(None, [], controller_step_responses=[...], agent_scripts={label: [...]})` scripts the parent and each child, by label. After a turn, `ctrl._subagents.registry.get(agent_id)` still returns the `AgentHandle`. Its `.loop._registry.definitions()` is the child's real tool list (used the same way by `test_nested_dispatch_stops_at_the_depth_limit`), and `.loop._active_skills` is its skills dict (`controller_loop.py:598`).

  **HOME must be redirected in every test.** `AgentCatalogLoader`'s third root defaults to `Path.home() / ".claude" / "agents"`, and a developer's real agents would otherwise leak into the catalog.

- [ ] **Step 2: Write the failing tests** — create `tests/test_agent_definitions_wiring.py`:

```python
"""Agent definitions reach dispatch and shape the child (spec §5.2, §5.3, §9)."""
import logging
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from tests.test_dispatch_integration import DONE, _controller, _dispatch

_REPORT = [{"type": "report", "thought": "t", "summary": "Read it."}]


def _agent_file(ws: Path, name: str, front: str) -> None:
    path = ws / ".crucible" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {name} agent\n{front}---\nPersona.\n",
                    encoding="utf-8")


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ChatThreadStore, str]:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # keep ~/.claude/agents out of it
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    return ws, store, store.create_thread(str(ws), title="t").thread_id


def test_discovered_agents_reach_the_tool_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws, store, _ = _setup(tmp_path, monkeypatch)
    ctrl = _controller(ws, tmp_path, store, ScriptedReasoningEngine(None, []))
    [before] = ctrl._dispatch_source("t", "u", None).definitions()
    assert "reviewer" not in before.description
    _agent_file(ws, "reviewer", "permissionMode: plan\n")   # added after construction
    [after] = ctrl._dispatch_source("t", "u", None).definitions()
    assert "- reviewer: reviewer agent" in after.description
    enum = after.parameters["properties"]["agents"]["items"]["properties"]["agent"]["enum"]
    assert enum == ["explore", "general-purpose", "reviewer"]


@pytest.mark.asyncio
async def test_definition_fields_shape_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    ws, store, tid = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("CRUCIBLE_SKILLS_ENABLED", "1")
    skill = ws / ".crucible" / "skills" / "tdd" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: tdd\ndescription: test first\n---\nWrite the test first.\n",
                     encoding="utf-8")
    _agent_file(ws, "reader", "tools: Read, Grep, Bash\ndisallowedTools: Bash\nskills: tdd, nope\n")
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("reader", "r1", "Read a file")), DONE], agent_scripts={"r1": _REPORT})
    ctrl = _controller(ws, tmp_path, store, engine)

    with caplog.at_level(logging.WARNING):
        await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    [row] = store.list_agents(tid)
    assert (row.name, row.status) == ("reader", "completed")
    assert ctrl._subagents is not None
    handle = ctrl._subagents.registry.get(row.agent_id)
    assert handle is not None
    # tools ∩ (available − disallowed): Bash is gone, read_skill/write_todos weren't listed.
    assert {d.name for d in handle.loop._registry.definitions()} == {"read_file", "search_code"}
    # No Edit/Write in `tools` → no edit action (spec §5.3).
    assert handle.context.allowed_types == ("tool_call", "progress", "report")
    assert set(handle.loop._active_skills) == {"tdd"}
    assert "Write the test first." in handle.loop._active_skills["tdd"]
    assert "unknown skill 'nope'" in caplog.text


@pytest.mark.asyncio
async def test_skills_need_the_skills_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    ws, store, tid = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv("CRUCIBLE_SKILLS_ENABLED", raising=False)
    _agent_file(ws, "reader", "skills: tdd\n")
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[
        _dispatch(("reader", "r1", "Read a file")), DONE], agent_scripts={"r1": _REPORT})
    ctrl = _controller(ws, tmp_path, store, engine)

    with caplog.at_level(logging.WARNING):
        await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    [row] = store.list_agents(tid)
    assert ctrl._subagents is not None
    handle = ctrl._subagents.registry.get(row.agent_id)
    assert handle is not None and handle.loop._active_skills == {}
    assert "CRUCIBLE_SKILLS_ENABLED is off" in caplog.text
```

- [ ] **Step 3: Run them to verify they fail**

Run: `pytest tests/test_agent_definitions_wiring.py`
Expected:
- `test_discovered_agents_reach_the_tool_without_a_restart` fails on `"- reviewer: reviewer agent" in after.description`, because the source still uses `BUILTIN_AGENTS`.
- The two async tests fail inside the parent's dispatch with `agents[0]: unknown agent 'reader'`. The scripted parent then submits, so `store.list_agents(tid)` is empty and `[row] = …` raises `ValueError`.

- [ ] **Step 4: Implement** in `agentd/chat/controller.py`:

Imports: insert directly **before** `from agentd.subagents.config import ...` (isort order — `agent_files` sorts before `config`):

```python
from agentd.subagents.agent_files import AgentCatalogLoader
```

and change `from agentd.subagents.definitions import BUILTIN_AGENTS` to:

```python
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition
``` Add `definition_allows_edit` to the existing parenthesized `from agentd.subagents.permissions import (...)` block, in alphabetical position. Next to `from agentd.skills.tool_source import SkillToolSource`, import `cap_skill_body` as well:

```python
from agentd.skills.tool_source import SkillToolSource, cap_skill_body
```

In `__init__`, directly after the `self._subagents: SubAgentRuntime | None = (...)` assignment (`:207-209`):

```python
        # Agent definitions (spec §9): files in front of the built-ins, re-read per dispatch
        # source so a new file takes effect next turn with no restart.
        self._agent_catalog_loader: AgentCatalogLoader | None = (
            AgentCatalogLoader(workspace_path) if is_subagents_enabled() else None)
```

Replace `_dispatch_source`:

```python
    def _agent_catalog(self) -> dict[str, AgentDefinition]:
        if self._agent_catalog_loader is None:
            return BUILTIN_AGENTS
        return self._agent_catalog_loader.load()

    def _dispatch_source(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
    ) -> SubAgentToolSource:
        return SubAgentToolSource(
            self._agent_catalog(), partial(self._dispatch, thread_id, turn_id, dispatcher))
```

In `_dispatch` (`:1471`), replace the single line

```python
                permission=permission, allowed_types=child_allowed_types(permission),
```

with:

```python
                permission=permission, allowed_types=child_allowed_types(
                    permission, can_edit=definition_allows_edit(
                        request.agent.tools, request.agent.disallowed_tools)),
```

In `_run_child`:
- After `active_skills: dict[str, str] = {}` inside `_run_child` (`:1536` — `_run_loop` has an identical line at `:523`; leave that one alone) add:

  ```python
          if handle.definition.skills:
              self._preseed_child_skills(handle, active_skills)
  ```

- Change the `child_tool_names(...)` call (`:1567-1569`) to:

  ```python
          names = child_tool_names(available, definition_tools=handle.definition.tools,
                                   permission=ctx.permission, may_dispatch=may_dispatch,
                                   definition_disallowed=handle.definition.disallowed_tools)
  ```

Add the method right after `_close_child`, i.e. immediately before `async def _child_edit_record_cb(`:

```python
    def _preseed_child_skills(self, handle: AgentHandle, active_skills: dict[str, str]) -> None:
        """`skills:` frontmatter pre-loads every listed skill (spec §5.2), capped like
        read_skill. Unknown or unreadable names are skipped with a warning."""
        name = handle.context.name
        if not is_skills_enabled():
            logger.warning("[subagent] agent %s lists skills but CRUCIBLE_SKILLS_ENABLED is "
                           "off — none pre-loaded", name)
            return
        catalog = {m.name: m for m in SkillCatalogLoader(self._workspace_path).load_catalog()}
        for skill in handle.definition.skills:
            manifest = catalog.get(skill)
            if manifest is None:
                logger.warning("[subagent] agent %s: unknown skill %r — skipped", name, skill)
                continue
            try:
                body = manifest.body_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[subagent] agent %s: cannot read skill %r: %s", name, skill, exc)
                continue
            active_skills[skill] = cap_skill_body(skill, body)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_agent_definitions_wiring.py tests/test_dispatch_integration.py tests/test_dispatch_tool_source.py tests/test_subagent_lifecycle.py tests/test_subagent_routes.py`
Expected: all pass.

- [ ] **Step 6: Lint, type-check, commit**

Run `ruff check --output-format concise agentd/subagents agentd/chat/controller.py tests/test_agent_definitions_wiring.py`. Expected: exactly one finding, `agentd/chat/controller.py:8:1: I001`. It is pre-existing at `1bf21c6` (the `chat.rewind` and `subagents.runtime` imports are out of order there) and is not this task's to fix.

Then run `mypy agentd/subagents agentd/chat/controller.py > /tmp/mypy-after.txt` and compare with the same command run at `1bf21c6` (`git stash` is shared across worktrees; use `git worktree add /tmp/p3-base 1bf21c6` for the baseline):

```bash
comm -13 <(sort /tmp/mypy-before.txt) <(sort /tmp/mypy-after.txt) | grep -c error
```

Expected: `0` new errors.

```bash
git add agentd/chat/controller.py tests/test_agent_definitions_wiring.py
git commit -m "feat(subagents): offer discovered agents and honor their tools, edits and skills"
```

---

### Task F6: `subagent-driven-development` becomes executable when the flag is on

**Files:**
- Modify: `agentd/chat/controller_loop.py`
- Test: `tests/test_controller_required_subskill_autoload.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_subagent_driven_development_is_executable_when_subagents_are_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    assert _pick_executable_required_subskill(
        ["subagent-driven-development", "executing-plans"]) == "subagent-driven-development"
```

(Add `import pytest` at the top if the file doesn't import it yet.)

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_controller_required_subskill_autoload.py`
Expected: the new test fails with `'executing-plans' == 'subagent-driven-development'`; the existing flag-off tests pass.

- [ ] **Step 3: Implement** — replace the comment and constant at `controller_loop.py:498-503` (the block starting `# Crucible's chat controller has no subagent-dispatch tool`):

```python
# A plan directive naming "subagent-driven-development" needs a sub-agent dispatch tool.
# This host has one (dispatch_agents) only while CRUCIBLE_SUBAGENTS_ENABLED is on; with it
# off, only its sibling "executing-plans" (one continuous session) is runnable here. A fact
# about THIS host's tools, not a preference between the two skills (spec §16 phase 3).
_SUBAGENT_ONLY_SUBSKILLS = frozenset({"subagent-driven-development"})


def _non_executable_subskills() -> frozenset[str]:
    from agentd.chat.controller_factory import is_subagents_enabled

    return frozenset() if is_subagents_enabled() else _SUBAGENT_ONLY_SUBSKILLS
```

Then in `_pick_executable_required_subskill`, change the body to:

```python
    blocked = _non_executable_subskills()
    return next((n for n in names if n not in blocked), None)
```

and update its docstring reference from `_NON_EXECUTABLE_SUBSKILLS` to `_non_executable_subskills()`. Run `grep -rn "_NON_EXECUTABLE_SUBSKILLS" agentd tests` and update every remaining reference; expect only comments.

The function-local import mirrors how `controller_prompts.py` imports flag resolvers. It avoids an import cycle: `controller_factory` imports the controller, which imports this module.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_controller_required_subskill_autoload.py tests/test_agent_loop.py`
Expected: all pass.

Run: `ruff check --output-format concise agentd/chat/controller_loop.py tests/test_controller_required_subskill_autoload.py`. Expected: only the four `E501` findings that already exist at `1bf21c6` (`controller_loop.py` lines 1229/1485/1497/1500 there; they shift +6 with this change). No finding in the lines this task touched.

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_loop.py tests/test_controller_required_subskill_autoload.py
git commit -m "feat(subagents): subagent-driven-development is executable when dispatch exists"
```

---

### Task F7: Full verification and docs

- [ ] **Step 1: Full backend suite**

Run: `pytest --color=no > /tmp/p3-full.txt 2>&1; echo exit=$?; tail -5 /tmp/p3-full.txt`
Expected: the only failure is the pre-existing `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies`, which also fails at `1bf21c6`. Anything else is a regression: reproduce it in isolation before attributing it (CLAUDE.md "shifting failure set").

- [ ] **Step 2: TypeScript is untouched.** Run `git diff --stat 1bf21c6 -- apps`; expect empty. The editor-client contract has no definition fields: the roster shows `name`/`label`, which already exist.

- [ ] **Step 3: Live check** (backend from `start-backend.sh` with `CRUCIBLE_SUBAGENTS_ENABLED=1`):
  1. Write `<ws>/.claude/agents/test-writer.md`:

     ```markdown
     ---
     name: test-writer
     description: Writes pytest tests for one module it is given; never edits source files.
     tools: Read, Grep, Glob, Bash, Write
     permissionMode: acceptEdits
     model: sonnet
     ---
     Write focused pytest tests. Touch only files under tests/.
     ```

  2. Without restarting, ask the chat to "use the test-writer agent to add tests for shop/cart.py".
  3. Confirm all of the following:
     - `[agents] … model 'sonnet' is a Claude Code alias` appears in the log.
     - The dispatch names `test-writer`.
     - The child's edits auto-accept with no gate (`acceptEdits`).
     - `GET /agents/{id}` shows `name: test-writer`.

- [ ] **Step 4: CLAUDE.md** — in "Sub-agents (P5)", change the Dispatch bullet's "Phase 3 adds `.crucible/agents` / `.claude/agents` discovery" to describe what shipped:
  - `AgentCatalogLoader` (`subagents/agent_files.py`) with precedence `.crucible/agents` → `.claude/agents` → `~/.claude/agents` → built-ins, recursive `*.md` discovery, and a per-file `(path, mtime)` cache (with why: root mtime misses nested edits);
  - the Claude Code tool mapping, with `edit` as the action pseudo-tool and `mcp__<server>__*` wildcards;
  - `bypassPermissions`→`default`; `sonnet|opus|haiku|fable`→`inherit`;
  - `skills:` pre-seed (needs `CRUCIBLE_SKILLS_ENABLED`);
  - `subagent-driven-development` executable only with the flag on.

  Commit with `docs(claude): sub-agent definitions`.
