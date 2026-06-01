"""Pydantic model tests for EnvProfile / EnvEcosystemEntry."""
from datetime import datetime, timezone

from agentd.domain.models import (
    EnvEcosystemEntry,
    EnvProfile,
    TaskExecutionState,
)


def test_env_ecosystem_entry_scope_key_combines_ecosystem_and_subdir():
    entry = EnvEcosystemEntry(
        ecosystem="python",
        subdir="services/agentd-py",
        manifest_path="services/agentd-py/pyproject.toml",
        package_manager="uv",
        install_command="uv sync",
        interpreter_or_runner=".venv/bin/python",
        test_command="pytest",
        declared_dependencies_top=["fastapi>=0.116.0"],
        notes=None,
    )
    assert entry.scope_key == "python:services/agentd-py"


def test_env_ecosystem_entry_workspace_root_subdir_becomes_empty_scope():
    entry = EnvEcosystemEntry(
        ecosystem="node",
        subdir="",
        manifest_path="package.json",
        package_manager="npm",
        install_command="npm ci",
        interpreter_or_runner=None,
        test_command="npm test",
        declared_dependencies_top=[],
        notes=None,
    )
    assert entry.scope_key == "node:"


def test_env_profile_roundtrips_through_json():
    profile = EnvProfile(
        workspace_root="/tmp/ws",
        built_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        bootstrap_needed=False,
        ecosystems=[
            EnvEcosystemEntry(
                ecosystem="python",
                subdir="",
                manifest_path="pyproject.toml",
                package_manager="uv",
                install_command="uv sync",
                interpreter_or_runner=".venv/bin/python",
                test_command="pytest",
                declared_dependencies_top=["pydantic>=2"],
                notes=None,
            )
        ],
        conventions_notes="uses uv",
        diagnostics=[],
    )
    raw = profile.model_dump_json()
    round = EnvProfile.model_validate_json(raw)
    assert round.ecosystems[0].package_manager == "uv"
    assert round.bootstrap_needed is False


def test_task_execution_state_has_pending_install_default_none():
    state = TaskExecutionState()
    assert state.pending_install_for_scope is None


def test_task_execution_state_pending_install_accepts_string():
    state = TaskExecutionState(pending_install_for_scope="python:services/agentd-py")
    assert state.pending_install_for_scope == "python:services/agentd-py"
