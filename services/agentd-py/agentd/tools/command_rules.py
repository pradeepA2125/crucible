"""Per-workspace persistent store of user-approved shell-command rules.

Backs the "Accept & remember (this workspace)" choice of the command-approval
gate. Rules are matched against the command's *token list* (shlex-tokenized),
NOT character `startswith` — so a `cat /etc/passwd` prefix rule does not bleed
into `/etc/password-store/secret`.

run_command executes a real shell line (operators such as `;`, `&&`, `|` and `>`
pass through unquoted), so a rule may approve a command only when EVERY command
on that line matches a rule: `git status ; curl … | sh` is not covered by a
remembered `git status`. File redirection, command substitution, subshells and
anything this module cannot parse never match — they fall through to the
approval card.
"""
from __future__ import annotations

import json
import os
import shlex
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentd.approvals.command_parse import (
    ASSIGNMENT,
    MAX_WRAPPER_DEPTH,
    UnsafeShellSyntax,
    strict_segments,
    wrapped_script,
)
from agentd.domain.models import CommandDecision, CommandRule
from agentd.tools.shell import _build_shell_command_line, _split_command


def rule_from_decision(
    decision: CommandDecision, command: str, args: list[str],
) -> CommandRule | None:
    """Derive the persistable CommandRule from an approve+remember decision; None when
    the decision is not a remember. Shared by the task engine and the chat controller
    so the scope→value derivation never drifts between the two gate paths."""
    if not (decision.approve and decision.remember):
        return None
    if decision.rule_value:
        value = decision.rule_value
    elif decision.scope == "binary":
        value = command.rsplit("/", 1)[-1]
    elif decision.scope == "exact":
        value = shlex.join([command, *args])
    else:  # prefix with no explicit value → lock command + first arg
        toks = [command, *args]
        value = shlex.join(toks[:2] if len(toks) > 1 else toks)
    return CommandRule(
        type=decision.scope,
        value=value,
        added_at=datetime.now(UTC).isoformat(),
    )


def _tokenize(s: str) -> list[str]:
    """shlex-split a command string; fall back to whitespace split on malformed quoting."""
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


def _segment_is_covered(
    rules: Sequence[CommandRule], segment: list[str], depth: int,
) -> bool:
    # An exact rule is the precise command the user approved, wrapper or not.
    if any(r.type == "exact" and _tokenize(r.value) == segment for r in rules):
        return True
    script = wrapped_script(segment)
    if script is not None:
        # A `bash` binary or `bash -c` prefix rule would otherwise approve any script.
        return _line_is_covered(rules, script, depth + 1)
    if ASSIGNMENT.match(segment[0]):
        # `X=/usr/bin/git curl …` runs curl; its first word is not the executable.
        return False
    return any(CommandRuleStore.rule_matches(r, segment) for r in rules)


def _line_is_covered(rules: Sequence[CommandRule], line: str, depth: int) -> bool:
    if depth > MAX_WRAPPER_DEPTH:
        raise UnsafeShellSyntax("shell wrappers nested too deep")
    return all(_segment_is_covered(rules, s, depth) for s in strict_segments(line))


def command_is_covered(
    rules: Sequence[CommandRule],
    command: str,
    args: list[str] | None,
    workspace_path: str | Path,
) -> bool:
    """True when `rules` approve every command `run_command` would execute.

    `args=None` treats `command` as a whole shell line (test/CLI use). Otherwise the
    line is the one run_command builds from `command` + `args` — including the
    model packing the whole line into `command` — so the check sees exactly what
    the shell runs."""
    raw_tokens = _tokenize(command) if args is None else [command, *args]
    if not raw_tokens or not rules:
        return False
    if any(r.type == "exact" and _tokenize(r.value) == raw_tokens for r in rules):
        return True
    line = (command if args is None
            else _build_shell_command_line(*_split_command(command, args, Path(workspace_path))))
    try:
        return _line_is_covered(rules, line, 0)
    except UnsafeShellSyntax:
        return False


class CommandRuleStore:
    def __init__(self, workspace_path: str | Path) -> None:
        self._workspace_path = Path(workspace_path)
        self._path = self._workspace_path / ".crucible" / "approved-commands.json"

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
        """Token-aware match of ONE simple command — NOT character startswith.
        A whole command line goes through `command_is_covered`, which splits it
        into simple commands first."""
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
        return command_is_covered(self.load(), command, args, self._workspace_path)

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
