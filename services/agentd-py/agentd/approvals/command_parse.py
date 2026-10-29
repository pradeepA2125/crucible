"""The one shell-line parser shared by remembered-rule matching and Auto approval.

run_command executes a real shell line: operators such as `;`, `&&`, `|` and `>` pass
through unquoted. Every approval check must therefore look at each simple command on
the line, not at its first words. `strict_segments` refuses anything a remembered rule
must never approve; `parse_command` keeps going and reports what it found (redirect
targets, assignments, substitution) so Auto can deny, ask or judge instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentd.tools.shell import _build_shell_command_line, _split_command

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
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
# Operators whose next word is a file the shell writes.
_WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})
# Operators whose next word is only read (an input file or a here-string).
_READ_REDIRECTS = frozenset({"<", "<<<"})
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_C_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
# `-o`/`-O` take an argument, so a cluster holding them cannot be parsed as flags only.
_SHORT_SHELL_OPTIONS = re.compile(r"[-+][A-NP-Za-np-z]+")
_LONG_SHELL_OPTIONS = frozenset({"--login", "--noprofile", "--norc", "--posix"})
MAX_WRAPPER_DEPTH = 3
# `env` options that take a value (`env -u NAME cmd`, `env -C dir cmd`).
_ENV_OPTIONS_WITH_VALUE = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})


class UnsafeShellSyntax(ValueError):
    """The line holds syntax a remembered rule must never approve (redirection to a
    file, command substitution, a subshell) or syntax this module cannot parse."""


def lex(line: str) -> list[tuple[bool, str]]:
    """Split a shell line into `(is_operator, text)` items, quotes removed from words.

    A POSIX subset: single/double quotes, backslash escapes, the operators above and
    an all-digit word directly before `<`/`>` (a descriptor number, as in `2>&1`)."""
    if "`" in line or "$(" in line:
        # Conservative even inside single quotes: a rule never needs to cover one.
        raise UnsafeShellSyntax("command substitution")
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
                raise UnsafeShellSyntax("trailing backslash")
            word.append(line[i + 1])
            quoted = True
            i += 2
        elif ch == "'":
            end = line.find("'", i + 1)
            if end < 0:
                raise UnsafeShellSyntax("unterminated single quote")
            word.append(line[i + 1:end])
            quoted = True
            i = end + 1
        elif ch == '"':
            i += 1
            while True:
                if i >= len(line):
                    raise UnsafeShellSyntax("unterminated double quote")
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


_PIPES = frozenset({"|", "|&"})


def _walk(line: str, *, strict: bool) -> tuple[list[list[str]], list[bool], list[str]]:
    """Simple commands of `line`, whether each one's output is piped into the next,
    and the files the line redirects into.

    `strict` refuses any redirection to a file (remembered rules); otherwise write
    targets are collected and input redirections are skipped (Auto)."""
    segments: list[list[str]] = []
    piped: list[bool] = []
    targets: list[str] = []
    current: list[str] = []
    last_separator = ""
    awaiting: str | None = None  # the redirection operator whose word comes next
    for is_operator, text in lex(line):
        if not is_operator:
            if awaiting in _FD_DUPLICATIONS:
                if not (text.isdigit() or text == "-"):
                    if strict:
                        raise UnsafeShellSyntax(f"redirection to file {text!r}")
                    targets.append(text)  # `>&file` writes both streams to `file`
            elif awaiting in _WRITE_REDIRECTS:
                targets.append(text)
            elif awaiting is None:
                current.append(text)
            awaiting = None
            continue
        if awaiting is not None:
            raise UnsafeShellSyntax("incomplete redirection")
        if text in _FD_DUPLICATIONS:
            awaiting = text
        elif text in _SEPARATORS:
            if current:
                segments.append(current)
                piped.append(text in _PIPES)
            elif text in _NEEDS_RIGHT_SIDE or last_separator in _NEEDS_RIGHT_SIDE:
                raise UnsafeShellSyntax(f"empty command around {text!r}")
            current, last_separator = [], text
        elif not strict and (text in _WRITE_REDIRECTS or text in _READ_REDIRECTS):
            awaiting = text
        else:
            raise UnsafeShellSyntax(f"unsupported shell syntax {text!r}")
    if awaiting is not None:
        raise UnsafeShellSyntax("incomplete redirection")
    if current:
        segments.append(current)
        piped.append(False)
    elif last_separator in _NEEDS_RIGHT_SIDE:
        raise UnsafeShellSyntax(f"line ends with {last_separator!r}")
    if not segments:
        raise UnsafeShellSyntax("no command")
    return segments, piped, targets


def strict_segments(line: str) -> list[list[str]]:
    """The simple commands of `line`, each as its word list; raises on file
    redirection, subshells and other syntax a rule must not approve."""
    segments, _piped, _targets = _walk(line, strict=True)
    return segments


def wrapped_script(segment: list[str]) -> str | None:
    """The SCRIPT of an `sh -c SCRIPT` style segment; None when it is not one.

    Only `<shell> [flags] -c SCRIPT` parses: options taking an argument, extra
    positional words after SCRIPT, or a `-c` anywhere else raise."""
    if Path(segment[0]).name not in SHELLS:
        return None
    if not any(_C_FLAG.fullmatch(token) for token in segment[1:]):
        return None
    *options, script = segment[1:]
    if (not options or not _C_FLAG.fullmatch(options[-1])
            or not all(_SHORT_SHELL_OPTIONS.fullmatch(o) or o in _LONG_SHELL_OPTIONS
                       for o in options)):
        raise UnsafeShellSyntax(f"unparseable {segment[0]} -c invocation")
    return script


@dataclass(frozen=True)
class ParsedCommand:
    line: str                               # the exact line run_command would execute
    segments: tuple[tuple[str, ...], ...]   # argv per simple command, wrappers unwrapped
    redirect_targets: tuple[str, ...]       # files written via > >> >| &> &>> <> >&file
    assignments: tuple[str, ...]            # variable names set by NAME=, env or export
    has_substitution: bool                  # $( ) or backticks anywhere
    parse_failed: bool                      # unbalanced quotes, subshells, heredocs…
    piped: tuple[bool, ...] = ()            # per segment: its output feeds the next one


def _strip_env(segment: list[str], assignments: list[str]) -> list[str]:
    """Drop leading NAME=value words and an `env [options] NAME=value…` prefix,
    recording the names: `HOME=/tmp rm -rf ~` runs `rm`, with HOME shadowed."""
    words = list(segment)
    while True:
        while words and ASSIGNMENT.match(words[0]):
            assignments.append(words.pop(0).split("=", 1)[0])
        if not words or Path(words[0]).name != "env":
            return words
        words.pop(0)
        while words and words[0].startswith("-"):
            option = words.pop(0)
            if option in _ENV_OPTIONS_WITH_VALUE and words:
                words.pop(0)


def _collect(line: str, depth: int, segments: list[tuple[str, ...]], piped: list[bool],
             targets: list[str], assignments: list[str]) -> None:
    if depth > MAX_WRAPPER_DEPTH:
        raise UnsafeShellSyntax("shell wrappers nested too deep")
    raw_segments, raw_piped, raw_targets = _walk(line, strict=False)
    targets.extend(raw_targets)
    for raw, feeds_next in zip(raw_segments, raw_piped, strict=True):
        words = _strip_env(raw, assignments)
        if not words:
            continue  # `env` alone, or only assignments
        if words[0] == "export":
            assignments.extend(w.split("=", 1)[0] for w in words[1:] if ASSIGNMENT.match(w))
        script = wrapped_script(words)
        if script is not None:
            _collect(script, depth + 1, segments, piped, targets, assignments)
            if piped:
                # The wrapper's own output is its last inner command's output.
                piped[-1] = feeds_next
        else:
            segments.append(tuple(words))
            piped.append(feeds_next)


def parse_command(command: str, args: list[str], workspace: Path) -> ParsedCommand:
    """Parse the line run_command would build from `command` + `args`. Never raises:
    anything it cannot read sets `parse_failed` (with no segments), so callers fail
    closed by sending the command to a person or the judge."""
    line = _build_shell_command_line(*_split_command(command, args, workspace))
    if "`" in line or "$(" in line:
        return ParsedCommand(line, (), (), (), has_substitution=True, parse_failed=False)
    segments: list[tuple[str, ...]] = []
    piped: list[bool] = []
    targets: list[str] = []
    assignments: list[str] = []
    try:
        _collect(line, 0, segments, piped, targets, assignments)
    except UnsafeShellSyntax:
        return ParsedCommand(line, (), (), (), has_substitution=False, parse_failed=True)
    return ParsedCommand(line, tuple(segments), tuple(targets), tuple(assignments),
                         has_substitution=False, parse_failed=False, piped=tuple(piped))
