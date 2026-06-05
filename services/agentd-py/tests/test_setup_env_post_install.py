"""E1, E3 fixes in setup_env: post-install interpreter hint + cwd guard."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.tools.env import setup_env


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _make_workspace_layout(tmp_path: Path) -> tuple[Path, Path]:
    """shadow_root + real_workspace, both with services/agentd-py/ present."""
    real = tmp_path / "real"
    shadow = tmp_path / "shadow"
    (real / "services" / "agentd-py").mkdir(parents=True)
    (shadow / "services" / "agentd-py").mkdir(parents=True)
    return shadow, real


@pytest.mark.asyncio
async def test_appends_interpreter_hint_when_venv_appears(monkeypatch, tmp_path: Path):
    """E1: after a successful uv sync that creates .venv/bin/python, the
    tool output ends with 'AGENT INFO: interpreter now ready at <subdir>/.venv/bin/python'."""
    shadow, real = _make_workspace_layout(tmp_path)

    # Subprocess CREATES the venv binary the way uv would.
    async def fake_exec(*args, cwd=None, env=None, **kwargs):
        bin_dir = real / "services" / "agentd-py" / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/bin/sh\n")
        (bin_dir / "python").chmod(0o755)
        return _FakeProc(b"installed deps\n", returncode=0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    out = await setup_env(
        command="uv sync", shadow_root=shadow, real_workspace=real,
        cwd="services/agentd-py",
    )
    assert not out.is_error
    assert "AGENT INFO: interpreter now ready at services/agentd-py/.venv/bin/python" in out.output


@pytest.mark.asyncio
async def test_no_interpreter_hint_when_venv_did_not_appear(monkeypatch, tmp_path: Path):
    """If the install reported success but didn't create the binary (rare —
    maybe uv was misconfigured), no hint is appended. Don't lie to the agent."""
    shadow, real = _make_workspace_layout(tmp_path)

    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"no-op", returncode=0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    out = await setup_env(
        command="uv sync", shadow_root=shadow, real_workspace=real,
        cwd="services/agentd-py",
    )
    assert "AGENT INFO: interpreter now ready" not in out.output


@pytest.mark.asyncio
async def test_cwd_must_exist_in_shadow_or_error(tmp_path: Path):
    """E3: setup_env refuses to run when the requested cwd subdir is not
    present in the shadow. Earlier behaviour silently clamped to shadow_root
    and ran the install against the wrong (or missing) manifest."""
    real = tmp_path / "real"
    shadow = tmp_path / "shadow"
    (real / "services" / "agentd-py").mkdir(parents=True)
    shadow.mkdir()  # lightweight shadow: no services/agentd-py/

    out = await setup_env(
        command="uv sync", shadow_root=shadow, real_workspace=real,
        cwd="services/agentd-py",
    )
    assert out.is_error
    assert "not present in the shadow workspace" in out.output


@pytest.mark.asyncio
async def test_appends_node_runner_hint_when_node_modules_appears(monkeypatch, tmp_path: Path):
    """E1 for node: after npm install, surface node_modules/.bin location."""
    shadow, real = _make_workspace_layout(tmp_path)

    async def fake_exec(*args, **kwargs):
        bin_dir = real / "services" / "agentd-py" / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True)
        return _FakeProc(b"added 100 packages", returncode=0)

    async def fake_which(name: str) -> bool:
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("agentd.tools.env._which", fake_which)

    out = await setup_env(
        command="npm ci", shadow_root=shadow, real_workspace=real,
        cwd="services/agentd-py",
    )
    assert not out.is_error
    assert "AGENT INFO: runner binaries now ready under services/agentd-py/node_modules/.bin" in out.output
