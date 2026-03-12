from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from agentd.domain.models import Diagnostic, PatchDocument, PlanDocument, StepExecutionTrace, TaskRecord, TaskStatus


class TaskReplayBundle(BaseModel):
    schema_version: str = "task-replay-bundle.v1"
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: str
    goal: str
    status: TaskStatus
    workspace_path: str
    shadow_workspace_path: str | None = None
    plan: PlanDocument | None = None
    patch: PatchDocument | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    execution_trace: list[StepExecutionTrace] = Field(default_factory=list)
    source_db_path: str | None = None
    source_artifacts_root: str | None = None


class ReplayCheckResult(BaseModel):
    bundle_path: str
    fingerprint: str
    deterministic: bool
    matches_expected: bool
    expected_fingerprint: str | None = None


def export_bundle_from_task(
    task: TaskRecord,
    *,
    db_path: Path | None = None,
    artifacts_root: Path | None = None,
) -> TaskReplayBundle:
    return TaskReplayBundle(
        task_id=task.task_id,
        goal=task.goal,
        status=task.status,
        workspace_path=task.workspace_path,
        shadow_workspace_path=task.shadow_workspace_path,
        plan=task.plan,
        patch=task.latest_patch,
        completed_step_ids=task.completed_step_ids,
        modified_files=task.modified_files,
        diagnostics=task.diagnostics,
        execution_trace=task.execution_trace,
        source_db_path=str(db_path.resolve()) if db_path else None,
        source_artifacts_root=str(artifacts_root.resolve()) if artifacts_root else None,
    )


def load_task_from_db(db_path: Path, task_id: str) -> TaskRecord:
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
    finally:
        conn.close()

    if row is None:
        msg = f"Task not found in database: {task_id}"
        raise KeyError(msg)
    return TaskRecord.model_validate_json(str(row[0]))


def bundle_fingerprint(bundle: TaskReplayBundle) -> str:
    payload = bundle.model_dump(mode="json")
    payload.pop("captured_at", None)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def replay_bundle(bundle_path: Path, expected_fingerprint: str | None = None) -> ReplayCheckResult:
    bundle = load_bundle_file(bundle_path)
    first = bundle_fingerprint(bundle)
    second = bundle_fingerprint(load_bundle_file(bundle_path))
    deterministic = first == second
    matches_expected = expected_fingerprint is None or first == expected_fingerprint
    return ReplayCheckResult(
        bundle_path=str(bundle_path.resolve()),
        fingerprint=first,
        deterministic=deterministic,
        matches_expected=matches_expected,
        expected_fingerprint=expected_fingerprint,
    )


def load_bundle_file(path: Path) -> TaskReplayBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") == "task-replay-bundle.v1":
        return TaskReplayBundle.model_validate(payload)

    # Best effort fallback for /v1/tasks/{id}/result snapshots.
    if "task_id" in payload and "status" in payload and "goal" in payload:
        return TaskReplayBundle(
            task_id=str(payload["task_id"]),
            goal=str(payload["goal"]),
            status=TaskStatus(str(payload["status"])),
            workspace_path=str(payload.get("workspace_path", "")),
            shadow_workspace_path=payload.get("shadow_workspace_path"),
            plan=PlanDocument.model_validate(payload["plan"]) if payload.get("plan") else None,
            patch=PatchDocument.model_validate(payload["patch"]) if payload.get("patch") else None,
            completed_step_ids=list(payload.get("completed_step_ids", [])),
            modified_files=list(payload.get("modified_files", [])),
            diagnostics=[
                Diagnostic.model_validate(item)
                for item in payload.get("diagnostics", [])
            ],
            execution_trace=[
                StepExecutionTrace.model_validate(item)
                for item in payload.get("execution_trace", [])
            ],
        )

    msg = f"Unsupported replay bundle format: {path}"
    raise ValueError(msg)
