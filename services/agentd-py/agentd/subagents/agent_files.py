"""Agent definition files (spec §9): Claude Code–compatible Markdown with YAML frontmatter.

Parsing is best-effort by design: a malformed file is skipped (or a malformed field
ignored) with a warning, and loading never raises into a turn.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import yaml

from agentd.prompting.tagged import Permission
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition

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
