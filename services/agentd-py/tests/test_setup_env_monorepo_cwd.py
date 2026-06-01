"""Tests confirming setup_env's install root respects cwd (monorepo case).

Pre-fix bug: UV_PROJECT_ENVIRONMENT / npm_config_prefix / --modules-dir all
hardcoded real_workspace, so a monorepo subdir's install would land at the
workspace root rather than co-located with the manifest. These tests pin the
fixed behaviour.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agentd.tools.env import setup_env


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


@pytest.mark.asyncio
async def test_uv_install_root_follows_cwd(monkeypatch, tmp_path: Path):
    """UV_PROJECT_ENVIRONMENT must point at <real_workspace>/<cwd>/.venv."""
    captured = {}

    async def fake_exec(*args, cwd=None, env=None, **kwargs):
        captured["env"] = env
        return _FakeProc(b"uv ok\n", returncode=0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    shadow = tmp_path / "shadow"
    real = tmp_path / "real"
    (real / "services" / "agentd-py").mkdir(parents=True)
    shadow.mkdir()
    (shadow / "services" / "agentd-py").mkdir(parents=True)

    out = await setup_env(
        command="uv sync",
        shadow_root=shadow,
        real_workspace=real,
        cwd="services/agentd-py",
    )
    assert not out.is_error
    assert captured["env"]["UV_PROJECT_ENVIRONMENT"] == str(
        real / "services/agentd-py/.venv"
    )


@pytest.mark.asyncio
async def test_uv_install_root_at_workspace_root_when_no_cwd(monkeypatch, tmp_path: Path):
    """Backwards compatibility: cwd omitted → install root = real workspace."""
    captured = {}

    async def fake_exec(*args, cwd=None, env=None, **kwargs):
        captured["env"] = env
        return _FakeProc(b"ok", 0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    real = tmp_path / "real"
    real.mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()

    await setup_env(command="uv sync", shadow_root=shadow, real_workspace=real)

    assert captured["env"]["UV_PROJECT_ENVIRONMENT"] == str(real / ".venv")


@pytest.mark.asyncio
async def test_npm_prefix_follows_cwd(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_exec(*args, cwd=None, env=None, **kwargs):
        captured["env"] = env
        return _FakeProc(b"npm ok", 0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    real = tmp_path / "real"
    (real / "apps" / "editor-client").mkdir(parents=True)
    shadow = tmp_path / "shadow"
    (shadow / "apps" / "editor-client").mkdir(parents=True)

    await setup_env(
        command="npm ci",
        shadow_root=shadow,
        real_workspace=real,
        cwd="apps/editor-client",
    )
    assert captured["env"]["npm_config_prefix"] == str(real / "apps/editor-client")


@pytest.mark.asyncio
async def test_yarn_modules_dir_follows_cwd(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_exec(*args, cwd=None, env=None, **kwargs):
        captured["argv"] = list(args)
        return _FakeProc(b"yarn ok", 0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    real = tmp_path / "real"
    (real / "pkg-a").mkdir(parents=True)
    shadow = tmp_path / "shadow"
    (shadow / "pkg-a").mkdir(parents=True)

    await setup_env(
        command="yarn install --frozen-lockfile",
        shadow_root=shadow,
        real_workspace=real,
        cwd="pkg-a",
    )
    argv = captured["argv"]
    assert "--modules-dir" in argv
    idx = argv.index("--modules-dir")
    assert argv[idx + 1] == str(real / "pkg-a" / "node_modules")
