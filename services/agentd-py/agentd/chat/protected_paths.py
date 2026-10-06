"""Files that control what agents may do are not editable by agents (spec §3.9)."""
from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatchcase

from agentd.domain.models import PatchFailureCode, PatchPreflightIssue
from agentd.patch.engine import PatchPreflightFailed

PROTECTED_PATTERNS: tuple[str, ...] = (
    ".crucible/*", ".claude/agents/*", ".claude/skills/*", "AGENTS.md",
    ".vscode/settings.json", ".vscode/tasks.json", ".vscode/launch.json", "*.code-workspace",
)


def is_protected(key: str) -> bool:
    """`key` is a canonical workspace-relative POSIX path (write_log.canonical_path), so a
    symlink into a protected directory is matched by where it really points."""
    return any(fnmatchcase(key, pattern) for pattern in PROTECTED_PATTERNS)


class ProtectedPathError(PatchPreflightFailed):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(message, [PatchPreflightIssue(
            code=PatchFailureCode.PROTECTED_PATH, file=path, message=message)])
        self.path = path


class AgentProtection:
    """A sub-agent or team member: refused outright, at apply and again at accept."""
    requires_review = False

    def check_apply(self, keys: list[str]) -> None:
        self.check_accept(keys)

    def check_accept(self, keys: list[str]) -> None:
        for key in keys:
            if is_protected(key):
                raise ProtectedPathError(
                    key, f"`{key}` is a protected Crucible configuration file; agents cannot "
                    "edit it. Tell your dispatcher what should change instead.")


class TeamScopeError(PatchPreflightFailed):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(message, [PatchPreflightIssue(
            code=PatchFailureCode.TEAM_SCOPE, file=path, message=message)])
        self.path = path


class TeamProtection(AgentProtection):
    """A team member or its helper (spec v2 §3.9, §8.6): protected paths first, then the
    team's phase and plan, checked at Check 1 before any edit gate. One per activation, so
    a repeated ownership refusal for the same path can say so."""

    def __init__(self, rule: Callable[[str], str | None]) -> None:
        self._rule = rule
        self._told: set[str] = set()

    def check_apply(self, keys: list[str]) -> None:
        super().check_apply(keys)
        for key in keys:
            message = self._rule(key)
            if message is None:
                continue
            if " is owned by " in message:
                if key in self._told:
                    owner = message.split(" is owned by ", 1)[1].split(" ", 1)[0]
                    message += (f" You were already told this file is owned by {owner}. "
                                "Do not retry the edit.")
                self._told.add(key)
            raise TeamScopeError(key, message)


class MainProtection:
    """The main agent may propose the edit, but it always raises a review card; at accept,
    a path that became protected after apply (a symlink created in between) is refused.
    `team_rule` refuses files a live team's plan covers: in the 5B live smoke the main
    agent kept doing a member's part itself, leaving its assignment open for good."""

    def __init__(self, team_rule: Callable[[str], str | None] | None = None) -> None:
        self.requires_review = False
        self._approved: set[str] = set()
        self._team_rule = team_rule

    def check_apply(self, keys: list[str]) -> None:
        if self._team_rule is not None:
            for key in keys:
                message = self._team_rule(key)
                if message is not None:
                    raise TeamScopeError(key, message)
        self._approved = {k for k in keys if is_protected(k)}
        self.requires_review = bool(self._approved)

    def check_accept(self, keys: list[str]) -> None:
        for key in keys:
            if is_protected(key) and key not in self._approved:
                raise ProtectedPathError(
                    key, f"`{key}` resolves to a protected file only since the edit was "
                    "shown for review; it was not applied.")


_COMMAND_MARKERS = (".crucible/", ".claude/agents", ".claude/skills", "AGENTS.md", ".vscode/",
                    ".code-workspace")


def command_mentions_protected(command: str, args: list[str]) -> bool:
    """A hint for the command card (spec §3.9): writes through run_command are the documented
    residual gap, so the card can at least highlight a command that names a protected path."""
    text = " ".join([command, *args])
    return any(marker in text for marker in _COMMAND_MARKERS)
