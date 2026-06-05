"""Tests for EnvProfileStore — JSON read/write/staleness."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.profile_store import EnvProfileStore


def _make_profile(ws: Path, built_at: datetime | None = None) -> EnvProfile:
    return EnvProfile(
        workspace_root=str(ws),
        built_at=built_at or datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="", manifest_path="pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=["pydantic>=2"], notes=None,
        )],
        conventions_notes=None, diagnostics=[],
    )


def test_read_missing_returns_none(tmp_path: Path):
    store = EnvProfileStore()
    assert store.read(tmp_path) is None


def test_write_then_read_roundtrip(tmp_path: Path):
    store = EnvProfileStore()
    p = _make_profile(tmp_path)
    store.write(tmp_path, p)
    got = store.read(tmp_path)
    assert got is not None
    assert got.ecosystems[0].package_manager == "uv"


def test_write_creates_agentd_dir_if_missing(tmp_path: Path):
    store = EnvProfileStore()
    store.write(tmp_path, _make_profile(tmp_path))
    assert (tmp_path / ".agentd" / "env_profile.json").is_file()


def test_is_stale_returns_true_when_missing(tmp_path: Path):
    store = EnvProfileStore()
    assert store.is_stale(tmp_path) is True


def test_is_stale_returns_false_for_fresh_profile(tmp_path: Path):
    store = EnvProfileStore()
    store.write(tmp_path, _make_profile(tmp_path))
    assert store.is_stale(tmp_path) is False


def test_is_stale_returns_true_for_old_profile(tmp_path: Path):
    store = EnvProfileStore(max_age_days=30)
    old = datetime.now(timezone.utc) - timedelta(days=31)
    store.write(tmp_path, _make_profile(tmp_path, built_at=old))
    pth = tmp_path / ".agentd" / "env_profile.json"
    os.utime(pth, (old.timestamp(), old.timestamp()))
    assert store.is_stale(tmp_path) is True


def test_read_returns_none_on_corrupted_json(tmp_path: Path):
    (tmp_path / ".agentd").mkdir()
    (tmp_path / ".agentd" / "env_profile.json").write_text("{not valid json")
    store = EnvProfileStore()
    assert store.read(tmp_path) is None
