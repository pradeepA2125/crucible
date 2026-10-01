"""VCS guard for sub-agents (spec §4.6.6).

Siblings edit one shared workspace and the main agent commits after the batch, so a
child must not mutate version control. This runs inside the child's run_command BEFORE
the approval callback, so nobody is ever shown a card for a command that will be refused.
It parses the exact shell line run_command would execute and fails CLOSED: anything it
cannot tokenize is refused. VCS calls hidden inside scripts, make targets or npm hooks are
invisible to it (accepted residual gap); the role block's rule is the backstop.
"""
from __future__ import annotations

import os
import shlex

VCS_REFUSAL = (
    "Version-control changes are blocked for sub-agents: other agents are editing this "
    "workspace, and the main agent commits after the work is done. Read-only git "
    "(status/diff/log/show) is fine.")

_READONLY_SUBCOMMANDS = frozenset({
    "status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse",
    "describe", "shortlog", "cat-file"})
_CONFIG_READ_FLAGS = frozenset({"--get", "--get-all", "--list"})
_GLOBAL_OPTS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree"})
_GLOBAL_OPTS_EQUALS = ("--git-dir=", "--work-tree=", "--exec-path=")
_GLOBAL_FLAGS = frozenset({"--no-pager", "-P", "--exec-path"})
_SHELLS = frozenset({"sh", "bash", "zsh"})
_OPERATOR_CHARS = frozenset(";&|()")
_MAX_NESTING = 8


class _Unparseable(ValueError):
    """The line cannot be analysed reliably — the guard refuses it."""


def vcs_refusal(command_line: str) -> str | None:
    try:
        return VCS_REFUSAL if _mutates(command_line, 0) else None
    except _Unparseable:
        return VCS_REFUSAL


def _mutates(line: str, depth: int) -> bool:
    if depth > _MAX_NESTING:
        raise _Unparseable("nesting too deep")
    for inner in _substitutions(line):
        if _mutates(inner, depth + 1):
            return True
    return any(_segment_mutates(segment, depth) for segment in _segments(_tokens(line)))


def _substitutions(line: str) -> list[str]:
    """Inner text of every $(…) and `…` that the shell would execute: single-quoted text
    is literal, double-quoted text still substitutes."""
    found: list[str] = []
    in_double = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not in_double:
            end = line.find("'", i + 1)
            if end == -1:
                raise _Unparseable("unbalanced single quote")
            i = end + 1
            continue
        if ch == '"':
            in_double = not in_double
            i += 1
            continue
        if line.startswith("$(", i):
            level, j = 1, i + 2
            while j < n and level:
                if line[j] == "(":
                    level += 1
                elif line[j] == ")":
                    level -= 1
                j += 1
            if level:
                raise _Unparseable("unbalanced $(")
            found.append(line[i + 2:j - 1])
            i = j
            continue
        if ch == "`":
            end = line.find("`", i + 1)
            if end == -1:
                raise _Unparseable("unbalanced backtick")
            found.append(line[i + 1:end])
            i = end + 1
            continue
        i += 1
    if in_double:
        raise _Unparseable("unbalanced double quote")
    return found


def _tokens(line: str) -> list[str]:
    # punctuation_chars splits operators even without spaces (a&&git commit) and makes
    # subshell parens their own tokens (( git commit )).
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError as exc:
        raise _Unparseable(str(exc)) from exc


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and set(token) <= _OPERATOR_CHARS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _segment_mutates(segment: list[str], depth: int) -> bool:
    # ANY token counts, not just the first: `timeout 60 git …`, `xargs git …`,
    # `find -exec git …` and `VAR=x git …` are all git invocations.
    for i, token in enumerate(segment):
        base = os.path.basename(token)
        if base == "git" and _git_mutates(segment[i + 1:]):
            return True
        if base in _SHELLS:
            script = _shell_script(segment[i + 1:])
            if script is not None and _mutates(script, depth + 1):
                return True
        if base == "eval" and _mutates(" ".join(segment[i + 1:]), depth + 1):
            return True
    return False


def _shell_script(rest: list[str]) -> str | None:
    """The -c script of `sh|bash|zsh [flags] -c SCRIPT` (combined flags like -lc count)."""
    for j, token in enumerate(rest):
        if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
            return rest[j + 1] if j + 1 < len(rest) else None
        if not token.startswith("-"):
            return None  # a script file, not -c
    return None


def _git_mutates(rest: list[str]) -> bool:
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
        elif token.startswith(_GLOBAL_OPTS_EQUALS) or token in _GLOBAL_FLAGS:
            i += 1
        else:
            break
    if i >= len(rest):
        return False  # bare `git`: prints usage
    subcommand = rest[i]
    if subcommand == "config":
        flags = [a for a in rest[i + 1:] if a.startswith("-")]
        return not (flags and all(f in _CONFIG_READ_FLAGS for f in flags))
    # An allow-list, not a deny-list: user aliases (`git ci`) can never be enumerated.
    return subcommand not in _READONLY_SUBCOMMANDS
