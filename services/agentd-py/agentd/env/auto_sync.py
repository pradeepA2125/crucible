"""maybe_run_pending_install — auto-sync helper for ToolLoop.

After an emit_patch touches a manifest, the loop sets pending_install_for_scope
(via resolve_manifest_scope_key). Before the next run_command, the loop calls
this helper to run the ecosystem's install_command (e.g. 'uv sync'). Flag is
one-shot — cleared by the loop regardless of install outcome. Failure surfaces
on the run_command that follows; this helper never retries.

Returns the list of lockfiles that the install modified in the shadow (as
workspace-relative paths). The loop folds these into all_touched_files so
the user sees the lockfile update in the diff and the promote step includes
it. Lockfile drift after `uv sync` etc. is otherwise invisible to the
patch-tracking machinery (E2 fix).

Optional broadcaster + broadcast_key emit env_install_{running,done} SSE events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentd.env.profile_store import EnvProfileStore


class _Broadcaster(Protocol):
    def broadcast(self, channel_id: str, event: dict) -> None: ...


_LOCKFILE_CANDIDATES = (
    "uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Cargo.lock", "go.sum",
)


def _snapshot_mtimes(root: Path, names: tuple[str, ...]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name in names:
        p = root / name
        try:
            out[name] = p.stat().st_mtime if p.is_file() else None
        except OSError:
            out[name] = None
    return out


async def maybe_run_pending_install(
    *,
    scope_key: str | None,
    real_workspace: Path,
    shadow_root: Path,
    broadcaster: _Broadcaster | None = None,
    broadcast_key: str | None = None,
) -> list[str]:
    if scope_key is None:
        return []

    profile = EnvProfileStore().read(real_workspace)
    if profile is None:
        return []

    entry = next((e for e in profile.ecosystems if e.scope_key == scope_key), None)
    if entry is None:
        return []

    if broadcaster is not None and broadcast_key is not None:
        broadcaster.broadcast(broadcast_key, {
            "type": "env_install_running",
            "payload": {"scope_key": scope_key, "command": entry.install_command},
        })

    # Snapshot lockfile mtimes before — `uv sync`, `npm ci`, etc. may rewrite
    # the lockfile to match the manifest the agent just patched. Promote step
    # needs to see those changes.
    lockfile_dir = shadow_root / entry.subdir if entry.subdir else shadow_root
    before = _snapshot_mtimes(lockfile_dir, _LOCKFILE_CANDIDATES)

    # Late import to avoid a circular at module import time.
    from agentd.tools.env import setup_env

    result = await setup_env(
        command=entry.install_command,
        shadow_root=shadow_root,
        real_workspace=real_workspace,
        cwd=entry.subdir or None,
    )

    after = _snapshot_mtimes(lockfile_dir, _LOCKFILE_CANDIDATES)
    changed_lockfiles: list[str] = []
    for name in _LOCKFILE_CANDIDATES:
        if after[name] is not None and before.get(name) != after[name]:
            changed_lockfiles.append(
                f"{entry.subdir}/{name}" if entry.subdir else name
            )

    if broadcaster is not None and broadcast_key is not None:
        tail = result.output[-300:] if result.output else ""
        broadcaster.broadcast(broadcast_key, {
            "type": "env_install_done",
            "payload": {
                "scope_key": scope_key,
                "exit_ok": not result.is_error,
                "tail": tail,
                "lockfiles_touched": changed_lockfiles,
            },
        })

    return changed_lockfiles
