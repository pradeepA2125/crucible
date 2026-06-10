"""Tests for find_binary, setup_env, and list_directory tools."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.tools.env import find_binary, init_workspace, setup_env
from agentd.tools.files import list_directory
from agentd.tools.registry import ToolOutput


@pytest.mark.asyncio
async def test_find_binary_finds_python(tmp_path: Path) -> None:
    result = await find_binary(name="python3", real_workspace=tmp_path)
    assert not result.is_error
    assert "python3" in result.output


@pytest.mark.asyncio
async def test_find_binary_not_found(tmp_path: Path) -> None:
    result = await find_binary(name="__nonexistent_binary_xyz__", real_workspace=tmp_path)
    assert not result.is_error
    assert "not found" in result.output.lower()


@pytest.mark.asyncio
async def test_find_binary_emits_actionable_hint_for_known_pm_binary(tmp_path: Path) -> None:
    result = await find_binary(name="pytest", real_workspace=tmp_path)
    assert not result.is_error
    if "found:" not in result.output:
        # pytest may not be on PATH in the test env — that's the case we're testing.
        assert "AGENT SHOULD:" in result.output
        assert "uv sync --extra dev" in result.output


def test_remediation_for_missing_pytest_is_uv_aware() -> None:
    """A missing Python dev tool must steer the agent to uv (dev extra + uv pip/run)
    and warn that pip is unavailable in a uv venv — NOT the old 'emit_patch to
    declare it' trap (pytest is already declared under the dev extra)."""
    from agentd.tools.env import _remediation_for_missing_binary

    text = _remediation_for_missing_binary("pytest")
    assert "uv sync --extra dev" in text
    assert "uv pip install" in text
    assert "uv run" in text
    assert "do not work" in text.lower()  # pip-unavailable warning
    assert "emit_patch to declare" not in text


class _FakeProc:
    """Minimal asyncio subprocess stand-in for setup_env tests."""

    def __init__(self, out: bytes = b"Resolved 1 package\nInstalled 1 package", rc: int = 0) -> None:
        self._out = out
        self.returncode = rc

    async def communicate(self) -> tuple[bytes, bytes]:
        return (self._out, b"")


@pytest.mark.asyncio
async def test_setup_env_uv_appends_usage_cheatsheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After `uv sync`, the feedback must teach future uv commands (install /
    inspect / uninstall / run) so the agent doesn't reach for pip."""
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return True

    async def fake_exec(*args: object, **kwargs: object) -> _FakeProc:
        return _FakeProc()

    monkeypatch.setattr(env_module, "_which", fake_which)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="uv sync", shadow_root=shadow, real_workspace=real)
    out = result.output
    assert "uv pip install" in out
    assert "uv pip list" in out
    assert "uv pip uninstall" in out
    assert "uv run" in out
    assert "do not work" in out.lower()  # pip-unavailable warning


