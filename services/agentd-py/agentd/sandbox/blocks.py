"""Telling the agent that the sandbox, not the command, stopped it.

A blocked command fails like any other (EPERM on macOS, EROFS on Linux, a refused proxy
request), and agents retry blocked commands unless the result names the sandbox (Cursor's
finding). Detection is deliberately narrow: "Permission denied" (EACCES) is ordinary file
permissions, never the sandbox, and an ordinary error is never relabelled.
"""
from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agentd.sandbox.policy import SandboxPolicy

if TYPE_CHECKING:
    from agentd.sandbox.launch import SandboxLaunch
    from agentd.tools.registry import ToolOutput

BlockKind = Literal["write", "read", "network", "direct_network"]

_DENIED = r"(?:Operation not permitted|Read-only file system)"
# Go tools print the system error in lower case. Unquoted absolute paths may hold spaces
# ("AI editor") and must start a word, or ".git/config" reads as "/config". Unquoted relative
# paths are one word, so "could not write file x: ..." yields "x", not the whole phrase.
_PATH_PATTERNS = (
    re.compile(rf"'(?P<path>[^'\n]+)': {_DENIED}", re.IGNORECASE),
    re.compile(rf"{_DENIED}: '(?P<path>[^'\n]+)'", re.IGNORECASE),
    re.compile(rf"(?<![^\s:'\"])(?P<path>/[^:'\"\n]+): {_DENIED}", re.IGNORECASE),
    re.compile(rf"(?<!\S)(?P<path>[^\s:'\"/][^\s:'\"]*): {_DENIED}", re.IGNORECASE),
    # Node: `EPERM: operation not permitted, open '/x'`. Ruby: `... @ rb_sysopen - /x`.
    re.compile(r"E(?:PERM|ROFS): [^,\n]+, \w+ '(?P<path>[^'\n]+)'"),
    re.compile(rf"{_DENIED} @ \w+ - (?P<path>/[^\n]+)", re.IGNORECASE),
)
_MISSING_FILE = re.compile(r"(?P<path>/[^:'\"\n]+): No such file or directory")
_DIRECT_NETWORK = (
    re.compile(r"connect to host \S+ port \d+: Operation not permitted"),
    re.compile(r"Could not resolve host: "),
    # Only the proxy can resolve names inside the wall, so a tool that looks one up itself
    # (a git push over SSH, a database driver) fails with one of these.
    re.compile(r"Could not resolve hostname "),
    re.compile(r"nodename nor servname provided"),
    re.compile(r"Temporary failure in name resolution"),
    re.compile(r"Name or service not known"),
    re.compile(r"getaddrinfo (?:ENOTFOUND|EAI_AGAIN)"),
    re.compile(r"lookup \S+.*: no such host"),
    # Go's dial errors, libpq, and Linux's network namespace, which has no route out.
    re.compile(r"connect: operation not permitted"),
    re.compile(r"connection to server at .* failed: Operation not permitted"),
    re.compile(r"network is unreachable", re.IGNORECASE),
    re.compile(r"connect EPERM "),
    re.compile(r"sendto: Operation not permitted"),
)
_FAILED_CONNECT = re.compile(r"Failed to connect to (?P<host>\S+) port \d+")
_SOCKET_EPERM = re.compile(r"PermissionError: \[Errno 1\] Operation not permitted\s*$",
                           re.MULTILINE)
_LOCAL_HOSTS = ("localhost", "127.", "::1", "[::1]", "0.0.0.0")


@dataclass(frozen=True)
class SandboxBlock:
    kind: BlockKind
    target: str


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _protected_write(raw: str, policy: SandboxPolicy) -> bool:
    return _inside(Path(os.path.realpath(policy.workspace / raw)), policy.read_only)


