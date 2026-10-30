"""Linux: bubblewrap arguments from a SandboxPolicy.

The whole filesystem is mounted read-only, writable roots are bound back read-write, then
read-only files are bound over them and credential stores are masked. Later mounts win,
so a shadow is re-bound after the read-only `.crucible/state` that contains it. bwrap can
only mount over paths that exist: a missing read-only file can still be created (the
Seatbelt profile blocks that on macOS).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agentd.sandbox import netbridge
from agentd.sandbox.policy import SandboxPolicy


def _inside(path: Path, parents: Sequence[Path]) -> bool:
    return any(path != parent and path.is_relative_to(parent) for parent in parents)


def bwrap_argv(policy: SandboxPolicy, argv: Sequence[str], *, bwrap: str,
               python: str) -> list[str]:
    args = [bwrap, "--die-with-parent", "--unshare-net", "--unshare-pid", "--new-session",
            "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for path in policy.writable:
        if path.exists():
            args += ["--bind", str(path), str(path)]
    for path in policy.read_only:
        if path.exists():
            args += ["--ro-bind", str(path), str(path)]
    for path in policy.writable:
        if path.exists() and _inside(path, policy.read_only):
            args += ["--bind", str(path), str(path)]
    for path in policy.unreadable:
        if path.is_dir():
            args += ["--tmpfs", str(path)]
        elif path.exists():
            args += ["--ro-bind", "/dev/null", str(path)]
    command = list(argv)
    if policy.proxy_socket is not None and policy.proxy_port is not None:
        sock = str(policy.proxy_socket)
        args += ["--bind", sock, sock]
        command = [python, netbridge.__file__, sock, str(policy.proxy_port), "--", *command]
    return [*args, "--", *command]
