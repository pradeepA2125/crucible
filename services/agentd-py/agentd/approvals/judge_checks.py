"""Read-only facts the Auto judge may ask for. Answers come from the system, never from
file contents, so they cannot carry injected text (spec §6.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentd.child_env import child_env
from agentd.subagents.write_log import canonical_path

_GIT_TIMEOUT_SEC = 5


def _path_info(raw: str, workspace: Path) -> str:
    candidate = Path(raw).expanduser()
    path = candidate if candidate.is_absolute() else workspace / candidate
    if path.is_symlink():
        return f"{raw}: symlink"
    if not path.exists():
        return f"{raw}: does not exist"
    if path.is_dir():
        count = sum(1 for _ in path.iterdir())
        return f"{raw}: folder, {'empty' if count == 0 else f'{count} entries'}"
    return f"{raw}: file, {path.stat().st_size} bytes"


def _git_remotes(workspace: Path) -> str:
    try:
        remotes = subprocess.run(
            ["git", "-C", str(workspace), "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
            env=child_env(),
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(workspace), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
            env=child_env(),
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"git unavailable: {exc.__class__.__name__}"
    return f"remotes:\n{remotes or '(none)'}\ncurrent branch: {branch or '(detached or none)'}"


def run_check(name: str, path: str | None, workspace: Path) -> str:
    if name == "path_info":
        return _path_info(path or ".", workspace)
    if name == "git_remotes":
        return _git_remotes(workspace)
    if name == "inside_workspace":
        inside = canonical_path(workspace, path or "") is not None
        return "inside the workspace" if inside else "outside the workspace"
    return f"unknown check {name!r}"
