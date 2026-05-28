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
