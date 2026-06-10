from __future__ import annotations

from pathlib import Path

import pytest

from agentd.tools import _paths
from agentd.tools._paths import editable_package_names, shadow_import_roots
from agentd.tools.shell import run_command


def _make_pkg(root: Path, rel: str) -> Path:
    """Create <root>/<rel>/__init__.py and return the package dir."""
    pkg = root / rel
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    return pkg


def test_shadow_import_roots_finds_package_by_name(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    _make_pkg(shadow, "services/agentd-py/agentd")
    roots = shadow_import_roots(shadow, {"agentd"})
    assert roots == [str(shadow / "services/agentd-py")]


def test_shadow_import_roots_prefers_shallowest(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    _make_pkg(shadow, "pkg")  # shallow: <shadow>/pkg
    _make_pkg(shadow, "nested/deeper/pkg")  # deeper
    roots = shadow_import_roots(shadow, {"pkg"})
    assert roots == [str(shadow)]


def test_shadow_import_roots_skips_vendored_dirs(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    _make_pkg(shadow, ".venv/lib/python3.13/site-packages/agentd")
    _make_pkg(shadow, "node_modules/agentd")
    assert shadow_import_roots(shadow, {"agentd"}) == []


def test_shadow_import_roots_empty_for_unknown_package(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    _make_pkg(shadow, "services/agentd-py/agentd")
    assert shadow_import_roots(shadow, {"nonexistent"}) == []


def test_editable_package_names_detects_agentd() -> None:
    # agentd is installed editable (pip install -e) in the dev/CI venv, so it must be
    # detected as a package whose shadow copy should win over the installed one.
    assert "agentd" in editable_package_names()


def _fake_echo_pythonpath(tmp_path: Path) -> Path:
    script = tmp_path / "echo_pp"
    script.write_text('#!/bin/sh\nprintf "%s" "$PYTHONPATH"\n')
    script.chmod(0o755)
    return script


@pytest.mark.asyncio
async def test_run_command_prepends_shadow_import_root_for_pytest(tmp_path: Path, monkeypatch) -> None:
    shadow = tmp_path / "shadow"
    real = tmp_path / "real"
    real.mkdir()
    _make_pkg(shadow, "pkgs/mypkg")

    monkeypatch.setattr(_paths, "editable_package_names", lambda: {"mypkg"})
    script = _fake_echo_pythonpath(tmp_path)

    out = await run_command(
        command=str(script),
        args=[],
        shadow_root=shadow,
        real_workspace_path=real,
        binary_name_override="pytest",
    )
    assert str(shadow / "pkgs") in out.output


def test_split_command_naked_uv_run_falls_back_to_first_token(tmp_path: Path) -> None:
    """`command="uv run pytest"` (model packed the whole line in) must split to
    executable 'uv' + the rest as args, not be looked up as one binary."""
    from agentd.tools.shell import _split_command

    cmd, args = _split_command("uv run pytest", ["tests/x.py", "-x"], tmp_path)
    assert cmd == "uv"
    assert args == ["run", "pytest", "tests/x.py", "-x"]


def test_split_command_abs_path_with_space_plus_args(tmp_path: Path) -> None:
    """The workspace path can contain a space ('AI editor'); the longest leading
    run that resolves to a real file is the executable, the rest are args."""
    from agentd.tools.shell import _split_command

    binp = tmp_path / "AI editor" / "bin"
    binp.mkdir(parents=True)
    py = binp / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)

    cmd, args = _split_command(f"{py} -m pytest tests/x.py -x", [], tmp_path)
    assert cmd == str(py)
    assert args == ["-m", "pytest", "tests/x.py", "-x"]


def test_split_command_whole_path_is_file_unchanged(tmp_path: Path) -> None:
    from agentd.tools.shell import _split_command

    binp = tmp_path / "AI editor" / "bin"
    binp.mkdir(parents=True)
    py = binp / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)

    cmd, args = _split_command(str(py), ["-m", "pytest"], tmp_path)
    assert cmd == str(py)
    assert args == ["-m", "pytest"]


def test_split_command_no_whitespace_unchanged(tmp_path: Path) -> None:
    from agentd.tools.shell import _split_command

    cmd, args = _split_command("pytest", ["-x"], tmp_path)
    assert cmd == "pytest"
    assert args == ["-x"]


def _fake_echo_uv_env(tmp_path: Path) -> Path:
    script = tmp_path / "echo_uv_env"
    script.write_text(
        '#!/bin/sh\nprintf "UV=%s VENV=%s" "$UV_PROJECT_ENVIRONMENT" "$VIRTUAL_ENV"\n'
    )
    script.chmod(0o755)
    return script


@pytest.mark.asyncio
async def test_run_command_points_uv_at_workspace_venv(tmp_path: Path) -> None:
    """uv run / python must resolve the setup_env-populated WORKSPACE venv, not
    create an empty one in the shadow cwd. run_command sets UV_PROJECT_ENVIRONMENT
    and VIRTUAL_ENV to <real_workspace>/<cwd>/.venv (mirrors setup_env's install_root)."""
    shadow = tmp_path / "shadow"
    (shadow / "services/agentd-py").mkdir(parents=True)
    real = tmp_path / "real"
    (real / "services/agentd-py").mkdir(parents=True)
    script = _fake_echo_uv_env(tmp_path)

    out = await run_command(
        command=str(script),
        args=[],
        shadow_root=shadow,
        real_workspace_path=real,
        cwd="services/agentd-py",
    )
    expected = str(real / "services/agentd-py" / ".venv")
    assert f"UV={expected}" in out.output
    assert f"VENV={expected}" in out.output


@pytest.mark.asyncio
async def test_run_command_skips_shadow_import_root_for_non_python_tool(tmp_path: Path, monkeypatch) -> None:
    shadow = tmp_path / "shadow"
    real = tmp_path / "real"
    real.mkdir()
    _make_pkg(shadow, "pkgs/mypkg")

    monkeypatch.setattr(_paths, "editable_package_names", lambda: {"mypkg"})
    script = _fake_echo_pythonpath(tmp_path)

    out = await run_command(
        command=str(script),
        args=[],
        shadow_root=shadow,
        real_workspace_path=real,
        binary_name_override="eslint",
    )
    assert str(shadow / "pkgs") not in out.output
