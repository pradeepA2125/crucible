"""Tests for the read_env_profile tool + registry exposure."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.profile_store import EnvProfileStore
from agentd.tools.registry import ToolRegistry


def _write_profile(workspace: Path) -> EnvProfile:
    profile = EnvProfile(
        workspace_root=str(workspace),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="", manifest_path="pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=["pydantic>=2"], notes=None,
        )],
        conventions_notes="uses uv", diagnostics=[],
    )
    EnvProfileStore().write(workspace, profile)
    return profile


@pytest.mark.asyncio
async def test_read_env_profile_returns_json_when_present(tmp_path: Path):
    _write_profile(tmp_path)
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    out = await reg.execute("read_env_profile", {})
    assert not out.is_error
    parsed = json.loads(out.output)
    assert parsed["ecosystems"][0]["package_manager"] == "uv"


@pytest.mark.asyncio
async def test_read_env_profile_returns_friendly_message_when_absent(tmp_path: Path):
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    out = await reg.execute("read_env_profile", {})
    assert not out.is_error
    assert "not yet built" in out.output


def test_read_env_profile_in_explore_definitions(tmp_path: Path):
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {d.name for d in reg.definitions(phase="explore")}
    assert "read_env_profile" in names


def test_read_env_profile_in_verify_definitions(tmp_path: Path):
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {d.name for d in reg.definitions(phase="verify")}
    assert "read_env_profile" in names
