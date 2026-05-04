"""Environment setup and binary discovery tools."""
from __future__ import annotations

import asyncio
import os
from asyncio.subprocess import PIPE, STDOUT
from pathlib import Path

from agentd.tools.registry import ToolOutput

_MAX_OUTPUT_CHARS = 4000


async def find_binary(*, name: str, real_workspace: Path) -> ToolOutput:
    """Locate an executable binary on system PATH and within the real workspace.

    Runs `which {name}` then `find {real_workspace} -name {name} -maxdepth 6 -type f`.
    Returns all found paths ranked shallowest first, or a "not found" message.
    Not sandboxed to shadow — intentionally searches real filesystem.
    """
    if not name or "/" in name:
        return ToolOutput(
            output="Error: binary name must not contain path separators", is_error=True
        )

    found: list[str] = []

    # 1. System PATH lookup
    which_path = await _run_silent("which", name)
    if which_path:
        found.append(which_path.strip())

    # 2. Workspace-local search (covers .venv, venv, node_modules, etc.)
    try:
        proc = await asyncio.create_subprocess_exec(
            "find", str(real_workspace),
            "-name", name,
            "-maxdepth", "6",
            "-type", "f",
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and line not in found:
                found.append(line)
    except (TimeoutError, FileNotFoundError):
        pass

    if not found:
        return ToolOutput(
            output=f"not found: no '{name}' binary on PATH or in {real_workspace}",
            is_error=False,
        )

    # Sort by path depth (shallowest = most local first)
    found.sort(key=lambda p: p.count(os.sep))
    lines = [f"found: {p}" for p in found]
    return ToolOutput(output="\n".join(lines))


async def _run_silent(command: str, *args: str) -> str | None:
    """Run a command, return stdout stripped, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            command, *args,
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
    except (TimeoutError, FileNotFoundError):
        pass
    return None


_SETUP_ENV_BINARIES = {"uv", "pip3", "pip", "npm", "yarn", "pnpm", "cargo", "go", "poetry"}
_SETUP_ENV_TIMEOUT_SEC = 300


async def setup_env(
    *,
    command: str,
    shadow_root: Path,
    real_workspace: Path,
    timeout_sec: int = _SETUP_ENV_TIMEOUT_SEC,
) -> ToolOutput:
    """Run an env setup command in shadow_root (reads patched dep files),
    installing binaries permanently to real_workspace.

    shadow_root is cwd so the package manager reads YOUR patched pyproject.toml
    / package.json / requirements.txt. Real-workspace targeting is achieved via
    env vars or explicit path args per package manager.
    """
    parts = command.strip().split()
    if not parts:
        return ToolOutput(output="Error: command is required", is_error=True)

    binary = parts[0]
    if binary not in _SETUP_ENV_BINARIES:
        return ToolOutput(
            output=(
                f"Error: '{binary}' not allowed for setup_env. "
                f"Allowed: {', '.join(sorted(_SETUP_ENV_BINARIES))}"
            ),
            is_error=True,
        )

    env = os.environ.copy()
    cmd_parts = list(parts)

    if binary == "uv":
        env["UV_PROJECT_ENVIRONMENT"] = str(real_workspace / ".venv")

    elif binary in ("pip3", "pip"):
        real_pip = real_workspace / ".venv" / "bin" / binary
        if real_pip.exists():
            cmd_parts[0] = str(real_pip)

    elif binary == "npm":
        env["npm_config_prefix"] = str(real_workspace)

    elif binary == "yarn":
        if "--modules-dir" not in command:
            cmd_parts += ["--modules-dir", str(real_workspace / "node_modules")]

    elif binary == "pnpm":
        if "--modules-dir" not in command:
            cmd_parts += ["--modules-dir", str(real_workspace / "node_modules")]

    # cargo/go/poetry: cwd=shadow_root is sufficient; they use global caches

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            cwd=str(shadow_root),
            stdout=PIPE,
            stderr=STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        return ToolOutput(
            output=f"Error: setup_env '{command}' timed out after {timeout_sec}s",
            is_error=True,
        )
    except FileNotFoundError:
        return ToolOutput(
            output=f"Error: '{binary}' not found on PATH",
            is_error=True,
        )
    except Exception as exc:
        return ToolOutput(output=f"Error running setup_env '{command}': {exc}", is_error=True)

    output = stdout.decode("utf-8", errors="replace")
    exit_code = proc.returncode or 0
    header = f"$ {command}\n(exit code: {exit_code})\n"
    full = header + output
    if len(full) > _MAX_OUTPUT_CHARS:
        keep = _MAX_OUTPUT_CHARS - len(header) - 80
        full = header + "...(truncated)...\n" + output[-keep:]
    return ToolOutput(output=full, is_error=exit_code != 0)
