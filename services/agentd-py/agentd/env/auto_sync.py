"""maybe_run_pending_install — auto-sync helper for ToolLoop.

After an emit_patch touches a manifest, the loop sets pending_install_for_scope
(via resolve_manifest_scope_key). Before the next run_command, the loop calls
this helper to run the ecosystem's install_command (e.g. 'uv sync'). Flag is
one-shot — cleared by the loop regardless of install outcome. Failure surfaces
on the run_command that follows; this helper never retries.

Optional broadcaster + broadcast_key emit env_install_{running,done} SSE events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentd.env.profile_store import EnvProfileStore


class _Broadcaster(Protocol):
    def broadcast(self, channel_id: str, event: dict) -> None: ...


async def maybe_run_pending_install(
    *,
    scope_key: str | None,
    real_workspace: Path,
    shadow_root: Path,
    broadcaster: _Broadcaster | None = None,
    broadcast_key: str | None = None,
) -> None:
    if scope_key is None:
        return

    profile = EnvProfileStore().read(real_workspace)
    if profile is None:
        return

    entry = next((e for e in profile.ecosystems if e.scope_key == scope_key), None)
    if entry is None:
        return

    if broadcaster is not None and broadcast_key is not None:
        broadcaster.broadcast(broadcast_key, {
            "type": "env_install_running",
            "payload": {"scope_key": scope_key, "command": entry.install_command},
        })

    # Late import to avoid a circular at module import time.
    from agentd.tools.env import setup_env

    result = await setup_env(
        command=entry.install_command,
        shadow_root=shadow_root,
        real_workspace=real_workspace,
        cwd=entry.subdir or None,
    )

    if broadcaster is not None and broadcast_key is not None:
        # Last ~300 chars of output is enough for the UI to surface success/failure.
        tail = result.output[-300:] if result.output else ""
        broadcaster.broadcast(broadcast_key, {
            "type": "env_install_done",
            "payload": {
                "scope_key": scope_key,
                "exit_ok": not result.is_error,
                "tail": tail,
            },
        })
