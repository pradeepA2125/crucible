"""run_command tool implementation (allow-listed, sandbox-scoped)."""
from __future__ import annotations

import asyncio
import os
from asyncio.subprocess import PIPE, STDOUT
from pathlib import Path

from agentd.tools._paths import (
    prepend_pythonpath,
    resolve_workspace_bin,
    shadow_pythonpath_extras,
)
from agentd.tools.registry import ToolOutput

_MAX_OUTPUT_CHARS = 8000
_DEFAULT_TIMEOUT_SEC = 60

# Only these allow-listed tools import the workspace's Python package(s), so only they
# need the shadow's editable packages prepended to PYTHONPATH. ruff/tsc/eslint/npm/cargo
# don't import it.
#
# TODO(pradeep): this shadow-vs-installed-package redirect is Python-only. The same
# hazard — tests importing an ALREADY-INSTALLED copy of the package under edit instead
# of the shadow — exists for every language we add, but each needs its own mechanism
# (PYTHONPATH has no universal analogue): Node resolves via node_modules + symlinks,
# Rust/cargo via the target dir + path/patch overrides in Cargo.toml, Go via the module
# cache + replace directives. Needs deeper design before we support those toolchains.
_PY_IMPORT_TOOLS = {"pytest", "mypy", "python", "python3"}


def _split_command(
    command: str, args: list[str], real_workspace: Path
) -> tuple[str, list[str]]:
    """Recover (executable, args) when the model packs a whole command line into
    `command` (e.g. "uv run pytest tests/x.py -x").

    run_command execs directly (no shell), so a space-containing `command` is
    looked up as one binary and fails. We can't naively split because the workspace
    path itself may contain a space (".../AI editor/..."): take the LONGEST leading
    token-run that resolves to an existing file as the executable; otherwise fall
    back to the first token (covers PATH binaries like "uv"). No-op when `command`
    has no whitespace or is already a real path.
    """
    if not command or (" " not in command and "\t" not in command):
        return command, args
    tokens = command.split(" ")
    for i in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:i])
        if Path(candidate).is_file() or (real_workspace / candidate).is_file():
            return candidate, [*tokens[i:], *args]
    return tokens[0], [*tokens[1:], *args]


def _resolve_workspace_cwd(shadow_root: Path, cwd: str | None) -> Path:
    """Resolve an agent-supplied cwd to an absolute path INSIDE shadow_root.

    Empty/None → shadow_root. Relative → joined under shadow_root.
    Absolute paths and paths that escape shadow_root (`..` traversal,
    foreign absolute roots) are clamped back to shadow_root."""
    if not cwd:
        return shadow_root
    target = (shadow_root / cwd).resolve() if not Path(cwd).is_absolute() else Path(cwd).resolve()
    try:
        target.relative_to(shadow_root.resolve())
    except ValueError:
        return shadow_root
    return target if target.is_dir() else shadow_root


async def run_command(
    *,
    command: str,
    args: list[str],
    shadow_root: Path,
    real_workspace_path: Path,
    cwd: str | None = None,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
    binary_name_override: str | None = None,
) -> ToolOutput:
    if not command:
        return ToolOutput(output="Error: command is required", is_error=True)

    # Recover from the model packing the whole command line into `command`
    # (e.g. "uv run pytest …"): exec does no word-splitting, so split it back into
    # an executable + args before resolution. No-op for a clean single binary/path.
    command, args = _split_command(command, args, real_workspace_path)

    # Gating happens upstream in ToolRegistry via the command_approval_callback
    # (or is bypassed in allow_all/test paths). shell.run_command no longer
    # enforces a static allowlist — that mechanism was replaced by the approval gate.
    check_name = binary_name_override or command  # noqa: F841 — kept for log clarity
    # Binary resolution — always against real_workspace_path, never shadow.
    # setup_env installs binaries into the real workspace; the shadow has no .venv.
    # CWD stays shadow_root so patched files (pyproject.toml, pytest.ini, tests)
    # are what the binary runs against.
    cmd_path = Path(command)
    if not cmd_path.is_absolute():
        if "/" not in command and "\\" not in command:
            # Naked name (e.g. "pytest") — probe real workspace bin dirs.
            local = resolve_workspace_bin(real_workspace_path, command)
            if local is not None:
                command = str(local)
        else:
            # Relative path with separator (e.g. ".venv/bin/pytest") —
            # resolve against real workspace, not shadow CWD.
            resolved = real_workspace_path / cmd_path
            if resolved.is_file():
                command = str(resolved)

    # Make the shadow's edited source win over any installed copy of the same package, so
    # pytest/mypy import the patched files under test rather than the installed copy.
    # PYTHONPATH wins because Python's PathFinder is consulted before setuptools' appended
    # editable finder. See shadow_pythonpath_extras for the two redirects.
    env = prepend_pythonpath(
        os.environ.copy(),
        shadow_pythonpath_extras(
            shadow_root,
            real_workspace_path,
            include_editable=check_name in _PY_IMPORT_TOOLS,
        ),
    )

    # Point uv/python at the venv setup_env populates (real_workspace/<cwd>/.venv),
    # mirroring setup_env's install_root. CWD is the shadow, so without this `uv run`
    # would default to a shadow-local .venv — create an empty one missing the dev
    # extra and fail to spawn pytest. Also overrides any inherited VIRTUAL_ENV (the
    # backend runs inside its own venv, which os.environ.copy() would otherwise leak).
    _workspace_venv = real_workspace_path / (cwd or "") / ".venv"
    env["UV_PROJECT_ENVIRONMENT"] = str(_workspace_venv)
    env["VIRTUAL_ENV"] = str(_workspace_venv)

    resolved_cwd = _resolve_workspace_cwd(shadow_root, cwd)
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=str(resolved_cwd),
            env=env,
            stdout=PIPE,
            stderr=STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        return ToolOutput(
            output=f"Error: command '{command}' timed out after {timeout_sec}s",
            is_error=True,
        )
    except FileNotFoundError:
        return ToolOutput(
            output=f"Error: '{command}' not found on PATH",
            is_error=True,
        )
    except Exception as exc:
        return ToolOutput(output=f"Error running '{command}': {exc}", is_error=True)

    output = stdout.decode("utf-8", errors="replace")
    exit_code = proc.returncode or 0
    header = f"$ {command} {' '.join(args)}\n(exit code: {exit_code})\n"
    full = header + output

    if len(full) > _MAX_OUTPUT_CHARS:
        # Keep the tail (more useful for error messages)
        keep = _MAX_OUTPUT_CHARS - len(header) - 100
        full = header + f"... (output truncated, showing last {keep} chars)\n" + output[-keep:]

    is_error = exit_code != 0
    return ToolOutput(output=full, is_error=is_error)
