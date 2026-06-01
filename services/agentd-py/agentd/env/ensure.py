"""EnvProfileEnsurer — lazy build + freshness check + concurrent serialization.

Owned by AgentOrchestrator. Called once at task start to make sure the workspace
has a usable env_profile.json. The ensurer is workspace-keyed; concurrent first
tasks on the same workspace wait on a per-workspace asyncio lock.

SSE events (broadcast on the workspace_root channel):
- env_profile_building — fires when a build starts
- env_profile_built    — fires after the profile is written
No event when the profile is already fresh (the common case).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from agentd.env.profile_builder import EnvProfileBuilder
from agentd.env.profile_store import EnvProfileStore


class _Reasoner(Protocol):
    async def draft_conventions(self, *, probe: object) -> dict: ...


class _Broadcaster(Protocol):
    def broadcast(self, channel_id: str, event: dict) -> None: ...


class EnvProfileEnsurer:
    def __init__(
        self,
        *,
        reasoner: _Reasoner,
        broadcaster: _Broadcaster,
        store: EnvProfileStore | None = None,
    ) -> None:
        self._reasoner = reasoner
        self._broadcaster = broadcaster
        self._store = store or EnvProfileStore()
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure(self, workspace_root: Path) -> None:
        key = str(workspace_root.resolve())
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not self._store.is_stale(workspace_root):
                return

            self._broadcaster.broadcast(key, {
                "type": "env_profile_building",
                "payload": {"workspace_root": key},
            })

            builder = EnvProfileBuilder(reasoner=self._reasoner)
            profile = await builder.build(workspace_root)
            self._store.write(workspace_root, profile)

            self._broadcaster.broadcast(key, {
                "type": "env_profile_built",
                "payload": {
                    "ecosystems_count": len(profile.ecosystems),
                    "bootstrap_needed": profile.bootstrap_needed,
                },
            })
