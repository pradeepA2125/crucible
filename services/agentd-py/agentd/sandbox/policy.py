"""What one sandboxed command may write, may not write, and may not read.

The platform wrappers (Seatbelt, bubblewrap) turn a SandboxPolicy into OS rules. A
writable path nested inside a read-only one wins (it is the more specific rule): the
task path's shadow lives under the otherwise read-only `.crucible/state`.
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
STARTUP_FILES = (".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile", ".gitconfig")
# `approved-*.json` cannot be a path rule: these are the stores that exist today.
APPROVAL_FILES = (
    "approved-commands.json", "approved-mcp-tools.json", "approved-web-fetch.json",
    "approved-web-search.json", "approved-hosts.json",
)
_TEMP_DIRS = ("/tmp", "/private/tmp", "/private/var/folders")


@dataclass(frozen=True)
class SandboxPolicy:
    workspace: Path
    writable: tuple[Path, ...]
    read_only: tuple[Path, ...]
    unreadable: tuple[Path, ...]
    proxy_port: int | None
    proxy_socket: Path | None


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
    return SandboxPolicy(
        workspace=root,
        writable=_unique(writable),
        read_only=_unique(_read_only_in(root)),
        unreadable=_unique([home / p for p in UNREADABLE_IN_HOME]),
        proxy_port=proxy_port,
        proxy_socket=proxy_socket,
    )
