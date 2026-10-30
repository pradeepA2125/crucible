"""One sandboxed launch: the policy, the wrapped argv, the environment and its proxy lease."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from agentd.sandbox.env import sandbox_env
from agentd.sandbox.policy import SandboxPolicy
from agentd.sandbox.proxy import ProxyLease


@dataclass
class SandboxLaunch:
    policy: SandboxPolicy
    lease: ProxyLease | None
    _wrap: Callable[[list[str]], list[str]]
    _release: Callable[[SandboxLaunch], None]
    _released: bool = field(default=False, init=False)

    def wrap(self, argv: list[str]) -> list[str]:
        return self._wrap(argv)

    def env(self, base: Mapping[str, str]) -> dict[str, str]:
        """The caller's environment (venv, PYTHONPATH…) minus secrets, plus the proxy."""
        return sandbox_env(base, self.lease.url if self.lease is not None else None)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._release(self)
