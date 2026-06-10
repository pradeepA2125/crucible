"""Tests for ToolRegistry phase-gated definitions and basename allowlist."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.tools.registry import ToolRegistry


def test_explore_phase_omits_env_tools(tmp_path: Path) -> None:
    registry = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {t.name for t in registry.definitions(phase="explore")}
    assert "search_code" in names
    assert "read_file" in names
    assert "list_directory" in names
    assert "setup_env" not in names
    assert "find_binary" not in names


def test_verify_phase_includes_env_tools(tmp_path: Path) -> None:
    registry = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {t.name for t in registry.definitions(phase="verify")}
    assert "setup_env" in names
    assert "find_binary" in names
    assert "init_workspace" in names


def test_init_workspace_dispatch_creates_pyproject(tmp_path: Path) -> None:
    registry = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    result = asyncio.run(
        registry.execute(
            "init_workspace", {"ecosystem": "python", "dev_deps": ["pytest"]}
        )
    )
    assert not result.is_error, result.output
    assert (tmp_path / "pyproject.toml").exists()


@pytest.mark.asyncio
async def test_verify_reads_fall_back_to_workspace_for_missing_shadow_path(tmp_path: Path) -> None:
    """In verify phase reads target the shadow, but env/build artifacts (.venv,
    node_modules, target/) live only in the workspace. A path absent in the
    shadow must read-through to the workspace instead of erroring."""
    shadow = tmp_path / "shadow"
    (shadow / "src").mkdir(parents=True)
    real = tmp_path / "real"
    venv_bin = real / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n")

    registry = ToolRegistry(shadow_root=shadow, real_workspace_path=real)
    registry.use_shadow_for_reads()

    result = await registry.execute("list_directory", {"path": ".venv/bin"})
    assert not result.is_error, result.output
    assert "python" in result.output


@pytest.mark.asyncio
async def test_verify_reads_prefer_shadow_when_path_exists(tmp_path: Path) -> None:
    """Read-through must NOT mask edited source: when a path exists in the shadow,
    the shadow copy wins over a stale workspace copy."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (shadow / "foo.py").write_text("SHADOW VERSION\n")
    (real / "foo.py").write_text("WORKSPACE VERSION\n")

    registry = ToolRegistry(shadow_root=shadow, real_workspace_path=real)
    registry.use_shadow_for_reads()

    result = await registry.execute("read_file", {"path": "foo.py"})
    assert not result.is_error, result.output
    assert "SHADOW VERSION" in result.output
    assert "WORKSPACE VERSION" not in result.output


def test_run_command_allows_full_path(tmp_path: Path) -> None:
    """Basename of a full path must pass the allowlist check."""
    registry = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    fake = tmp_path / "pytest"
    fake.write_text("#!/bin/sh\necho ok")
    fake.chmod(0o755)

    result = asyncio.run(
        registry.execute("run_command", {"command": str(fake), "args": ["--version"]})
    )
    assert "not in the shell allowlist" not in result.output


@pytest.mark.asyncio
async def test_run_command_consults_approval_callback(tmp_path: Path) -> None:
    """ToolRegistry calls command_approval_callback before running; on reject
    returns a tool-result error string so the agent can adapt."""
    calls: list[tuple[str, list[str]]] = []

    from agentd.domain.models import CommandDecision

    async def cb(command: str, args: list[str], cwd: str) -> CommandDecision:
        calls.append((command, args))
        return CommandDecision(approve=False)

    registry = ToolRegistry(
        shadow_root=tmp_path,
        real_workspace_path=tmp_path,
        command_approval_callback=cb,
    )
    result = await registry.execute(
        "run_command", {"command": "python", "args": ["-c", "print(1)"]},
    )
    assert calls == [("python", ["-c", "print(1)"])]
    assert result.is_error
    assert "rejected" in result.output.lower()


@pytest.mark.asyncio
async def test_run_command_no_callback_runs_unguarded(tmp_path: Path) -> None:
    """If no callback is wired (test/legacy path), run_command runs without
    gating — the static allowlist has been removed."""
    registry = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    fake = tmp_path / "ok"
    fake.write_text("#!/bin/sh\necho hi")
    fake.chmod(0o755)
    result = await registry.execute(
        "run_command", {"command": str(fake), "args": []},
    )
    assert not result.is_error
    assert "rejected" not in result.output.lower()


def test_run_command_resolves_relative_venv_path_from_real_workspace(tmp_path: Path) -> None:
    """'.venv/bin/pytest' (relative path) resolves against real workspace, not shadow CWD."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    venv_bin = real / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake = venv_bin / "pytest"
    fake.write_text("#!/bin/sh\necho FROM_REAL_RELATIVE\nexit 0\n")
    fake.chmod(0o755)

    registry = ToolRegistry(shadow_root=shadow, real_workspace_path=real)
    result = asyncio.run(
        registry.execute("run_command", {"command": ".venv/bin/pytest", "args": []})
    )
    assert "FROM_REAL_RELATIVE" in result.output, result.output
    assert not result.is_error


def test_run_command_resolves_pytest_from_real_workspace_not_shadow(tmp_path: Path) -> None:
    """Bare 'pytest' resolves from the real workspace's .venv/bin, not the shadow.

    setup_env installs binaries into the real workspace. The shadow never gets a
    .venv copy. run_command must probe real_workspace_path for binary resolution
    while keeping CWD=shadow so patched files are what the binary runs against.
    """
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    # Binary lives in real workspace only — shadow has no .venv
    venv_bin = real / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake = venv_bin / "pytest"
    fake.write_text("#!/bin/sh\necho FROM_REAL_VENV\nexit 0\n")
    fake.chmod(0o755)

    registry = ToolRegistry(shadow_root=shadow, real_workspace_path=real)
    result = asyncio.run(
        registry.execute("run_command", {"command": "pytest", "args": []})
    )
    assert "FROM_REAL_VENV" in result.output, result.output
    assert not result.is_error
