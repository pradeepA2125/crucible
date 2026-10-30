"""What one sandboxed command may write, may not write, and may not read.

The platform wrappers (Seatbelt, bubblewrap) turn a SandboxPolicy into OS rules. A
writable path nested inside a read-only one wins (it is the more specific rule): the
task path's shadow lives under the otherwise read-only `.crucible/state`.

Protecting a file is not enough when its folder can be renamed aside, rewritten and renamed
back, or planted as a symlink: every ancestor of a protected path is pinned too. `.git` is an
allowlist, not a list of bad files: git run outside the sandbox (VS Code runs it unprompted)
executes `hooks/`, `core.fsmonitor` from `config`, and follows `commondir`, so a new control
file must be blocked by default.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agentd.approvals.auto_rules import ALWAYS_ASK_IN_WORKSPACE

# Package and build caches outside the workspace, so installs and builds work. Never their
# bin directories: those are on PATH and would let a command plant a program run later.
CACHE_DIRS = (
    ".npm", ".cache", "Library/Caches", ".cargo/registry", ".cargo/git", "go/pkg/mod",
    ".gradle/caches", ".m2/repository", ".pnpm-store", ".bun/install/cache", ".local/share/uv",
)
# Credential stores. The rest of ~/.crucible stays readable: commands run tools from
# ~/.crucible/runtime/bin.
UNREADABLE_IN_HOME = (
    ".ssh", ".aws", ".gnupg", ".config/gcloud", ".azure", ".kube", ".docker/config.json",
    ".netrc", ".git-credentials", ".config/gh", ".crucible/run", ".crucible/auth",
    ".crucible/trust.json", ".claude", "Library/Keychains",
)
# Shell startup files and git config that something unsandboxed would run or read later.
STARTUP_FILES = (".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile",
                 ".zshenv", ".zprofile", ".zshrc", ".zlogin", ".zlogout", ".gitconfig")
# `approved-*.json` cannot be a path rule: these are the stores that exist today.
APPROVAL_FILES = (
    "approved-commands.json", "approved-mcp-tools.json", "approved-web-fetch.json",
    "approved-web-search.json",
)
# What git writes in everyday work, by where it lives in a linked worktree. Everything else
# in a git directory stays read-only, including control files git may add later.
GIT_WORKTREE_DIRS = ("logs", "refs", "rebase-merge", "rebase-apply", "sequencer", "rr-cache")
GIT_WORKTREE_FILES = (
    r"(index|HEAD|ORIG_HEAD|FETCH_HEAD|MERGE_HEAD|MERGE_MSG|MERGE_MODE|MERGE_RR|SQUASH_MSG"
    r"|COMMIT_EDITMSG|TAG_EDITMSG|NOTES_EDITMSG|CHERRY_PICK_HEAD|REVERT_HEAD|REBASE_HEAD"
    r"|AUTO_MERGE|BISECT_[A-Z_]+)(\.lock)?"
    r"|next-index-[0-9]+\.lock|index\.stash\.[0-9]+(\.lock)?|sharedindex\.[0-9a-f]+"
)
GIT_SHARED_DIRS = ("objects", "refs", "logs", "lfs")
GIT_SHARED_FILES = r"(packed-refs|shallow|gc\.pid|gc\.log)(\.lock|\.new)?"
_TEMP_DIRS = ("/tmp", "/private/tmp", "/private/var/folders")


@dataclass(frozen=True)
class SandboxPolicy:
    workspace: Path
    writable: tuple[Path, ...]
    read_only: tuple[Path, ...]
    unreadable: tuple[Path, ...]
    proxy_port: int | None
    proxy_socket: Path | None
    # Each path itself, not its contents: it cannot be renamed, replaced or created.
    pinned: tuple[Path, ...] = ()
    # This checkout's git directory and the one holding shared objects and refs. The same
    # folder in an ordinary repository; apart in a linked worktree.
    git_dir: Path | None = None
    git_common_dir: Path | None = None


def _canonical(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _read_only_in(workspace: Path) -> list[Path]:
    """The always-ask list as concrete paths. A pattern ending in `/*` protects the whole
    directory; a literal protects that file even before it exists; another glob covers
    the files that match now (plus the known approval stores)."""
    paths: list[Path] = []
    for pattern in ALWAYS_ASK_IN_WORKSPACE:
        head = pattern[:-2] if pattern.endswith("/*") else pattern
        if "*" not in head:
            paths.append(workspace / head)
        elif pattern == ".crucible/approved-*.json":
            paths.extend(workspace / ".crucible" / name for name in APPROVAL_FILES)
            paths.extend(workspace.glob(pattern))
        else:
            paths.extend(workspace.glob(pattern))
    paths.extend(workspace / name for name in STARTUP_FILES)
    return paths


def _git_dirs(root: Path) -> tuple[Path | None, Path | None]:
    """The real git directories. A linked worktree's `.git` is a file naming its directory;
    one that doesn't look like a real worktree of a real repository opens nothing."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git, dot_git
    try:
        pointer = dot_git.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not pointer.startswith("gitdir:"):
        return None, None
    git_dir = _canonical(root / pointer.removeprefix("gitdir:").strip())
    common = git_dir.parent.parent
    if git_dir.parent.name != "worktrees" or not (common / "HEAD").is_file():
        return None, None
    return git_dir, common


def _pinned(paths: Sequence[Path]) -> tuple[Path, ...]:
    """Every folder above a protected path. Not canonicalized: renaming or replacing a symlink
    is checked at the link's own path."""
    return tuple(dict.fromkeys(parent for path in paths for parent in path.parents))


def _temp_dirs() -> list[Path]:
    dirs = [Path(d) for d in _TEMP_DIRS if sys.platform == "darwin" or not d.startswith("/private")]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        dirs.append(Path(tmpdir))
    return dirs


def _unique(paths: list[Path]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(_canonical(p) for p in paths))


def build_policy(*, workspace: Path, home: Path, shadow_root: Path | None,
                 extra_writable: Sequence[Path], proxy_port: int | None,
                 proxy_socket: Path | None = None) -> SandboxPolicy:
    root = _canonical(workspace)
    writable = [root, *_temp_dirs(), *(home / d for d in CACHE_DIRS), *extra_writable]
    if shadow_root is not None:
        writable.append(shadow_root)
    git_dir, git_common_dir = _git_dirs(root)
    protected = [*_read_only_in(root), root / ".git"]
    return SandboxPolicy(
        workspace=root,
        writable=_unique(writable),
        read_only=_unique([*protected, *(d for d in (git_dir, git_common_dir) if d is not None)]),
        unreadable=_unique([home / p for p in UNREADABLE_IN_HOME]),
        proxy_port=proxy_port,
        proxy_socket=proxy_socket,
        pinned=_pinned(protected),
        git_dir=git_dir,
        git_common_dir=git_common_dir,
    )
