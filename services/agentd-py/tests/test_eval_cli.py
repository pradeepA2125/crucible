from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agentd.domain.models import TaskRecord, TaskStatus
from agentd.eval.bundle import bundle_fingerprint, load_bundle_file
from agentd.eval.cli import main


def _seed_db_with_task(db_path: Path, task: TaskRecord) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              goal TEXT NOT NULL,
              status TEXT NOT NULL,
              workspace_path TEXT NOT NULL,
              shadow_workspace_path TEXT,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO tasks (
              task_id, goal, status, workspace_path, shadow_workspace_path,
              payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.goal,
                task.status,
                task.workspace_path,
                task.shadow_workspace_path,
                task.model_dump_json(),
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_init_manifest_writes_seed_manifest(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "pydantic").mkdir(parents=True)
    (workspace_root / "typescript-language-server").mkdir(parents=True)
    output = tmp_path / "manifest.json"

    code = main(
        [
            "init-corpus-manifest",
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(output),
            "--freeze",
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "benchmark-corpus.v1"
    assert payload["frozen"] is True
    assert payload["stats"]["total_tasks"] == 2
    assert payload["stats"]["oss_tasks"] == 2


def test_export_replay_and_score(tmp_path: Path) -> None:
    db_path = tmp_path / "agentd.sqlite3"
    task = TaskRecord(
        task_id="task-1",
        goal="demo",
        workspace_path=str(tmp_path),
        status=TaskStatus.SUCCEEDED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    _seed_db_with_task(db_path, task)

    bundle_path = tmp_path / "bundle.task-1.json"
    code = main(
        [
            "export-bundle",
            "--db-path",
            str(db_path),
            "--task-id",
            "task-1",
            "--output",
            str(bundle_path),
        ]
    )
    assert code == 0

    bundle = load_bundle_file(bundle_path)
    expected_fingerprint = bundle_fingerprint(bundle)

    replay_output = tmp_path / "replay.json"
    code = main(
        [
            "replay-bundle",
            "--bundle",
            str(bundle_path),
            "--expect-fingerprint",
            expected_fingerprint,
            "--output",
            str(replay_output),
        ]
    )
    assert code == 0
    replay_payload = json.loads(replay_output.read_text(encoding="utf-8"))
    assert replay_payload["deterministic"] is True
    assert replay_payload["matches_expected"] is True

    weekly_output = tmp_path / "weekly.json"
    code = main(
        [
            "weekly-report",
            "--bundles-root",
            str(tmp_path),
            "--output",
            str(weekly_output),
        ]
    )
    assert code == 0
    weekly_payload = json.loads(weekly_output.read_text(encoding="utf-8"))
    assert weekly_payload["schema_version"] == "benchmark-weekly-report.v1"
    assert weekly_payload["summary"]["total_bundles"] >= 1
    assert "failure_counts" in weekly_payload["summary"]


def test_phase1_gate_report(tmp_path: Path) -> None:
    baseline_root = tmp_path / "baseline"
    current_root = tmp_path / "current"
    baseline_root.mkdir(parents=True)
    current_root.mkdir(parents=True)

    failed_task = TaskRecord(
        task_id="task-fail",
        goal="demo",
        workspace_path=str(tmp_path),
        status=TaskStatus.FAILED,
        diagnostics=[
            {
                "source": "patch_preflight:anchor_missing",
                "message": "anchor missing",
                "level": "error",
            }
        ],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    ok_task = TaskRecord(
        task_id="task-ok",
        goal="demo",
        workspace_path=str(tmp_path),
        status=TaskStatus.SUCCEEDED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    (baseline_root / "bundle-fail.json").write_text(
        json.dumps(
            {
                "task_id": failed_task.task_id,
                "status": failed_task.status,
                "goal": failed_task.goal,
                "workspace_path": failed_task.workspace_path,
                "plan": None,
                "patch": None,
                "modified_files": [],
                "diagnostics": [item.model_dump(mode="json") for item in failed_task.diagnostics],
                "execution_trace": [],
                "fingerprint": "x",
            }
        ),
        encoding="utf-8",
    )
    (current_root / "bundle-ok.json").write_text(
        json.dumps(
            {
                "task_id": ok_task.task_id,
                "status": ok_task.status,
                "goal": ok_task.goal,
                "workspace_path": ok_task.workspace_path,
                "plan": None,
                "patch": None,
                "modified_files": [],
                "diagnostics": [],
                "execution_trace": [],
                "fingerprint": "y",
            }
        ),
        encoding="utf-8",
    )

    gate_output = tmp_path / "phase1-gate.json"
    code = main(
        [
            "phase1-gate-report",
            "--bundles-root",
            str(current_root),
            "--baseline-bundles-root",
            str(baseline_root),
            "--output",
            str(gate_output),
        ]
    )
    assert code == 0
    payload = json.loads(gate_output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase1-gate-report.v1"
    assert payload["gate"]["baseline_cluster_failures"] >= 1
