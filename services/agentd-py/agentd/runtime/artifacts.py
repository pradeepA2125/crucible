from __future__ import annotations

import os
from pathlib import Path


def resolve_artifacts_base(workspace_path: str | Path | None = None) -> Path:
    raw = os.getenv("CRUCIBLE_ARTIFACTS_ROOT")
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


def chat_turn_artifacts_root(
    thread_id: str,
    turn_id: str,
    workspace_path: str | Path | None = None,
) -> Path:
    """Artifacts for ONE controller chat turn. Controller turns have no task_id, so they
    nest under chat/<thread_id>/<turn_id>/ (vs task path's <task_id>/). Holds the exact
    LLM bytes per iteration + the turn trace — the controller analog of the task path's
    debug-plan-turn-NN / tool-trace.json."""
    return resolve_artifacts_base(workspace_path) / "chat" / thread_id / turn_id


def provider_debug_root(
    provider_name: str,
    workspace_path: str | Path | None = None,
) -> Path:
    return resolve_artifacts_base(workspace_path) / "_provider_debug" / provider_name

