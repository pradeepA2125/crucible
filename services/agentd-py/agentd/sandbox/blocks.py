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
_PATH_PATTERNS = (
    re.compile(rf"'(?P<path>/[^'\n]+)': {_DENIED}"),
    re.compile(rf"{_DENIED}: '(?P<path>/[^'\n]+)'"),
    re.compile(rf"(?P<path>/[^:'\"\n]+): {_DENIED}"),
)
_DIRECT_NETWORK = (
    re.compile(r"connect to host \S+ port \d+: Operation not permitted"),
    re.compile(r"Could not resolve host: "),
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


def _path_block(raw: str, policy: SandboxPolicy) -> SandboxBlock | None:
    path = Path(os.path.realpath(raw))
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


def classify_block(output: str, exit_code: int | None, policy: SandboxPolicy,
                   refused_hosts: Sequence[str]) -> SandboxBlock | None:
    if exit_code == 0:
        return None
    if refused_hosts:
        return SandboxBlock("network", refused_hosts[0])
    for pattern in _PATH_PATTERNS:
        for match in pattern.finditer(output):
            block = _path_block(match["path"], policy)
            if block is not None:
                return block
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
    output.output = f"{annotate(block, tool)}\n\n{output.output}"
    output.status = "sandbox_blocked"
    return True


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
