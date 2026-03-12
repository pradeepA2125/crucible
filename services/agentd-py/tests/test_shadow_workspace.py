from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentd.domain.models import TaskRecord, TaskStatus
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_shadow_workspace_prepare_promote_and_cleanup(tmp_path: Path) -> None:
    real_workspace = tmp_path / "real"
    real_workspace.mkdir(parents=True)
    (real_workspace / "src").mkdir()
    (real_workspace / "src/main.py").write_text("print('real')\n", encoding="utf-8")

    manager = ShadowWorkspaceManager(root_path=tmp_path / "shadows")
    shadow = await manager.prepare("task-1", str(real_workspace))

    shadow_file = shadow.shadow_path / "src/main.py"
    assert shadow_file.exists()

    shadow_file.write_text("print('shadow')\n", encoding="utf-8")

    task = TaskRecord(
        task_id="task-1",
        goal="goal",
        workspace_path=str(real_workspace),
        shadow_workspace_path=str(shadow.shadow_path),
        status=TaskStatus.SUCCEEDED,
        modified_files=["src/main.py"],
    )

    await manager.promote(task)
    assert (real_workspace / "src/main.py").read_text(encoding="utf-8") == "print('shadow')\n"

    await manager.cleanup(task)
    assert not shadow.shadow_path.exists()


@pytest.mark.asyncio
async def test_shadow_workspace_prunes_old_checkpoint_tasks(tmp_path: Path) -> None:
    manager = ShadowWorkspaceManager(
        root_path=tmp_path / "shadows",
        checkpoint_retention_tasks=2,
    )
    checkpoints_root = (tmp_path / "shadows" / "_checkpoints")
    checkpoints_root.mkdir(parents=True)
    oldest = checkpoints_root / "task-oldest"
    middle = checkpoints_root / "task-middle"
    newest = checkpoints_root / "task-newest"
    for item in (oldest, middle, newest):
        item.mkdir(parents=True)
        (item / "sentinel.txt").write_text(item.name, encoding="utf-8")

    os.utime(oldest, (1_000_000, 1_000_000))
    os.utime(middle, (2_000_000, 2_000_000))
    os.utime(newest, (3_000_000, 3_000_000))

    await manager.prune_checkpoints()

    remaining = sorted(path.name for path in checkpoints_root.iterdir() if path.is_dir())
    assert remaining == ["task-middle", "task-newest"]
