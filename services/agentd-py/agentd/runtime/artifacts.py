from __future__ import annotations

import os
from pathlib import Path


def resolve_artifacts_base(workspace_path: str | Path | None = None) -> Path:
    raw = os.getenv("AI_EDITOR_ARTIFACTS_ROOT")
    workspace = Path(workspace_path).resolve() if workspace_path is not None else None
    if raw:
        rendered = raw
        if workspace is not None:
            rendered = raw.format(workspace=str(workspace))
        return Path(rendered).expanduser().resolve()
    if workspace is not None:
        return (workspace / ".agentd" / "artifacts").resolve()
    return Path(".agentd/artifacts").resolve()


def task_artifacts_root(task_id: str, workspace_path: str | Path | None = None) -> Path:
    return resolve_artifacts_base(workspace_path) / task_id


def provider_debug_root(
    provider_name: str,
    workspace_path: str | Path | None = None,
) -> Path:
    return resolve_artifacts_base(workspace_path) / "_provider_debug" / provider_name