def _path_block(raw: str, policy: SandboxPolicy) -> SandboxBlock | None:
    # Relative to the workspace, where commands start; from a subfolder this lands inside the
    # workspace at worst, which reads as an ordinary error rather than a false block.
    path = Path(os.path.realpath(policy.workspace / raw))
    if _inside(path, policy.unreadable):
        return SandboxBlock("read", raw)
    reopened = [w for w in policy.writable
                if _inside(w, policy.read_only) and w not in policy.read_only]
    if _inside(path, policy.read_only) and not _inside(path, reopened):
        return SandboxBlock("write", raw)
    if not _inside(path, policy.writable):
        return SandboxBlock("write", raw)
    return None


def _direct_network(output: str) -> bool:
    if any(p.search(output) for p in _DIRECT_NETWORK):
        return True
    match = _FAILED_CONNECT.search(output)
    if match and not match["host"].startswith(_LOCAL_HOSTS):
        return True
    return bool(_SOCKET_EPERM.search(output)) and ("connect" in output or "socket" in output)


def _masked_read(output: str, policy: SandboxPolicy) -> SandboxBlock | None:
    for match in _MISSING_FILE.finditer(output):
        raw = match["path"]
        if os.path.isabs(raw) and _inside(Path(os.path.realpath(raw)), policy.unreadable):
            return SandboxBlock("read", raw)
    return None


def classify_block(output: str, exit_code: int | None, policy: SandboxPolicy,
                   refused_hosts: Sequence[str]) -> SandboxBlock | None:
    """A refused host counts whatever the exit code: `curl http://x` exits 0 on the proxy's
    403. So does a denied write to a protected file: `git checkout` exits 0 and leaves it
    stale. Anything else in the output of a command that succeeded may be text about
    something else (a grep hit, a test's captured log), not evidence of a block."""
    if refused_hosts:
        return SandboxBlock("network", refused_hosts[0])
    for pattern in _PATH_PATTERNS:
        for match in pattern.finditer(output):
            if exit_code == 0 and not _protected_write(match["path"], policy):
                continue
            block = _path_block(match["path"], policy)
            if block is not None:
                return block
    if exit_code == 0:
        return None
    masked = _masked_read(output, policy)
    if masked is not None:
        return masked
    if _direct_network(output):
        return SandboxBlock("direct_network", "")
    return None


def annotate_block(output: ToolOutput, launch: SandboxLaunch, tool: str,
                   text: str | None = None, *, refused_from: int = 0) -> bool:
    """Prepend the annotation to a tool result the sandbox blocked; True when it did.
    `text` and `refused_from` let a terminal session check only its new output and the
    hosts refused since its last read."""
    refused = launch.lease.refused[refused_from:] if launch.lease is not None else []
    block = classify_block(output.output if text is None else text, output.exit_code,
                           launch.policy, refused)
    if block is None:
        return False
    # A background call to a blocked host often doesn't matter; the agent can tell.
    succeeded = " The command itself exited 0." if output.exit_code == 0 else ""
    output.output = f"{annotate(block, tool)}{succeeded}\n\n{output.output}"
    output.status = "sandbox_blocked"
    return True


def sandbox_marker(output: ToolOutput) -> Literal["blocked", "outside"] | None:
    """What the tool pill shows about this result; ordinary sandboxed runs show nothing."""
    if getattr(output, "status", None) == "sandbox_blocked":
        return "blocked"
    return "outside" if getattr(output, "outside_sandbox", False) else None


def annotate(block: SandboxBlock, tool: str) -> str:
    outside = (f"To run this command outside the sandbox, call {tool} again with "
               "outside_sandbox: true and a short reason.")
    if block.kind == "write":
        return (f"Blocked by the sandbox: write to {block.target} is outside the allowed "
                f"folders. {outside}")
    if block.kind == "read":
        return f"Blocked by the sandbox: reading {block.target} is not allowed. {outside}"
    if block.kind == "network":
        return (f"Blocked by the sandbox: network access to {block.target} is not allowed. "
                f'To allow this host, call {tool} again with allow_hosts: ["{block.target}"] '
                "and a short reason.")
    return ("Blocked by the sandbox: this command connected to the network directly instead "
            f"of through the sandbox's proxy. To run it outside the sandbox, call {tool} again "
            "with outside_sandbox: true and a short reason.")
