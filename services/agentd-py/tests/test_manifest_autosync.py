"""Tests for maybe_run_pending_install — the auto-sync helper."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.auto_sync import maybe_run_pending_install
from agentd.env.profile_store import EnvProfileStore


class _RecordingBroadcaster:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, channel_id: str, event: dict) -> None:
        self.events.append((channel_id, event))


def _python_profile(workspace: Path, subdir: str = "services/agentd-py") -> None:
    EnvProfileStore().write(workspace, EnvProfile(
        workspace_root=str(workspace),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir=subdir,
            manifest_path=f"{subdir}/pyproject.toml" if subdir else "pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=[], notes=None,
        )],
        conventions_notes=None, diagnostics=[],
    ))


@pytest.mark.asyncio
async def test_does_nothing_when_scope_key_is_none(tmp_path: Path):
    _python_profile(tmp_path)
    broadcaster = _RecordingBroadcaster()

    calls = []
    async def fake_setup_env(*a, **k):
        calls.append(k)
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="", is_error=False)

    with mock_patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            scope_key=None,
            real_workspace=tmp_path,
            shadow_root=tmp_path,
            broadcaster=broadcaster,
            broadcast_key="ch",
        )

    assert calls == []
    assert broadcaster.events == []


@pytest.mark.asyncio
async def test_runs_install_with_profile_command_and_cwd(tmp_path: Path):
    _python_profile(tmp_path, subdir="services/agentd-py")
    broadcaster = _RecordingBroadcaster()

    captured = {}
    async def fake_setup_env(*, command, shadow_root, real_workspace, cwd=None, **_):
        captured["command"] = command
        captured["cwd"] = cwd
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="installed ok", is_error=False)

    with mock_patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            scope_key="python:services/agentd-py",
            real_workspace=tmp_path,
            shadow_root=tmp_path,
            broadcaster=broadcaster,
            broadcast_key="ch",
        )

    assert captured["command"] == "uv sync"
    assert captured["cwd"] == "services/agentd-py"

    event_types = [evt["type"] for _, evt in broadcaster.events]
    assert event_types == ["env_install_running", "env_install_done"]
    # done event includes exit_ok flag
    done_payload = broadcaster.events[-1][1]["payload"]
    assert done_payload["exit_ok"] is True


@pytest.mark.asyncio
async def test_still_emits_done_when_install_fails(tmp_path: Path):
    _python_profile(tmp_path)
    broadcaster = _RecordingBroadcaster()

    async def failing_setup_env(*a, **k):
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="boom", is_error=True)

    with mock_patch("agentd.tools.env.setup_env", failing_setup_env):
        await maybe_run_pending_install(
            scope_key="python:services/agentd-py",
            real_workspace=tmp_path,
            shadow_root=tmp_path,
            broadcaster=broadcaster,
            broadcast_key="ch",
        )

    event_types = [evt["type"] for _, evt in broadcaster.events]
    assert "env_install_done" in event_types
    done_payload = [evt for _, evt in broadcaster.events if evt["type"] == "env_install_done"][0]
    assert done_payload["payload"]["exit_ok"] is False


@pytest.mark.asyncio
async def test_scope_key_with_no_matching_entry_is_a_noop(tmp_path: Path):
    """If somehow a stale scope_key points at a scope no longer in the profile,
    bail silently rather than crashing the loop."""
    _python_profile(tmp_path, subdir="services/agentd-py")
    broadcaster = _RecordingBroadcaster()

    calls = []
    async def fake_setup_env(*a, **k):
        calls.append(k)
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="", is_error=False)

    with mock_patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            scope_key="rust:services/old",  # not in profile
            real_workspace=tmp_path,
            shadow_root=tmp_path,
            broadcaster=broadcaster,
            broadcast_key="ch",
        )
    assert calls == []
    assert broadcaster.events == []


@pytest.mark.asyncio
async def test_scope_key_with_no_profile_is_a_noop(tmp_path: Path):
    # No profile written.
    broadcaster = _RecordingBroadcaster()

    calls = []
    async def fake_setup_env(*a, **k):
        calls.append(k)
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="", is_error=False)

    with mock_patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            scope_key="python:",
            real_workspace=tmp_path,
            shadow_root=tmp_path,
            broadcaster=broadcaster,
            broadcast_key="ch",
        )
    assert calls == []
