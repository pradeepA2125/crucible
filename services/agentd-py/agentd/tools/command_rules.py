"""Per-workspace persistent store of user-approved shell-command rules.

Backs the "Accept & remember (this workspace)" choice of the command-approval
gate. Rules are matched against the command's *token list* (shlex-tokenized),
NOT character `startswith` — so a `cat /etc/passwd` prefix rule does not bleed
into `/etc/password-store/secret`.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from agentd.domain.models import CommandRule


def _tokenize(s: str) -> list[str]:
    """shlex-split a command string; fall back to whitespace split on malformed quoting."""
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


class CommandRuleStore:
    def __init__(self, workspace_path: str | Path) -> None:
        self._path = Path(workspace_path) / ".ai-editor" / "approved-commands.json"

    def load(self) -> list[CommandRule]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [CommandRule(**r) for r in raw if isinstance(r, dict)]

    @staticmethod
    def rule_matches(rule: CommandRule, cmd_tokens: list[str]) -> bool:
        """Token-aware match — NOT character startswith."""
        if not cmd_tokens:
            return False
        if rule.type == "binary":
            return Path(cmd_tokens[0]).name == rule.value
        rule_tokens = _tokenize(rule.value)
        if rule.type == "exact":
            return cmd_tokens == rule_tokens
        if rule.type == "prefix":
            return bool(rule_tokens) and cmd_tokens[: len(rule_tokens)] == rule_tokens
        return False

    def matches(self, command: str, args: list[str] | None = None) -> bool:
        """`args=None` → treat `command` as a full \"cmd args\" string (test/CLI use).
        Otherwise treat `[command, *args]` as the token list directly."""
        cmd_tokens = _tokenize(command) if args is None else [command, *args]
        return any(self.rule_matches(r, cmd_tokens) for r in self.load())

    def add(self, rule: CommandRule) -> None:
        rules = self.load()
        if any(r.type == rule.type and r.value == rule.value for r in rules):
            return
        rules.append(rule)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a reader never sees a half-written file.
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps([r.model_dump() for r in rules], indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)
