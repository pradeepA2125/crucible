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
import re
import shlex
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentd.domain.models import CommandDecision, CommandRule
from agentd.tools.shell import _build_shell_command_line, _split_command

_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Longest first, so the greedy match takes `&&` before `&` and `>>` before `>`.
_OPERATORS = (
    "&&", "||", ";;", "|&", "&>>", "&>", ">>", ">&", ">|", "<<<", "<<", "<&", "<>",
    ";", "&", "|", "<", ">", "(", ")", "\n",
)
_SEPARATORS = frozenset({"&&", "||", ";", "&", "|", "|&", "\n"})
# A separator that needs a command after it; ending a line or an empty command on it
# is a syntax error, so it is refused rather than guessed at.
_NEEDS_RIGHT_SIDE = frozenset({"&&", "||", "|", "|&"})
# `2>&1` / `<&0` duplicate a descriptor (no file is touched); `>&-` closes one.
_FD_DUPLICATIONS = frozenset({">&", "<&"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_C_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
# `-o`/`-O` take an argument, so a cluster holding them cannot be parsed as flags only.
_SHORT_SHELL_OPTIONS = re.compile(r"[-+][A-NP-Za-np-z]+")
_LONG_SHELL_OPTIONS = frozenset({"--login", "--noprofile", "--norc", "--posix"})
_MAX_WRAPPER_DEPTH = 3


class _UnsafeShellSyntax(ValueError):
    """The line holds syntax a remembered rule must never approve (redirection to a
    file, command substitution, a subshell) or syntax this module cannot parse."""


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


def _lex(line: str) -> list[tuple[bool, str]]:
    """Split a shell line into `(is_operator, text)` items, quotes removed from words.

    A POSIX subset: single/double quotes, backslash escapes, the operators above and
    an all-digit word directly before `<`/`>` (a descriptor number, as in `2>&1`)."""
    if "`" in line or "$(" in line:
        # Conservative even inside single quotes: a rule never needs to cover one.
        raise _UnsafeShellSyntax("command substitution")
    items: list[tuple[bool, str]] = []
    word: list[str] = []
    in_word = False  # `''` is still a word, so emptiness of `word` is not enough
    quoted = False
    i = 0

    def flush() -> None:
        nonlocal word, in_word, quoted
        if in_word:
            items.append((False, "".join(word)))
        word, in_word, quoted = [], False, False

    while i < len(line):
        ch = line[i]
        if ch in " \t":
            flush()
            i += 1
            continue
        op = next((o for o in _OPERATORS if line.startswith(o, i)), None)
        if op is not None:
            if op[0] in "<>" and in_word and not quoted and "".join(word).isdigit():
                word, in_word = [], False  # descriptor number of this redirection
            else:
                flush()
            items.append((True, op))
            i += len(op)
            continue
        in_word = True
        if ch == "\\":
            if i + 1 >= len(line):
                raise _UnsafeShellSyntax("trailing backslash")
            word.append(line[i + 1])
            quoted = True
            i += 2
        elif ch == "'":
            end = line.find("'", i + 1)
            if end < 0:
                raise _UnsafeShellSyntax("unterminated single quote")
            word.append(line[i + 1:end])
            quoted = True
            i = end + 1
        elif ch == '"':
            i += 1
            while True:
                if i >= len(line):
                    raise _UnsafeShellSyntax("unterminated double quote")
                c = line[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\" and i + 1 < len(line) and line[i + 1] in '$`"\\\n':
                    word.append(line[i + 1])
                    i += 2
                    continue
                word.append(c)
                i += 1
            quoted = True
        else:
            word.append(ch)
            i += 1
    flush()
    return items


def _segments(line: str) -> list[list[str]]:
    """The simple commands of `line`, each as its word list; raises on file
    redirection, subshells and other syntax a rule must not approve."""
    segments: list[list[str]] = []
    current: list[str] = []
    last_separator = ""
    awaiting_descriptor = False
    for is_operator, text in _lex(line):
        if not is_operator:
            if awaiting_descriptor:
                if not (text.isdigit() or text == "-"):
                    raise _UnsafeShellSyntax(f"redirection to file {text!r}")
                awaiting_descriptor = False
            else:
                current.append(text)
            continue
        if awaiting_descriptor:
            raise _UnsafeShellSyntax("incomplete descriptor duplication")
        if text in _FD_DUPLICATIONS:
            awaiting_descriptor = True
        elif text in _SEPARATORS:
            if current:
                segments.append(current)
            elif text in _NEEDS_RIGHT_SIDE or last_separator in _NEEDS_RIGHT_SIDE:
                raise _UnsafeShellSyntax(f"empty command around {text!r}")
            current, last_separator = [], text
        else:
            raise _UnsafeShellSyntax(f"unsupported shell syntax {text!r}")
    if awaiting_descriptor:
        raise _UnsafeShellSyntax("incomplete descriptor duplication")
    if current:
        segments.append(current)
    elif last_separator in _NEEDS_RIGHT_SIDE:
        raise _UnsafeShellSyntax(f"line ends with {last_separator!r}")
    if not segments:
        raise _UnsafeShellSyntax("no command")
    return segments


def _wrapped_script(segment: list[str]) -> str | None:
    """The SCRIPT of an `sh -c SCRIPT` style segment; None when it is not one.

    Only `<shell> [flags] -c SCRIPT` parses: options taking an argument, extra
    positional words after SCRIPT, or a `-c` anywhere else raise."""
    if Path(segment[0]).name not in _SHELLS:
        return None
    if not any(_C_FLAG.fullmatch(token) for token in segment[1:]):
        return None
    *options, script = segment[1:]
    if (not options or not _C_FLAG.fullmatch(options[-1])
            or not all(_SHORT_SHELL_OPTIONS.fullmatch(o) or o in _LONG_SHELL_OPTIONS
                       for o in options)):
        raise _UnsafeShellSyntax(f"unparseable {segment[0]} -c invocation")
    return script


def _segment_is_covered(
    rules: Sequence[CommandRule], segment: list[str], depth: int,
) -> bool:
    # An exact rule is the precise command the user approved, wrapper or not.
    if any(r.type == "exact" and _tokenize(r.value) == segment for r in rules):
        return True
    script = _wrapped_script(segment)
    if script is not None:
        # A `bash` binary or `bash -c` prefix rule would otherwise approve any script.
        return _line_is_covered(rules, script, depth + 1)
    if _ASSIGNMENT.match(segment[0]):
        # `X=/usr/bin/git curl …` runs curl; its first word is not the executable.
        return False
    return any(CommandRuleStore.rule_matches(r, segment) for r in rules)


def _line_is_covered(rules: Sequence[CommandRule], line: str, depth: int) -> bool:
    if depth > _MAX_WRAPPER_DEPTH:
        raise _UnsafeShellSyntax("shell wrappers nested too deep")
    return all(_segment_is_covered(rules, s, depth) for s in _segments(line))


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
    except _UnsafeShellSyntax:
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
