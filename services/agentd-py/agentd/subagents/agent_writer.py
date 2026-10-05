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
