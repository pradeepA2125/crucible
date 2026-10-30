"""Whether commands run sandboxed right now: the user's switch plus platform support.

The switch is the user's (approval-mode API); availability is probed once at startup by
running `true` inside the sandbox, so a missing bubblewrap or a disabled user namespace
is reported with its reason instead of failing every command.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel

from agentd.child_env import child_env
from agentd.sandbox.bubblewrap import bwrap_argv
from agentd.sandbox.launch import SandboxLaunch
from agentd.sandbox.policy import SandboxPolicy, build_policy
from agentd.sandbox.proxy import SandboxProxy
from agentd.sandbox.seatbelt import seatbelt_argv
from agentd.sandbox.store import WorkspaceSandboxStore

logger = logging.getLogger(__name__)

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
        # available=False with no reason: the check has not finished yet.
        self._reason: str | None = None
        self.store = WorkspaceSandboxStore(workspace, self.home)
        self.proxy: SandboxProxy | None = None

    def status(self) -> SandboxStatus:
        return SandboxStatus(enabled=self.enabled, available=self._available,
                             reason=self._reason)

    def active(self) -> bool:
        return self.enabled and self._available

    async def start(self) -> None:
        """Probe, then start the proxy when the sandbox can run."""
        self._clear_private_tmp()
        try:
            await self.probe()
        except Exception as exc:
            # Without a reason clients would keep reading this as "still checking".
            self._available, self._reason = False, f"sandbox failed to start: {exc}"
            raise
        if self._available and self.proxy is None:
            proxy = SandboxProxy(baseline_hosts=self.store.hosts)
            await proxy.start()
            self.proxy = proxy

    async def close(self) -> None:
        if self.proxy is not None:
            await self.proxy.close()
            self.proxy = None

    def launch_for(self, *, shadow_root: Path | None, extra_hosts: Iterable[str] = (),
                   allow_any: bool = False) -> SandboxLaunch | None:
        """Everything one command needs to run inside the wall; None when commands run
        unsandboxed (switched off, unavailable)."""
        if not self.active():
            return None
        lease = (self.proxy.lease(self.store.hosts(), extra_hosts, allow_any)
                 if self.proxy is not None else None)
        private_tmp = self._private_tmp()
        policy = build_policy(
            workspace=self.workspace, home=self.home, shadow_root=shadow_root,
            extra_writable=self.store.folders(),
            proxy_port=self.proxy.port if self.proxy is not None else None,
            proxy_socket=self.proxy.socket_path if self.proxy is not None else None,
            private_tmp=private_tmp)
        # Linux mounts the private folder at /tmp; macOS can only point TMPDIR at it.
        tmpdir = (None if private_tmp is None
                  else "/tmp" if self._linux else str(policy.private_tmp))
        return SandboxLaunch(policy=policy, lease=lease, tmpdir=tmpdir,
                             _wrap=lambda argv, interactive: self.wrap_argv(
                                 argv, policy, interactive=interactive),
                             _release=self._release_lease)

    @property
    def _linux(self) -> bool:
        return self._platform != "darwin"

    def _private_tmp_path(self) -> Path:
        # Short on macOS: Unix socket paths are limited to about 104 characters there.
        key = self.store.key
        return (self.home / ".crucible" / "sandbox" / key / "tmp" if self._linux
                else Path(f"/tmp/crucible-sbx-{os.getuid()}-{key}"))

    def _private_tmp(self) -> Path | None:
        """Ours, or none: /tmp is shared, and another user may have created this name first."""
        path = self._private_tmp_path()
        try:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = path.lstat()
        except OSError:
            logger.warning("[sandbox] no private temp folder at %s", path, exc_info=True)
            return None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            logger.warning("[sandbox] %s is not ours; using no private temp folder", path)
            return None
        return path

    def _clear_private_tmp(self) -> None:
        # Commands from a previous run are gone; one backend serves one workspace.
        path = self._private_tmp_path()
        if path.is_dir() and not path.is_symlink() and path.lstat().st_uid == os.getuid():
            shutil.rmtree(path, ignore_errors=True)

    def release(self, launch: SandboxLaunch | None) -> None:
        if launch is not None:
            launch.release()

    def _release_lease(self, launch: SandboxLaunch) -> None:
        if launch.lease is not None and self.proxy is not None:
            self.proxy.release(launch.lease)

    def wrap_argv(self, argv: Sequence[str], policy: SandboxPolicy, *,
                  interactive: bool = False) -> list[str]:
        if self._platform == "darwin":
            return seatbelt_argv(policy, argv)
        return bwrap_argv(policy, argv, bwrap=self._which("bwrap") or "bwrap",
                          python=sys.executable, interactive=interactive)

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
                                    text=True, timeout=_PROBE_TIMEOUT_SEC, env=child_env())
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
