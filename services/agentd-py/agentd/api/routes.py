from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException

from agentd.domain.models import (
    Diagnostic,
    RejectPatchRequest,
    StepProgress,
    TaskArtifactEntry,
    TaskArtifactsResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskRecord,
    TaskResult,
    TaskStatus,
    TaskView,
)
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.storage.base import TaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _to_task_result(task: TaskRecord) -> TaskResult:
    selected_patch = None
    patch_candidates = []
    if task.latest_patch_v2:
        patch_candidates = [*task.latest_patch_v2.candidates]
        if task.selected_candidate_id:
            selected_patch = next(
                (
                    item
                    for item in patch_candidates
                    if item.candidate_id == task.selected_candidate_id
                ),
                None,
            )
        if selected_patch is None and patch_candidates:
            selected_patch = patch_candidates[0]
    elif task.latest_patch:
        selected_patch = task.latest_patch

    total_steps = len(task.plan.steps) if task.plan else 0
    completed_steps = len(task.completed_step_ids)
    current_step_id: str | None = None
    if task.plan:
        for step in task.plan.steps:
            if step.id not in task.completed_step_ids:
                current_step_id = step.id
                break

    step_progress = (
        StepProgress(
            total_steps=total_steps,
            completed_steps=completed_steps,
            remaining_steps=max(total_steps - completed_steps, 0),
            current_step_id=current_step_id,
        )
        if task.plan
        else None
    )

    return TaskResult(
        task_id=task.task_id,
        goal=task.goal,
        status=task.status,
        plan=task.plan,
        patch=selected_patch,
        patch_candidates=patch_candidates,
        selected_candidate_id=task.selected_candidate_id,
        modified_files=task.modified_files,
        diagnostics=task.diagnostics,
        promoted_at=task.promoted_at,
        shadow_workspace_path=task.shadow_workspace_path,
        step_progress=step_progress,
        execution_trace=task.execution_trace[-50:],
        artifacts_root_path=task.artifacts_root_path,
    )


def _list_task_artifacts(task: TaskRecord) -> list[TaskArtifactEntry]:
    root_value = task.artifacts_root_path
    if not root_value:
        return []

    root = Path(root_value)
    if not root.exists() or not root.is_dir():
        return []

    entries: list[TaskArtifactEntry] = []
    for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
        relative = file_path.relative_to(root).as_posix()
        parts = relative.split("/")
        step_id: str | None = None
        attempt: int | None = None
        for part in parts:
            if part.startswith("step-"):
                step_id = part.removeprefix("step-")
            elif part.startswith("attempt-"):
                try:
                    attempt = int(part.removeprefix("attempt-"))
                except ValueError:
                    attempt = None

        name = file_path.name.lower()
        kind = "other"
        if "checkpoint" in name:
            kind = "checkpoint"
        elif "preflight" in name:
            kind = "preflight"
        elif "validation" in name:
            kind = "validation"
        elif "ranking" in name:
            kind = "ranking"
        elif "plan" in name:
            kind = "plan"
        elif "patch" in name:
            kind = "patch"

        candidate_id: str | None = None
        if "preflight-" in name:
            candidate_id = name.split("preflight-", maxsplit=1)[1].removesuffix(".json")
        elif "validation-" in name:
            candidate_id = name.split("validation-", maxsplit=1)[1].removesuffix(".json")

        entries.append(
            TaskArtifactEntry(
                relative_path=relative,
                kind=kind,  # type: ignore[arg-type]
                step_id=step_id,
                attempt=attempt,
                candidate_id=candidate_id,
            )
        )
    return entries


def build_router(
    store: TaskStore,
    orchestrator: AgentOrchestrator,
    workspace_manager: ShadowWorkspaceManager,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["tasks"])

    @router.post("/tasks", response_model=TaskCreateResponse)
    async def create_task(request: TaskCreateRequest, background_tasks: BackgroundTasks) -> TaskCreateResponse:
        task_id = f"task-{uuid4()}"
        task = TaskRecord(
            task_id=task_id,
            goal=request.goal,
            workspace_path=request.workspace_path,
            mode=request.mode,
            budget=request.budget,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await store.create(task)
        background_tasks.add_task(orchestrator.run_task, task_id)
        return TaskCreateResponse(task_id=task_id)

    @router.get("/tasks/{task_id}", response_model=TaskView)
    async def get_task(task_id: str) -> TaskView:
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return TaskView(
            task_id=task.task_id,
            goal=task.goal,
            status=task.status,
            modified_files=task.modified_files,
            diagnostics=task.diagnostics,
        )

    @router.get("/tasks/{task_id}/result", response_model=TaskResult)
    async def get_task_result(task_id: str) -> TaskResult:
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return _to_task_result(task)

    @router.get("/tasks/{task_id}/artifacts", response_model=TaskArtifactsResponse)
    async def get_task_artifacts(task_id: str) -> TaskArtifactsResponse:
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return TaskArtifactsResponse(
            task_id=task.task_id,
            artifacts_root_path=task.artifacts_root_path,
            entries=_list_task_artifacts(task),
        )

    @router.post("/tasks/{task_id}/cancel", response_model=TaskView)
    async def cancel_task(task_id: str) -> TaskView:
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}:
            task = transition(task, TaskStatus.ABORTED, "cancelled by user")
        await workspace_manager.cleanup(task)
        task.shadow_workspace_path = None
        await store.save(task)
        await workspace_manager.prune_checkpoints()

        return TaskView(
            task_id=task.task_id,
            goal=task.goal,
            status=task.status,
            modified_files=task.modified_files,
            diagnostics=task.diagnostics,
        )

    @router.post("/tasks/{task_id}/accept", response_model=TaskResult)
    async def accept_patch(task_id: str) -> TaskResult:
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if task.status != TaskStatus.READY_FOR_REVIEW:
            msg = f"Task {task_id} is not in READY_FOR_REVIEW state"
            raise HTTPException(status_code=409, detail=msg)

        task = transition(task, TaskStatus.PROMOTING, "promotion started")
        await store.save(task)

        try:
            await workspace_manager.promote(task)
            await workspace_manager.cleanup(task)
        except Exception as exc:
            task.diagnostics.append(
                Diagnostic(source="promotion", message=str(exc), level="error")
            )
            task = transition(task, TaskStatus.FAILED, "promotion failed")
            await store.save(task)
            raise HTTPException(status_code=500, detail=f"Promotion failed: {exc}") from exc

        task.shadow_workspace_path = None
        task.promoted_at = datetime.now(timezone.utc)
        task = transition(task, TaskStatus.SUCCEEDED, "promotion completed")
        await store.save(task)
        await workspace_manager.prune_checkpoints()

        return _to_task_result(task)

    @router.post("/tasks/{task_id}/reject", response_model=TaskResult)
    async def reject_patch(task_id: str, request: RejectPatchRequest) -> TaskResult:
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        if task.status != TaskStatus.READY_FOR_REVIEW:
            msg = f"Task {task_id} is not in READY_FOR_REVIEW state"
            raise HTTPException(status_code=409, detail=msg)

        await workspace_manager.cleanup(task)
        task.shadow_workspace_path = None
        task = transition(task, TaskStatus.ABORTED, f"patch rejected: {request.reason}")
        await store.save(task)
        await workspace_manager.prune_checkpoints()

        return _to_task_result(task)

    return router
