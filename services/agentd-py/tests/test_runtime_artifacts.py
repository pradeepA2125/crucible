from __future__ import annotations

from pathlib import Path

from agentd.runtime.artifacts import provider_debug_root, resolve_artifacts_base, task_artifacts_root


def test_resolve_artifacts_base_defaults_to_workspace_local_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AI_EDITOR_ARTIFACTS_ROOT", raising=False)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    assert resolve_artifacts_base(workspace) == workspace / ".agentd" / "artifacts"


def test_resolve_artifacts_base_honors_env_template(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setenv("AI_EDITOR_ARTIFACTS_ROOT", str(tmp_path / "artifacts" / "{workspace}"))
    resolved = resolve_artifacts_base(workspace)
    assert str(workspace) in str(resolved)


def test_task_and_provider_roots_share_same_base(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AI_EDITOR_ARTIFACTS_ROOT", raising=False)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    base = resolve_artifacts_base(workspace)
    assert task_artifacts_root("task-123", workspace) == base / "task-123"
    assert provider_debug_root("groq", workspace) == base / "_provider_debug" / "groq"
