"""macOS: a Seatbelt profile from a SandboxPolicy, run through `sandbox-exec`.

`sandbox-exec` is deprecated but still what Chrome, Codex, Cursor and Claude Code use; the
profile constrains the whole process tree. Later rules override earlier ones, which is how
read-only files carve holes in writable folders and how a shadow re-opens inside the
read-only `.crucible/state`.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agentd.sandbox.policy import SandboxPolicy

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# What ordinary developer tools need from the system: processes, signals within the
# sandbox, system info, shared memory, terminals and the standard devices.
_BASE = """(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow ipc-posix-sem)
(allow iokit-open)
(allow file-ioctl)
(allow pseudo-tty)
(allow file-read*)
(allow file-write*
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (literal "/dev/ptmx")
  (literal "/dev/dtracehelper") (regex #"^/dev/ttys[0-9]+$") (regex #"^/dev/fd/"))
(allow network-outbound (remote ip "localhost:*"))
(allow network-inbound (local ip "localhost:*"))
(allow network-bind (local ip "localhost:*"))
"""
# The Keychain is a credential store reached over Mach, not files: without this a git
# credential helper would hand the user's tokens to a sandboxed command.
_KEYCHAIN = """(deny mach-lookup
  (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd.xpc")
  (global-name "com.apple.security.agent") (global-name "com.apple.secd"))
"""


def _literal(path: Path) -> str:
    text = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _filters(paths: Sequence[Path]) -> str:
    # A path that is a directory now, or may become one, is covered with subpath; an
    # existing file only by its literal path.
    return " ".join(
        f"(literal {_literal(p)})" if p.is_file() else f"(subpath {_literal(p)})"
        for p in paths)


def _inside(path: Path, parents: Sequence[Path]) -> bool:
    return any(path != parent and path.is_relative_to(parent) for parent in parents)


def seatbelt_profile(policy: SandboxPolicy) -> str:
    rules = [_BASE, _KEYCHAIN]
    if policy.unreadable:
        rules.append(f"(deny file-read* file-write* {_filters(policy.unreadable)})\n")
    if policy.writable:
        rules.append(f"(allow file-write* {_filters(policy.writable)})\n")
    if policy.read_only:
        rules.append(f"(deny file-write* {_filters(policy.read_only)})\n")
        reopened = [p for p in policy.writable if _inside(p, policy.read_only)]
        if reopened:
            rules.append(f"(allow file-write* {_filters(reopened)})\n")
    return "".join(rules)


def seatbelt_argv(policy: SandboxPolicy, argv: Sequence[str]) -> list[str]:
    return [SANDBOX_EXEC, "-p", seatbelt_profile(policy), "--", *argv]
