"""Whether commands run sandboxed right now: the user's switch plus platform support.

The switch is the user's (approval-mode API); availability is probed once at startup by
running `true` inside the sandbox, so a missing bubblewrap or a disabled user namespace
is reported with its reason instead of failing every command.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel

from agentd.sandbox.bubblewrap import bwrap_argv
from agentd.sandbox.policy import SandboxPolicy
from agentd.sandbox.seatbelt import seatbelt_argv

_PROBE_TIMEOUT_SEC = 10


class SandboxStatus(BaseModel):
    enabled: bool
    available: bool
    reason: str | None


class SandboxRuntime:
    def __init__(self, workspace: Path, home: Path | None = None, *,
                 platform: str = sys.platform,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self.workspace = workspace
        self.home = home or Path.home()
        self.enabled = True
        self._platform = platform
        self._which = which
        self._available = False
        self._reason: str | None = "not checked yet"

    def status(self) -> SandboxStatus:
        return SandboxStatus(enabled=self.enabled, available=self._available,
                             reason=self._reason)

    def active(self) -> bool:
        return self.enabled and self._available

    def wrap_argv(self, argv: Sequence[str], policy: SandboxPolicy) -> list[str]:
        if self._platform == "darwin":
            return seatbelt_argv(policy, argv)
        return bwrap_argv(policy, argv, bwrap=self._which("bwrap") or "bwrap",
                          python=sys.executable)

    async def probe(self) -> None:
        self._available, self._reason = await asyncio.to_thread(self._probe)

    def _probe(self) -> tuple[bool, str | None]:
        if self._platform.startswith("win"):
            return False, "native Windows is not supported"
        if self._platform != "darwin" and self._which("bwrap") is None:
            return False, "bubblewrap is not installed"
        root = Path("/")
        policy = SandboxPolicy(workspace=root, writable=(), read_only=(), unreadable=(),
                               proxy_port=None, proxy_socket=None)
        true = "/usr/bin/true" if Path("/usr/bin/true").exists() else "/bin/true"
        try:
            result = subprocess.run(self.wrap_argv([true], policy), capture_output=True,
                                    text=True, timeout=_PROBE_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"sandbox failed to start: {exc}"
        if result.returncode == 0:
            return True, None
        first = (result.stderr.strip().splitlines() or ["exit " + str(result.returncode)])[0]
        if self._platform != "darwin":
            if "namespace" in first.lower() or "operation not permitted" in first.lower():
                return False, "user namespaces are disabled"
            return False, f"bubblewrap failed: {first}"
        return False, f"sandbox-exec failed: {first}"
