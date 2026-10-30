"""Auto mode's deterministic checks, run before any model call (spec §6.2).

Order of use (AutoGate): deny_reason → writes_protected → rules_cover → is_safe → judge.
Every check fails closed: a line the parser could not read is never safe, never
covered by a rule, and counts as a protected write when it names a protected path.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path

from agentd.approvals.command_parse import ParsedCommand
from agentd.chat.protected_paths import command_mentions_protected
from agentd.domain.models import CommandRule
from agentd.subagents.write_log import canonical_path
from agentd.tools.command_rules import command_is_covered

_INTERPRETERS = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "python", "python3", "node", "perl", "ruby", "php",
})
_DOWNLOADERS = frozenset({"curl", "wget"})
# Prefixes that run the next words as the real command.
_PASSTHROUGH = frozenset({"sudo", "doas", "nohup", "time", "exec", "command", "nice"})
_RECURSIVE_LONG = frozenset({"--recursive"})
_SHADOWED = frozenset({"HOME", "PATH"})
# Any binary or package runner that executes arbitrary code by itself.
_BROAD_BINARIES = frozenset({
    "uv", "python", "python3", "node", "npx", "bash", "sh", "zsh", "git", "npm", "pnpm",
    "yarn", "bun", "make", "cargo", "go", "ruby", "perl",
})
_PACKAGE_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun", "uv", "cargo"})
_SAFE_GIT = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "branch"})
_GIT_BRANCH_MUTATIONS = frozenset({
    "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force",
    "-u", "--set-upstream-to", "--unset-upstream", "-t", "--track", "--no-track",
    "--edit-description",
})
_SAFE_READERS = frozenset({"ls", "cat", "head", "tail", "wc", "rg", "grep", "pwd", "which",
                           "date", "echo"})
_FIND_ACTIONS = frozenset({"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint",
                           "-fprint0", "-fprintf", "-fls"})
# Commands whose file arguments they write.
_WRITERS_ALL_ARGS = frozenset({
    "tee", "touch", "truncate", "rm", "rmdir", "mkdir", "chmod", "chown",
})
_WRITERS_LAST_ARG = frozenset({"cp", "mv", "ln", "install", "rsync"})
# Shell writes Auto never leaves to the judge: they grant permissions (approval rules,
# MCP servers, agent definitions), become your words (AGENTS.md, prompt files, the chat
# record in .crucible/state), or make tools run code later (VS Code tasks, git hooks).
# One wrong verdict here would persist, so these always ask.
ALWAYS_ASK_IN_WORKSPACE = (
    "AGENTS.md", ".crucible/approved-*.json", ".crucible/mcp.json", ".crucible/agents/*",
    ".crucible/skills/*", ".crucible/prompts/*", ".crucible/state/*", ".claude/agents/*",
    ".claude/skills/*", ".vscode/settings.json", ".vscode/tasks.json", ".vscode/launch.json",
    "*.code-workspace", ".git/hooks/*", ".git/config", ".mcp.json", ".envrc",
    ".claude/settings.json", ".claude/settings.local.json",
)
# Outward actions: publishing or changing something remote (spec 2026-10-30 "Auto with
# the sandbox"). Inside the sandbox most lack credentials anyway; these still go to the
# judge because a token in a workspace file or an allowed host can carry them out.
_GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_GH_OPTIONS_WITH_VALUE = frozenset({"-R", "--repo", "--hostname"})
# gh is an allowlist of reads: it gains new write commands more often than new read ones.
_GH_READS = {
    "pr": frozenset({"list", "view", "status", "diff", "checks"}),
    "issue": frozenset({"list", "view", "status"}),
    "run": frozenset({"list", "view", "watch", "download"}),
    "repo": frozenset({"list", "view"}),
    "release": frozenset({"list", "view", "download"}),
    "workflow": frozenset({"list", "view"}),
    "gist": frozenset({"list", "view"}),
    "label": frozenset({"list"}),
    "cache": frozenset({"list"}),
    "auth": frozenset({"status"}),
}
_GH_READ_GROUPS = frozenset({"search", "status", "help", "version", "browse"})
_GH_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")
# Any of these words anywhere after one of these tools. Global flags can sit before the
# subcommand and each tool spells them differently; a false match only costs a judge call.
_PUBLISHING_TOOLS = frozenset({"npm", "pnpm", "yarn", "bun", "cargo", "uv", "poetry", "twine",
                               "hatch", "flit", "gem", "docker", "podman", "nerdctl"})
_PUBLISHING_WORDS = frozenset({"publish", "unpublish", "push", "upload", "yank", "owner",
                               "dist-tag", "deprecate", "--push"})
_GIT_OUTWARD = frozenset({"push", "send-pack"})
# ~/.crucible holds agent trust, backend tokens and sign-ins.
_ALWAYS_ASK_IN_HOME = (".crucible/*", ".claude/agents/*", ".claude/skills/*")


def _command_words(segment: tuple[str, ...]) -> tuple[str, ...]:
    """Skip `sudo`-style prefixes so `sudo rm -rf /` is judged as `rm -rf /`."""
    words = segment
    while words and Path(words[0]).name in _PASSTHROUGH:
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
    return words


def _name(words: tuple[str, ...]) -> str:
    return Path(words[0]).name if words else ""


def _expand(path: str, *, workspace: Path, home: Path) -> Path:
    for prefix in ("~", "$HOME", "${HOME}"):
        if path == prefix or path.startswith(prefix + "/"):
            path = str(home) + path[len(prefix):]
            break
    candidate = Path(path)
    absolute = candidate if candidate.is_absolute() else workspace / candidate
    return Path(os.path.normpath(absolute))


def _is_catastrophic_target(raw: str, *, workspace: Path, home: Path) -> bool:
    """`/`, a top-level folder, the home folder or any of its ancestors, or the
    workspace or any of its ancestors. A trailing `/*` empties that folder."""
    stripped = raw[:-2] if raw.endswith("/*") else raw
    target = _expand(stripped or "/", workspace=workspace, home=home)
    resolved_workspace = Path(os.path.normpath(workspace.resolve()))
    for protected in (home, workspace, resolved_workspace):
        if target == protected or target in protected.parents:
            return True
    return target == Path("/") or target.parent == Path("/")


def _is_recursive(words: tuple[str, ...]) -> bool:
    for word in words[1:]:
        if word in _RECURSIVE_LONG:
            return True
        if word.startswith("-") and not word.startswith("--") and ("r" in word or "R" in word):
            return True
    return False


def _operands(words: tuple[str, ...]) -> list[str]:
    return [w for w in words[1:] if not w.startswith("-")]


def deny_reason(parsed: ParsedCommand, *, workspace: Path, home: Path) -> str | None:
    """A one-line, user-facing reason when the command is catastrophic; else None."""
    segments = [_command_words(s) for s in parsed.segments]
    destructive = False
    for index, words in enumerate(segments):
        name = _name(words)
        if name in {"rm", "rmdir"} and _is_recursive(words):
            destructive = True
            for target in _operands(words):
                if _is_catastrophic_target(target, workspace=workspace, home=home):
                    if _expand(target.removesuffix("/*") or "/", workspace=workspace,
                               home=home) == home:
                        return "recursive delete of your home folder"
                    return (f"recursive delete of {target}, which holds your system, "
                            "home or workspace")
        if name in {"chmod", "chown"} and _is_recursive(words):
            destructive = True
            if any(_is_catastrophic_target(t, workspace=workspace, home=home)
                   for t in _operands(words)[1:]):
                return f"recursive {name} on a system, home or workspace folder"
        if name.startswith("mkfs"):
            return "formats a disk"
        if name == "dd" and any(w.startswith("of=/dev/") for w in words[1:]):
            return "writes directly to a disk device"
        if name in _DOWNLOADERS and index < len(parsed.piped) and parsed.piped[index]:
            following = segments[index + 1] if index + 1 < len(segments) else ()
            if _name(following) in _INTERPRETERS:
                return "pipes a download straight into a shell or interpreter"
    if destructive and _SHADOWED.intersection(parsed.assignments):
        return "redefines HOME or PATH before a destructive command"
    return None


def _write_operands(words: tuple[str, ...]) -> list[str]:
    name = _name(words)
    operands = _operands(words)
    if name in _WRITERS_ALL_ARGS:
        return operands
    if name in _WRITERS_LAST_ARG:
        return operands[-1:]
    if name == "sed" and any(w == "-i" or w.startswith("-i") or w == "--in-place"
                             for w in words[1:]):
        return operands[1:]  # the first operand is the script
    return []


def _from_cwd(raw: str, cwd: str) -> str:
    """run_command resolves relative paths against its cwd argument."""
    if not cwd or Path(raw).is_absolute() or raw.startswith(("~", "$HOME", "${HOME}")):
        return raw
    return str(Path(cwd) / raw)


def _always_asks(raw: str, directory: str, *, workspace: Path, home: Path) -> bool:
    """A shell write to a file that grants permission or steers the agent. Narrower than
    the edit-protection list: a scratch file in `.crucible/` is left to the judge."""
    target = _expand(_from_cwd(raw, directory), workspace=workspace, home=home)
    key = canonical_path(workspace, str(target))
    if key is not None:
        return any(fnmatchcase(key, pattern) for pattern in ALWAYS_ASK_IN_WORKSPACE)
    try:
        relative = target.relative_to(home).as_posix()
    except ValueError:
        return False
    return any(fnmatchcase(relative, pattern) for pattern in _ALWAYS_ASK_IN_HOME)


def writes_protected(parsed: ParsedCommand, *, workspace: Path, home: Path | None = None,
                     cwd: str = "") -> bool:
    """True when the command writes (or may write) a file that grants permission or
    steers the agent, in the workspace or the home folder."""
    home = home or Path.home()
    if parsed.parse_failed or parsed.has_substitution:
        # Unreadable: treat any mention of a protected path as a write.
        return command_mentions_protected(parsed.line, [])
    candidates = list(parsed.redirect_targets)
    # Every directory the line may run in. Redirections are not tied to a segment, so
    # each write is checked against all of them (over-asking, never under-asking).
    directories = [cwd]
    untracked_cd = False
    for segment in parsed.segments:
        words = _command_words(segment)
        if _name(words) in {"cd", "pushd"}:
            operands = _operands(words)
            if len(operands) == 1 and operands[0] != "-":
                directories.append(_from_cwd(operands[0], directories[-1]))
            elif operands:
                untracked_cd = True
        elif _name(words) == "popd":
            untracked_cd = True
        candidates.extend(_write_operands(words))
    for raw in candidates:
        for directory in directories:
            if _always_asks(raw, directory, workspace=workspace, home=home):
                return True
    # `cd -` or `popd` lands somewhere we do not track: any write on a line that names a
    # protected path counts.
    return untracked_cd and bool(candidates) and command_mentions_protected(parsed.line, [])


def is_broad_rule(rule: CommandRule) -> bool:
    """A remembered rule that amounts to "run any code" (spec §6.2 step 3)."""
    tokens = rule.value.split()
    if not tokens:
        return False
    head = Path(tokens[0]).name
    if rule.type == "binary":
        return head in _BROAD_BINARIES
    if rule.type == "prefix":
        if len(tokens) == 1:
            return head in _BROAD_BINARIES
        return (len(tokens) == 2 and head in _PACKAGE_RUNNERS
                and tokens[1] in {"run", "exec", "x", "dlx"})
    return False


def rules_cover(rules: Sequence[CommandRule], command: str, args: list[str],
                workspace: Path) -> bool:
    """Remembered rules approve the command only when every part of the line is
    covered by a rule that is not broad (shared with Ask mode's coverage rule)."""
    narrow = [r for r in rules if not is_broad_rule(r)]
    return command_is_covered(narrow, command, args, workspace)


def _inside(raw: str, workspace: Path, cwd: str = "") -> bool:
    return canonical_path(workspace, _from_cwd(raw, cwd)) is not None


def _looks_like_path(word: str) -> bool:
    return "/" in word or word.startswith(("~", "."))


def _segment_is_safe(words: tuple[str, ...], workspace: Path, cwd: str = "") -> bool:
    name = _name(words)
    if not words:
        return False
    if name == "git":
        if len(words) < 2 or words[1] not in _SAFE_GIT:
            return False
        if any(w.startswith("--output") for w in words[2:]):
            return False
        if words[1] == "branch" and _GIT_BRANCH_MUTATIONS.intersection(
                w.split("=", 1)[0] for w in words[2:]):
            return False
        return True
    if name == "find":
        if _FIND_ACTIONS.intersection(words[1:]):
            return False
    elif name == "rg":
        if any(w == "--pre" or w.startswith("--pre=") for w in words[1:]):
            return False
    elif name not in _SAFE_READERS:
        return False
    return all(_inside(w, workspace, cwd) for w in words[1:]
               if not w.startswith("-") and _looks_like_path(w))


def _subcommand(words: tuple[str, ...], options_with_value: frozenset[str]) -> tuple[str, ...]:
    """The words from the subcommand on: `git -C dir push` → ("push",)."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in options_with_value else 1
    return words[i:]


def _gh_api_writes(rest: tuple[str, ...]) -> bool:
    for i, word in enumerate(rest):
        name, _, value = word.partition("=")
        if name in ("-X", "--method"):
            method = value or (rest[i + 1] if i + 1 < len(rest) else "")
            return method.upper() != "GET"
        if word.startswith("-X"):  # -XPOST
            return word[2:].upper() != "GET"
    # gh api switches to POST when given fields, including the attached `-fa=b` form.
    return any(w.startswith(_GH_FIELD_FLAGS) for w in rest)


def _gh_writes(rest: tuple[str, ...]) -> bool:
    if not rest:
        return False
    group, action = rest[0], rest[1] if len(rest) > 1 else ""
    if group == "api":
        return _gh_api_writes(rest)
    return group not in _GH_READ_GROUPS and action not in _GH_READS.get(group, frozenset())


def _git_pushes(words: tuple[str, ...]) -> bool:
    rest = _subcommand(words, _GIT_OPTIONS_WITH_VALUE)
    if rest[:1] and rest[0] in _GIT_OUTWARD or rest[:2] == ("lfs", "push"):
        return True
    # `git -c alias.p=push p`: the subcommand can be anything once an alias is set inline.
    return any(w.startswith("alias.") for w in words)


def _runs_git_push(words: tuple[str, ...]) -> bool:
    """`xargs git push`, `find . -exec git push`: a wrapper this parser doesn't unwrap."""
    for i, word in enumerate(words[1:], start=1):
        if Path(word).name == "git":
            return _git_pushes(words[i:])
    return False


def _publishes(words: tuple[str, ...]) -> bool:
    if _name(words).startswith("python") and "-m" in words:
        words = words[words.index("-m") + 1:]
    return _name(words) in _PUBLISHING_TOOLS and any(w in _PUBLISHING_WORDS for w in words[1:])


def is_outward(parsed: ParsedCommand) -> bool:
    """A command that publishes or changes something remote through a host the sandbox
    allows (pushes, releases, package uploads). With the sandbox on, these still go to
    the judge. A line the parser cannot read counts as outward (fail closed)."""
    if parsed.parse_failed or parsed.has_substitution:
        return True
    for segment in parsed.segments:
        words = _command_words(segment)
        name = _name(words)
        if name == "git":
            if _git_pushes(words):
                return True
        elif name == "gh":
            if _gh_writes(_subcommand(words, _GH_OPTIONS_WITH_VALUE)):
                return True
        elif _publishes(words) or _runs_git_push(words):
            return True
    return False


def is_safe(parsed: ParsedCommand, *, workspace: Path, cwd: str = "") -> bool:
    """Read-only commands on workspace paths. Test runners, linters, type checkers and
    builds are deliberately absent: they execute workspace code an injected edit could
    have changed (spec §10)."""
    if parsed.parse_failed or parsed.has_substitution or parsed.redirect_targets:
        return False
    if parsed.assignments:
        # NAME=value prefixes (GIT_EXTERNAL_DIFF, LD_PRELOAD, PATH, RIPGREP_CONFIG_PATH…)
        # make "read-only" commands run other programs.
        return False
    if not parsed.segments:
        return False
    return all(_segment_is_safe(s, workspace, cwd) for s in parsed.segments)
