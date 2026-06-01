"""Tests for EnvProfileBuilder — composes probe + draft_conventions."""
from datetime import datetime
from pathlib import Path

import pytest

from agentd.env.profile_builder import EnvProfileBuilder
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_build_returns_profile_from_probe_and_llm(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname=\"demo\"\nversion=\"0\"\ndependencies=[\"fastapi\"]\n"
    )
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": ["fastapi"], "notes": None,
        }],
        "conventions_notes": "uses uv",
    }
    engine = ScriptedReasoningEngine(
        plan=None, patches=[], draft_conventions_responses=[canned],
    )
    builder = EnvProfileBuilder(reasoner=engine)
    profile = await builder.build(tmp_path)

    assert profile.workspace_root == str(tmp_path.resolve())
    assert profile.bootstrap_needed is False
    assert len(profile.ecosystems) == 1
    assert profile.ecosystems[0].install_command == "uv sync"
    assert profile.conventions_notes == "uses uv"
    assert isinstance(profile.built_at, datetime)


@pytest.mark.asyncio
async def test_build_on_bare_workspace_sets_bootstrap_needed(tmp_path: Path):
    engine = ScriptedReasoningEngine(
        plan=None, patches=[], draft_conventions_responses=[],
    )
    builder = EnvProfileBuilder(reasoner=engine)
    profile = await builder.build(tmp_path)

    assert profile.bootstrap_needed is True
    assert profile.ecosystems == []
    assert any("no manifests" in d.lower() for d in profile.diagnostics)


@pytest.mark.asyncio
async def test_build_passes_diagnostics_through_to_profile(tmp_path: Path):
    # Trigger the SETUPTOOLS_FLAT_LAYOUT_RISK diagnostic via the probe.
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname=\"demo\"\nversion=\"0\"\n"
        "[build-system]\nrequires=[\"setuptools>=68\"]\n"
        "build-backend=\"setuptools.build_meta\"\n"
    )
    (tmp_path / "agentd").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "workspaces").mkdir()
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": [], "notes": "flat-layout — added find stanza",
        }],
        "conventions_notes": None,
    }
    engine = ScriptedReasoningEngine(
        plan=None, patches=[], draft_conventions_responses=[canned],
    )
    builder = EnvProfileBuilder(reasoner=engine)
    profile = await builder.build(tmp_path)

    assert any("SETUPTOOLS_FLAT_LAYOUT_RISK" in d for d in profile.diagnostics)


@pytest.mark.asyncio
async def test_build_retries_once_on_llm_error_then_marks_bootstrap_needed(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname=\"x\"\nversion=\"0\"\n")

    class BrokenEngine:
        calls = 0

        async def draft_conventions(self, *, probe):
            BrokenEngine.calls += 1
            raise RuntimeError("boom")

    builder = EnvProfileBuilder(reasoner=BrokenEngine())
    profile = await builder.build(tmp_path)
    assert BrokenEngine.calls == 2  # one retry
    assert profile.bootstrap_needed is True
    assert any("convention drafting failed" in d for d in profile.diagnostics)
