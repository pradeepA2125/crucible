"""Tests for resolve_workspace_bin (shared workspace-local binary resolver)."""
from __future__ import annotations

from pathlib import Path

from agentd.tools._paths import resolve_workspace_bin


def _make_executable(path: Path, content: str = "#!/bin/sh\necho ok\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)
    return path


def test_resolve_finds_venv_bin(tmp_path: Path) -> None:
    pytest_bin = _make_executable(tmp_path / ".venv" / "bin" / "pytest")
    assert resolve_workspace_bin(tmp_path, "pytest") == pytest_bin


def test_resolve_finds_node_modules_bin(tmp_path: Path) -> None:
    vitest_bin = _make_executable(tmp_path / "node_modules" / ".bin" / "vitest")
    assert resolve_workspace_bin(tmp_path, "vitest") == vitest_bin


def test_resolve_finds_cargo_target(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path / "target" / "release" / "myapp")
    assert resolve_workspace_bin(tmp_path, "myapp") == binary


def test_resolve_returns_none_when_not_found(tmp_path: Path) -> None:
    assert resolve_workspace_bin(tmp_path, "pytest") is None


def test_resolve_skips_non_executable(tmp_path: Path) -> None:
    bin_path = tmp_path / ".venv" / "bin" / "pytest"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text("#!/bin/sh\necho ok\n")  # mode 0644 by default
    assert resolve_workspace_bin(tmp_path, "pytest") is None


def test_resolve_prefers_venv_over_node_modules(tmp_path: Path) -> None:
    """Dir-order matters: .venv/bin ranks above node_modules/.bin."""
    venv_bin = _make_executable(tmp_path / ".venv" / "bin" / "tool")
    _make_executable(tmp_path / "node_modules" / ".bin" / "tool")
    assert resolve_workspace_bin(tmp_path, "tool") == venv_bin


def test_resolve_rejects_path_separators(tmp_path: Path) -> None:
    _make_executable(tmp_path / ".venv" / "bin" / "pytest")
    assert resolve_workspace_bin(tmp_path, "../pytest") is None
    assert resolve_workspace_bin(tmp_path, ".venv/bin/pytest") is None


def test_resolve_rejects_empty_name(tmp_path: Path) -> None:
    assert resolve_workspace_bin(tmp_path, "") is None