@pytest.mark.asyncio
async def test_find_binary_resolves_workspace_local_first(tmp_path: Path) -> None:
    """Workspace .venv/bin/<name> ranks above any system or nested hit."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake = venv_bin / "pytest"
    fake.write_text("#!/bin/sh\necho ok\n")
    fake.chmod(0o755)

    # Plant a deeper, irrelevant copy that the find sweep would also pick up.
    nested = tmp_path / "packages" / "x" / "node_modules" / ".bin"
    nested.mkdir(parents=True)
    other = nested / "pytest"
    other.write_text("#!/bin/sh\necho nope\n")
    other.chmod(0o755)

    result = await find_binary(name="pytest", real_workspace=tmp_path)
    assert not result.is_error
    lines = [line for line in result.output.splitlines() if line.startswith("found:")]
    assert lines, result.output
    assert lines[0] == f"found: {fake}", lines


@pytest.mark.asyncio
async def test_find_binary_finds_in_venv(tmp_path: Path) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_pytest = venv_bin / "pytest"
    fake_pytest.write_text("#!/bin/sh\necho ok")
    fake_pytest.chmod(0o755)

    result = await find_binary(name="pytest", real_workspace=tmp_path)
    assert not result.is_error
    assert str(fake_pytest) in result.output


@pytest.mark.asyncio
async def test_setup_env_rejects_unknown_binary(tmp_path: Path) -> None:
    result = await setup_env(
        command="rm -rf /",
        shadow_root=tmp_path,
        real_workspace=tmp_path,
    )
    assert result.is_error
    assert "not allowed" in result.output.lower()


@pytest.mark.asyncio
async def test_setup_env_rejects_empty_command(tmp_path: Path) -> None:
    result = await setup_env(
        command="",
        shadow_root=tmp_path,
        real_workspace=tmp_path,
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_setup_env_uv_uses_shadow_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When uv IS on PATH, setup_env runs it with cwd=shadow + UV_PROJECT_ENVIRONMENT."""
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return True  # uv "is" installed

    monkeypatch.setattr(env_module, "_which", fake_which)

    calls: list[dict] = []

    async def fake_exec(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        calls.append({"args": args, "env": kwargs.get("env", {}), "cwd": kwargs.get("cwd")})
        raise FileNotFoundError("uv vanished post-probe — test only checks args")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    _ = await setup_env(command="uv sync", shadow_root=shadow, real_workspace=real)
    assert calls, "create_subprocess_exec must have been called"
    call = calls[0]
    assert call["cwd"] == str(shadow)
    assert call["env"].get("UV_PROJECT_ENVIRONMENT") == str(real / ".venv")


@pytest.mark.asyncio
async def test_setup_env_uv_missing_falls_back_to_python_venv_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv missing → setup_env transparently runs `python3 -m venv` + `pip install -e <shadow>`."""
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return name != "uv"

    monkeypatch.setattr(env_module, "_which", fake_which)

    async def fake_run_silent(command: str, *args: str) -> str | None:
        if command == "which" and args and args[0] == "python3":
            return "/usr/bin/python3"
        return None

    monkeypatch.setattr(env_module, "_run_silent", fake_run_silent)

    captured: list[list[str]] = []

    async def fake_capture(cmd: list[str], *, cwd: str, timeout_sec: int) -> tuple[int, str]:
        captured.append(list(cmd))
        return 0, "ok"

    monkeypatch.setattr(env_module, "_run_capture", fake_capture)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="uv sync", shadow_root=shadow, real_workspace=real)
    assert not result.is_error, result.output
    assert "bootstrapped via python3" in result.output
    # Verify subprocess sequence: venv, then pip install -e shadow.
    assert captured[0][:3] == ["/usr/bin/python3", "-m", "venv"], captured
    assert str(real / ".venv") in captured[0]
    assert captured[1][1:4] == ["install", "-e", str(shadow)], captured


@pytest.mark.asyncio
async def test_setup_env_uv_missing_no_python3_returns_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return False

    async def fake_run_silent(command: str, *args: str) -> str | None:
        return None

    monkeypatch.setattr(env_module, "_which", fake_which)
    monkeypatch.setattr(env_module, "_run_silent", fake_run_silent)
    # Block the sys.executable backup too.
    import sys
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="uv sync", shadow_root=shadow, real_workspace=real)
    assert result.is_error
    assert "fatal" in result.output.lower() or "no system" in result.output.lower()


@pytest.mark.asyncio
async def test_setup_env_pip_no_venv_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pip install -r requirements.txt` with no .venv → bootstraps venv via python3."""
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return name in ("pip",)

    async def fake_run_silent(command: str, *args: str) -> str | None:
        if command == "which" and args and args[0] == "python3":
            return "/usr/bin/python3"
        return None

    captured: list[list[str]] = []

    async def fake_capture(cmd: list[str], *, cwd: str, timeout_sec: int) -> tuple[int, str]:
        captured.append(list(cmd))
        return 0, "ok"

    monkeypatch.setattr(env_module, "_which", fake_which)
    monkeypatch.setattr(env_module, "_run_silent", fake_run_silent)
    monkeypatch.setattr(env_module, "_run_capture", fake_capture)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "requirements.txt").write_text("pytest\n")
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(
        command="pip install -r requirements.txt", shadow_root=shadow, real_workspace=real
    )
    assert not result.is_error, result.output
    # Bootstrapped venv first, then ran pip install with the agent's args.
    assert captured[0][:3] == ["/usr/bin/python3", "-m", "venv"]
    assert "install" in captured[1] and "-r" in captured[1] and "requirements.txt" in captured[1]


@pytest.mark.asyncio
async def test_setup_env_npm_missing_suggests_yarn_when_lock_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return name == "yarn"  # npm absent, yarn present

    monkeypatch.setattr(env_module, "_which", fake_which)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "yarn.lock").write_text("")
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="npm ci", shadow_root=shadow, real_workspace=real)
    assert result.is_error
    assert "yarn.lock" in result.output
    assert 'setup_env "yarn install --frozen-lockfile"' in result.output


@pytest.mark.asyncio
async def test_setup_env_npm_missing_no_alternative_returns_install_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return False

    monkeypatch.setattr(env_module, "_which", fake_which)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="npm ci", shadow_root=shadow, real_workspace=real)
    assert result.is_error
    assert "nodejs.org" in result.output
    assert "AGENT SHOULD: emit revision_needed" in result.output


