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
