"""Linux: bubblewrap arguments from a SandboxPolicy.

The whole filesystem is mounted read-only, writable roots are bound back read-write, then
read-only files are bound over them and credential stores are masked. Later mounts win,
so a shadow is re-bound after the read-only `.crucible/state` that contains it.

bwrap can only mount over paths that exist and can't express "this file but not that one",
so the control folders are read-only whole, with an empty read-only mount when absent (which
leaves an empty folder behind on the host; git ignores those). A mount point also can't be
renamed, which is what pins them. Two things Seatbelt does that this can't: `.git` has no
allowlist for new top-level files, so committing needs an escalation here, and a missing
`AGENTS.md` at the workspace root can still be created.

`--unshare-net` doesn't isolate Unix sockets found by path, so `/run` (the Docker socket, the
user's D-Bus and systemd) is hidden and `/tmp` is this workspace's own.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agentd.sandbox import netbridge
from agentd.sandbox.policy import GIT_SHARED_DIRS, GIT_WORKTREE_DIRS, SandboxPolicy

_CONTROL_FOLDERS = (".crucible", ".claude", ".vscode", ".git")


def _inside(path: Path, parents: Sequence[Path]) -> bool:
    return any(path != parent and path.is_relative_to(parent) for parent in parents)


def _control_folder_args(policy: SandboxPolicy) -> list[str]:
    # Only a real workspace's policy pins anything; the startup probe's "/" must not get these.
    if not policy.pinned:
        return []
    args: list[str] = []
    for name in _CONTROL_FOLDERS:
        path = policy.workspace / name
        if path.exists():
            args += ["--ro-bind", str(path), str(path)]
        else:
            args += ["--tmpfs", str(path), "--remount-ro", str(path)]
    return args


def _git_args(policy: SandboxPolicy) -> list[str]:
    if policy.git_dir is None or policy.git_common_dir is None:
        return []
    # `git fetch` writes this one directly at the top level; empty is a valid state.
    (policy.git_dir / "FETCH_HEAD").touch(exist_ok=True)
    paths = [policy.git_dir / "FETCH_HEAD", *(policy.git_dir / d for d in GIT_WORKTREE_DIRS),
             *(policy.git_common_dir / d for d in GIT_SHARED_DIRS)]
    return [arg for path in dict.fromkeys(paths) if path.exists()
            for arg in ("--bind", str(path), str(path))]


def bwrap_argv(policy: SandboxPolicy, argv: Sequence[str], *, bwrap: str,
               python: str, interactive: bool = False) -> list[str]:
    # setsid would drop the controlling terminal a terminal session needs for Ctrl-C.
    session = [] if interactive else ["--new-session"]
    args = [bwrap, "--die-with-parent", "--unshare-net", "--unshare-pid", *session,
            "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/run"]
    if not Path("/var/run").is_symlink():
        args += ["--tmpfs", "/var/run"]
    if policy.private_tmp is not None:
        args += ["--bind", str(policy.private_tmp), "/tmp"]
    for path in policy.writable:
        # Home is read-only, so a cache folder that doesn't exist yet could never be made.
        path.mkdir(parents=True, exist_ok=True)
        if str(path) != "/tmp":
            args += ["--bind", str(path), str(path)]
    args += _control_folder_args(policy)
    for path in policy.read_only:
        if path.exists():
            args += ["--ro-bind", str(path), str(path)]
    for path in policy.writable:
        if path.exists() and _inside(path, policy.read_only):
            args += ["--bind", str(path), str(path)]
    args += _git_args(policy)
    for path in policy.unreadable:
        if path.is_dir():
            args += ["--tmpfs", str(path), "--remount-ro", str(path)]
        elif path.exists():
            args += ["--ro-bind", "/dev/null", str(path)]
    command = list(argv)
    if policy.proxy_socket is not None and policy.proxy_port is not None:
        sock = str(policy.proxy_socket)
        args += ["--bind", sock, sock]
        command = [python, netbridge.__file__, sock, str(policy.proxy_port), "--", *command]
    return [*args, "--", *command]