@pytest.mark.asyncio
async def test_setup_env_cargo_missing_returns_install_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return False

    monkeypatch.setattr(env_module, "_which", fake_which)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="cargo build", shadow_root=shadow, real_workspace=real)
    assert result.is_error
    assert "rustup.rs" in result.output
    assert "revision_needed" in result.output


@pytest.mark.asyncio
async def test_init_workspace_python_creates_minimal_pyproject(tmp_path: Path) -> None:
    result = await init_workspace(ecosystem="python", dev_deps=["pytest"], shadow_root=tmp_path)
    assert not result.is_error, result.output
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "pytest" in pyproject
    assert '"name": ' not in pyproject  # not a package.json
    # Minimal: no extra plugins like pytest-cov, pytest-xdist
    assert "pytest-cov" not in pyproject
    assert "pytest-xdist" not in pyproject


@pytest.mark.asyncio
async def test_init_workspace_python_includes_only_declared_deps(tmp_path: Path) -> None:
    result = await init_workspace(
        ecosystem="python", dev_deps=["pytest", "ruff"], shadow_root=tmp_path
    )
    assert not result.is_error
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "pytest" in pyproject
    assert "ruff" in pyproject
    assert "mypy" not in pyproject  # not declared


@pytest.mark.asyncio
async def test_init_workspace_python_refuses_when_pyproject_exists(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("# pre-existing\n")
    result = await init_workspace(ecosystem="python", dev_deps=["pytest"], shadow_root=tmp_path)
    assert result.is_error
    assert "already exists" in result.output


@pytest.mark.asyncio
async def test_init_workspace_node_creates_minimal_package_json(tmp_path: Path) -> None:
    result = await init_workspace(ecosystem="node", dev_deps=["vitest"], shadow_root=tmp_path)
    assert not result.is_error
    import json as _json
    payload = _json.loads((tmp_path / "package.json").read_text())
    assert payload["devDependencies"] == {"vitest": "*"}
    assert payload["scripts"]["test"] == "vitest run"


@pytest.mark.asyncio
async def test_init_workspace_node_no_deps_uses_node_test_runner(tmp_path: Path) -> None:
    result = await init_workspace(ecosystem="node", dev_deps=[], shadow_root=tmp_path)
    assert not result.is_error
    import json as _json
    payload = _json.loads((tmp_path / "package.json").read_text())
    assert payload["scripts"]["test"] == "node --test"
    assert payload["devDependencies"] == {}


@pytest.mark.asyncio
async def test_init_workspace_rust_creates_cargo_toml_and_lib_rs(tmp_path: Path) -> None:
    result = await init_workspace(ecosystem="rust", dev_deps=[], shadow_root=tmp_path)
    assert not result.is_error
    assert (tmp_path / "Cargo.toml").exists()
    assert (tmp_path / "src" / "lib.rs").exists()


@pytest.mark.asyncio
async def test_init_workspace_go_creates_go_mod(tmp_path: Path) -> None:
    result = await init_workspace(ecosystem="go", dev_deps=[], shadow_root=tmp_path)
    assert not result.is_error
    assert "module workspace" in (tmp_path / "go.mod").read_text()


@pytest.mark.asyncio
async def test_init_workspace_rejects_unknown_ecosystem(tmp_path: Path) -> None:
    result = await init_workspace(ecosystem="ruby", dev_deps=[], shadow_root=tmp_path)
    assert result.is_error
    assert "ecosystem" in result.output.lower()


@pytest.mark.asyncio
async def test_setup_env_poetry_missing_returns_install_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poetry has no transparent fallback (lockfile semantics)."""
    from agentd.tools import env as env_module

    async def fake_which(name: str) -> bool:
        return False

    monkeypatch.setattr(env_module, "_which", fake_which)

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    real = tmp_path / "real"
    real.mkdir()

    result = await setup_env(command="poetry install", shadow_root=shadow, real_workspace=real)
    assert result.is_error
    assert "poetry" in result.output.lower()
    assert "revision_needed" in result.output


@pytest.mark.asyncio
async def test_list_directory_shows_files(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("x = 1")
    (tmp_path / "bar.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()

    result = await list_directory(path=".", root=tmp_path)
    assert not result.is_error
    assert "foo.py" in result.output
    assert "bar.txt" in result.output
    assert "subdir" in result.output


@pytest.mark.asyncio
async def test_list_directory_rejects_traversal(tmp_path: Path) -> None:
    result = await list_directory(path="../../etc", root=tmp_path)
    assert result.is_error
    assert "traversal" in result.output.lower()


@pytest.mark.asyncio
async def test_list_directory_missing_path(tmp_path: Path) -> None:
    result = await list_directory(path="nonexistent_dir", root=tmp_path)
    assert result.is_error
