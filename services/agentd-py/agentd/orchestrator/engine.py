from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agentd.domain.models import (
    CandidateScoreBreakdown,
    CheckpointManifest,
    Diagnostic,
    PatchCandidateV2,
    PatchDocumentV2,
    PatchFailureCode,
    PatchPreflightIssue,
    PlanStep,
    PlanDocument,
    StepExecutionTrace,
    TaskRecord,
    TaskStatus,
    ValidationResult,
)
from agentd.domain.state_machine import assert_budget, bump_usage, transition
from agentd.patch.engine import PatchEngine
from agentd.reasoning.contracts import ReasoningEngine
from agentd.retrieval.artifact_client import RetrievalContext
from agentd.storage.base import TaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CandidateEvaluation:
    candidate: PatchCandidateV2
    score: float
    breakdown: CandidateScoreBreakdown
    preflight_issues: list[PatchPreflightIssue]
    validation: ValidationResult | None
    touched_files: list[str]
    changed_lines: int
    new_file_count: int
    preflight_report_path: str | None
    validation_report_path: str | None

    @property
    def preflight_pass(self) -> bool:
        return self.breakdown.preflight_pass

    @property
    def validation_pass(self) -> bool:
        return self.breakdown.validation_pass


class Validator(Protocol):
    async def run(self, workspace_path: str) -> ValidationResult: ...


class RetrievalClient(Protocol):
    def load_context(
        self,
        workspace_path: str,
        goal: str,
    ) -> tuple[RetrievalContext, list[Diagnostic]]: ...


class NullRetrievalClient:
    def load_context(
        self,
        workspace_path: str,
        goal: str,
    ) -> tuple[RetrievalContext, list[Diagnostic]]:
        _ = (workspace_path, goal)
        return RetrievalContext.empty(), []


class AgentOrchestrator:
    def __init__(
        self,
        store: TaskStore,
        reasoning_engine: ReasoningEngine,
        validator: Validator,
        patch_engine: PatchEngine,
        workspace_manager: ShadowWorkspaceManager,
        retrieval_client: RetrievalClient | None = None,
        max_attempts_per_step: int = 3,
        step_scoped_mode: bool = True,
        patch_candidate_count: int = 3,
    ) -> None:
        self._store = store
        self._reasoning_engine = reasoning_engine
        self._validator = validator
        self._patch_engine = patch_engine
        self._workspace_manager = workspace_manager
        self._retrieval_client = retrieval_client or NullRetrievalClient()
        self._max_attempts_per_step = max(1, max_attempts_per_step)
        self._step_scoped_mode = step_scoped_mode
        self._patch_candidate_count = max(1, patch_candidate_count)

    async def run_task(self, task_id: str) -> TaskRecord:
        task = await self._store.get(task_id)
        started_at_ms = int(time.time() * 1000)
        retrieval_context = RetrievalContext.empty()
        persistent_diagnostics: list[Diagnostic] = []
        task.artifacts_root_path = str(self._artifacts_root(task.task_id))

        try:
            task = transition(task, TaskStatus.CONTEXT_READY, "context assembled")
            await self._store.save(task)

            shadow_workspace = await self._workspace_manager.prepare(task.task_id, task.workspace_path)
            task.shadow_workspace_path = str(shadow_workspace.shadow_path)
            await self._store.save(task)

            retrieval_context, retrieval_warnings = self._retrieval_client.load_context(
                task.workspace_path,
                task.goal,
            )
            workspace_files_index = self._collect_workspace_file_index(
                Path(shadow_workspace.shadow_path)
            )
            workspace_files_set = set(workspace_files_index)
            plan_context_payload = retrieval_context.as_prompt_payload()
            plan_context_payload["workspace_files_index"] = workspace_files_index
            persistent_diagnostics = retrieval_warnings
            task.diagnostics = [*persistent_diagnostics]
            await self._store.save(task)

            plan_raw = await self._reasoning_engine.create_plan(
                task,
                str(shadow_workspace.shadow_path),
                plan_context_payload,
            )
            task.plan = PlanDocument.model_validate(plan_raw)
            unresolved_targets = self._find_unresolved_plan_targets(
                task.plan,
                workspace_files_set,
                workspace_files_index,
            )
            if unresolved_targets:
                plan_context_payload["plan_validation_feedback"] = {
                    "missing_targets": [
                        {
                            "step_id": item["step_id"],
                            "target": item["target"],
                            "suggestion": item["suggestion"],
                        }
                        for item in unresolved_targets
                    ],
                    "rule": (
                        "Use existing paths from workspace_files_index unless the step explicitly "
                        "creates a new tests/docs file."
                    ),
                }
                self._write_debug_artifact(
                    task.task_id,
                    "plan-feedback",
                    plan_context_payload["plan_validation_feedback"],
                )
                replanned_raw = await self._reasoning_engine.create_plan(
                    task,
                    str(shadow_workspace.shadow_path),
                    plan_context_payload,
                )
                task.plan = PlanDocument.model_validate(replanned_raw)
                unresolved_targets = self._find_unresolved_plan_targets(
                    task.plan,
                    workspace_files_set,
                    workspace_files_index,
                )
                plan_raw = replanned_raw

            if unresolved_targets:
                task.diagnostics = [
                    *persistent_diagnostics,
                    *self._plan_target_diagnostics(unresolved_targets),
                ]
                task = transition(task, TaskStatus.FAILED, "plan targets unresolved")
                await self._store.save(task)
                self._write_debug_artifact(
                    task.task_id,
                    "plan-unresolved-targets",
                    {"unresolved_targets": unresolved_targets},
                )
                return task

            task = transition(task, TaskStatus.PLANNED, "plan accepted")
            await self._store.save(task)
            self._write_debug_artifact(task.task_id, "plan", {"plan": plan_raw})

            if not self._step_scoped_mode:
                msg = "single-shot mode is deprecated; set AI_EDITOR_STEP_SCOPED_MODE=1"
                task.diagnostics = [
                    *persistent_diagnostics,
                    Diagnostic(source="orchestrator", message=msg, level="error"),
                ]
                task = transition(task, TaskStatus.FAILED, "step-scoped mode required")
                await self._store.save(task)
                return task

            shadow_path = Path(shadow_workspace.shadow_path)
            if task.plan is None:
                task = transition(task, TaskStatus.FAILED, "plan missing")
                await self._store.save(task)
                return task

            for step in task.plan.steps:
                if step.id in task.completed_step_ids:
                    continue
                succeeded = await self._run_step_with_retries(
                    task,
                    step,
                    shadow_path,
                    retrieval_context,
                    persistent_diagnostics,
                    started_at_ms,
                    full_validation=False,
                )
                await self._store.save(task)
                if not succeeded:
                    task = transition(task, TaskStatus.FAILED, "step execution exhausted")
                    await self._store.save(task)
                    return task

            task = transition(task, TaskStatus.VALIDATING, "full validation started")
            await self._store.save(task)
            validation = await self._validator.run(str(shadow_workspace.shadow_path))
            self._write_debug_artifact(
                task.task_id,
                "full-validation",
                validation.model_dump(mode="json"),
            )
            if validation.success:
                task.diagnostics = [*persistent_diagnostics]
                task = transition(
                    task,
                    TaskStatus.READY_FOR_REVIEW,
                    "validation passed; ready for review",
                )
                await self._store.save(task)
                return task

            retry_validation = await self._validator.run(str(shadow_workspace.shadow_path))
            self._write_debug_artifact(
                task.task_id,
                "full-validation-retry",
                retry_validation.model_dump(mode="json"),
            )
            if retry_validation.success:
                task.diagnostics = [*persistent_diagnostics]
                task = transition(
                    task,
                    TaskStatus.READY_FOR_REVIEW,
                    "validation passed on retry; ready for review",
                )
                await self._store.save(task)
                return task

            task.diagnostics = [*persistent_diagnostics, *validation.diagnostics]
            task = transition(task, TaskStatus.REPAIRING, "full validation failed")
            await self._store.save(task)

            repair_targets = task.modified_files or task.plan.expected_files
            repair_step = PlanStep(
                id="repair-full-validation",
                goal="Repair files failing full validation",
                targets=repair_targets,
                risk="med",
            )
            repaired = await self._run_step_with_retries(
                task,
                repair_step,
                shadow_path,
                retrieval_context,
                persistent_diagnostics,
                started_at_ms,
                full_validation=True,
                last_failure={
                    "failure_code": PatchFailureCode.APPLY_ERROR,
                    "file": None,
                    "op_id": None,
                    "excerpt": "\n".join(d.message for d in validation.diagnostics[:2]),
                },
            )
            await self._store.save(task)
            if not repaired:
                task = transition(task, TaskStatus.FAILED, "repair budget exhausted")
                await self._store.save(task)
                return task

            if task.status != TaskStatus.VALIDATING:
                task = transition(task, TaskStatus.VALIDATING, "final validation after repair")
                await self._store.save(task)
            task = transition(task, TaskStatus.READY_FOR_REVIEW, "validation passed; ready for review")
            await self._store.save(task)
            return task
        except Exception as exc:
            logger.exception(
                "Unhandled orchestrator failure",
                extra={
                    "task_id": task.task_id,
                    "workspace_path": task.workspace_path,
                    "status": task.status.value,
                },
            )
            if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}:
                task.diagnostics.append(
                    Diagnostic(source="orchestrator", message=str(exc), level="error")
                )
                try:
                    task = transition(task, TaskStatus.FAILED, "unhandled orchestrator error")
                except ValueError:
                    pass
            await self._store.save(task)
            return task
        finally:
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}:
                try:
                    await self._workspace_manager.prune_checkpoints()
                except Exception:
                    logger.exception(
                        "Checkpoint pruning failed",
                        extra={"task_id": task.task_id},
                    )

    async def _run_step_with_retries(
        self,
        task: TaskRecord,
        step: PlanStep,
        shadow_path: Path,
        retrieval_context: RetrievalContext,
        persistent_diagnostics: list[Diagnostic],
        started_at_ms: int,
        *,
        full_validation: bool,
        last_failure: dict[str, object] | None = None,
    ) -> bool:
        allowed_files = sorted(set(step.targets))
        if not allowed_files:
            allowed_files = [*task.modified_files] or [*task.plan.expected_files]
        max_files = max(1, min(task.budget.max_files_touched, len(allowed_files)))
        max_ops = max(1, min(12, max_files * 3))
        allowed_files_set = set(allowed_files)

        for attempt in range(1, self._max_attempts_per_step + 1):
            logger.info(
                "Step attempt started",
                extra={
                    "task_id": task.task_id,
                    "step_id": step.id,
                    "attempt": attempt,
                    "max_attempts_per_step": self._max_attempts_per_step,
                },
            )
            assert_budget(task, started_at_ms, int(time.time() * 1000))
            task = bump_usage(task)
            await self._store.save(task)

            if task.status == TaskStatus.PLANNED:
                task = transition(task, TaskStatus.REPAIRING, f"step {step.id} attempt {attempt} started")
                await self._store.save(task)

            checkpoint = self._create_shadow_checkpoint(
                task,
                step,
                attempt,
                shadow_path,
                tracked_files=allowed_files,
            )
            previous_modified_files = list(task.modified_files)
            try:
                patching_context_payload = retrieval_context.as_prompt_payload()
                patching_context_payload["file_contents"] = self._collect_file_contents(
                    shadow_path,
                    allowed_files,
                )
                self._write_debug_artifact(
                    task.task_id,
                    "patch-context",
                    patching_context_payload,
                    step_id=step.id,
                    attempt=attempt,
                )

                patch_raw = await self._create_patch_document(
                    task,
                    str(shadow_path),
                    task.diagnostics,
                    patching_context_payload,
                    current_step=step,
                    allowed_files=allowed_files,
                    max_ops=max_ops,
                    max_files=max_files,
                    candidate_count=self._patch_candidate_count,
                    last_failure=last_failure,
                )
                self._write_debug_artifact(
                    task.task_id,
                    "patch",
                    patch_raw,
                    step_id=step.id,
                    attempt=attempt,
                )
                patch_document = PatchDocumentV2.model_validate(patch_raw)
                task.latest_patch_v2 = patch_document
                task.latest_patch = None

                evaluations, ranking_path = await self._evaluate_candidates(
                    task=task,
                    step=step,
                    attempt=attempt,
                    patch_document=patch_document,
                    shadow_path=shadow_path,
                    checkpoint=checkpoint,
                    allowed_files=allowed_files_set,
                    max_ops=max_ops,
                    max_files=max_files,
                    full_validation=full_validation,
                )

                selected = self._select_best_candidate(evaluations)
                if selected is None:
                    issue = PatchPreflightIssue(
                        code=PatchFailureCode.APPLY_ERROR,
                        file=None,
                        message="No patch candidates were generated",
                    )
                    last_failure = self._last_failure_from_issues([issue])
                    task.execution_trace.append(
                        StepExecutionTrace(
                            step_id=step.id,
                            attempt=attempt,
                            status="preflight_failed",
                            issues=[issue],
                            message="no patch candidates",
                            checkpoint_id=checkpoint.checkpoint_id,
                        )
                    )
                    task.diagnostics = [*persistent_diagnostics, *self._issues_to_diagnostics([issue])]
                    self._restore_shadow_checkpoint(shadow_path, checkpoint.checkpoint_path)
                    task.modified_files = previous_modified_files
                    self._append_checkpoint(task, checkpoint)
                    task = transition(task, TaskStatus.REPAIRING, f"step {step.id} no candidates")
                    await self._store.save(task)
                    continue

                checkpoint.ranking_report_path = ranking_path
                checkpoint.candidate_id = selected.candidate.candidate_id
                checkpoint.preflight_report_path = selected.preflight_report_path
                checkpoint.validation_report_path = selected.validation_report_path
                task.selected_candidate_id = selected.candidate.candidate_id

                self._restore_shadow_checkpoint(shadow_path, checkpoint.checkpoint_path)
                final_preflight = await self._patch_engine.preflight_patch_candidate(
                    shadow_path,
                    selected.candidate,
                    allowed_files=allowed_files_set,
                )
                if not final_preflight.success:
                    failure_code = final_preflight.issues[0].code.value if final_preflight.issues else "unknown"
                    logger.warning(
                        "Step preflight rejected",
                        extra={
                            "task_id": task.task_id,
                            "step_id": step.id,
                            "attempt": attempt,
                            "result": "preflight_failed",
                            "failure_code": failure_code,
                        },
                    )
                    last_failure = self._last_failure_from_issues(final_preflight.issues)
                    task.execution_trace.append(
                        StepExecutionTrace(
                            step_id=step.id,
                            attempt=attempt,
                            status="preflight_failed",
                            candidate_id=selected.candidate.candidate_id,
                            checkpoint_id=checkpoint.checkpoint_id,
                            issues=final_preflight.issues,
                            score=selected.score,
                            message="selected candidate preflight failed",
                        )
                    )
                    task.diagnostics = [
                        *persistent_diagnostics,
                        *self._issues_to_diagnostics(final_preflight.issues),
                    ]
                    task.modified_files = previous_modified_files
                    self._append_checkpoint(task, checkpoint)
                    task = transition(task, TaskStatus.REPAIRING, f"step {step.id} preflight failed")
                    await self._store.save(task)
                    continue

                patch_result = await self._patch_engine.apply_patch_candidate(
                    shadow_path,
                    selected.candidate,
                    allowed_files=allowed_files_set,
                )
                touched = patch_result.touched_files
                task.modified_files = sorted({*task.modified_files, *touched})
                task.execution_trace.append(
                    StepExecutionTrace(
                        step_id=step.id,
                        attempt=attempt,
                        status="patch_applied",
                        candidate_id=selected.candidate.candidate_id,
                        checkpoint_id=checkpoint.checkpoint_id,
                        score=selected.score,
                        preflight_summary={"success": True},
                        message="selected candidate applied",
                    )
                )
                task = transition(task, TaskStatus.PATCHED, f"step {step.id} patch applied")
                await self._store.save(task)

                task = transition(task, TaskStatus.VALIDATING, f"step {step.id} validation started")
                await self._store.save(task)
                if full_validation:
                    validation = await self._validator.run(str(shadow_path))
                else:
                    validation = await self._run_fast_validation(str(shadow_path), touched)
                validation_path = self._write_debug_artifact(
                    task.task_id,
                    "validation-selected",
                    validation.model_dump(mode="json"),
                    step_id=step.id,
                    attempt=attempt,
                )
                checkpoint.validation_report_path = validation_path or checkpoint.validation_report_path
                checkpoint.file_hashes_after = self._hash_files(
                    shadow_path,
                    tracked_files=allowed_files,
                )
                self._append_checkpoint(task, checkpoint)

                if validation.success:
                    logger.info(
                        "Step completed",
                        extra={
                            "task_id": task.task_id,
                            "step_id": step.id,
                            "attempt": attempt,
                            "result": "step_completed",
                        },
                    )
                    if step.id not in task.completed_step_ids:
                        task.completed_step_ids.append(step.id)
                    task.execution_trace.append(
                        StepExecutionTrace(
                            step_id=step.id,
                            attempt=attempt,
                            status="step_completed",
                            candidate_id=selected.candidate.candidate_id,
                            checkpoint_id=checkpoint.checkpoint_id,
                            score=selected.score,
                            preflight_summary=selected.breakdown.model_dump(mode="json"),
                            validation_summary=validation.model_dump(mode="json"),
                            message="step validation passed",
                            artifacts={
                                "ranking": ranking_path or "",
                                "preflight": selected.preflight_report_path or "",
                                "validation": checkpoint.validation_report_path or "",
                            },
                        )
                    )
                    task.diagnostics = [*persistent_diagnostics]
                    task = transition(task, TaskStatus.PLANNED, f"step {step.id} completed")
                    await self._store.save(task)
                    return True

                last_failure = {
                    "failure_code": PatchFailureCode.APPLY_ERROR.value,
                    "file": None,
                    "op_id": None,
                    "excerpt": "\n".join(d.message for d in validation.diagnostics[:2]),
                }
                logger.warning(
                    "Step validation failed",
                    extra={
                        "task_id": task.task_id,
                        "step_id": step.id,
                        "attempt": attempt,
                        "result": "validation_failed",
                        "failure_code": PatchFailureCode.APPLY_ERROR.value,
                    },
                )
                task.execution_trace.append(
                    StepExecutionTrace(
                        step_id=step.id,
                        attempt=attempt,
                        status="validation_failed",
                        candidate_id=selected.candidate.candidate_id,
                        checkpoint_id=checkpoint.checkpoint_id,
                        score=selected.score,
                        validation_summary=validation.model_dump(mode="json"),
                        message="selected candidate validation failed",
                    )
                )
                task.diagnostics = [*persistent_diagnostics, *validation.diagnostics]
                self._restore_shadow_checkpoint(shadow_path, checkpoint.checkpoint_path)
                task.modified_files = previous_modified_files
                self._append_checkpoint(task, checkpoint)
                task = transition(task, TaskStatus.REPAIRING, f"step {step.id} validation failed")
                await self._store.save(task)
            except Exception as exc:
                logger.exception(
                    "Iteration failed while applying/validating patch",
                    extra={
                        "task_id": task.task_id,
                        "step_id": step.id,
                        "attempt": attempt,
                        "result": "validation_failed",
                        "failure_code": PatchFailureCode.APPLY_ERROR.value,
                    },
                )
                issue = PatchPreflightIssue(
                    code=PatchFailureCode.APPLY_ERROR,
                    file=None,
                    message=str(exc),
                )
                last_failure = self._last_failure_from_issues([issue])
                task.execution_trace.append(
                    StepExecutionTrace(
                        step_id=step.id,
                        attempt=attempt,
                        status="validation_failed",
                        issues=[issue],
                        checkpoint_id=checkpoint.checkpoint_id,
                        message="internal apply/validation error",
                    )
                )
                task.diagnostics = [*persistent_diagnostics, *self._issues_to_diagnostics([issue])]
                self._restore_shadow_checkpoint(shadow_path, checkpoint.checkpoint_path)
                task.modified_files = previous_modified_files
                self._append_checkpoint(task, checkpoint)
                await self._store.save(task)

        task.execution_trace.append(
            StepExecutionTrace(
                step_id=step.id,
                attempt=self._max_attempts_per_step,
                status="step_exhausted",
                message="step attempts exhausted",
            )
        )
        logger.error(
            "Step attempts exhausted",
            extra={
                "task_id": task.task_id,
                "step_id": step.id,
                "attempt": self._max_attempts_per_step,
                "result": "step_exhausted",
            },
        )
        return False

    async def _run_fast_validation(
        self,
        workspace_path: str,
        touched_files: list[str],
    ) -> ValidationResult:
        run_touched = getattr(self._validator, "run_touched", None)
        if callable(run_touched):
            return await run_touched(workspace_path, touched_files)

        diagnostics: list[Diagnostic] = []
        root = Path(workspace_path)
        for rel in touched_files:
            candidate = root / rel
            if candidate.suffix != ".py" or not candidate.exists():
                continue
            try:
                source = candidate.read_text(encoding="utf-8")
                compile(source, str(candidate), "exec")
            except Exception as exc:
                diagnostics.append(
                    Diagnostic(
                        source="validator:fast-python-compile",
                        message=f"{candidate}: {exc}",
                        level="error",
                    )
                )
        return ValidationResult(
            success=not diagnostics,
            diagnostics=diagnostics,
            duration_ms=0,
        )

    def _collect_file_contents(self, shadow_path: Path, allowed_files: list[str]) -> dict[str, str]:
        contents: dict[str, str] = {}
        for rel_path in allowed_files:
            abs_path = shadow_path / rel_path
            if not abs_path.exists() or not abs_path.is_file():
                continue
            try:
                contents[rel_path] = abs_path.read_text(encoding="utf-8")
            except OSError:
                continue
        return contents

    async def _create_patch_document(
        self,
        task: TaskRecord,
        workspace_path: str,
        diagnostics: list[Diagnostic],
        retrieval_context: dict[str, object],
        *,
        current_step: PlanStep | None,
        allowed_files: list[str],
        max_ops: int,
        max_files: int,
        candidate_count: int,
        last_failure: dict[str, object] | None,
    ) -> object:
        return await self._reasoning_engine.create_patch(
            task,
            workspace_path,
            diagnostics,
            retrieval_context,
            current_step=current_step,
            allowed_files=allowed_files,
            max_ops=max_ops,
            max_files=max_files,
            candidate_count=candidate_count,
            last_failure=last_failure,
        )

    async def _evaluate_candidates(
        self,
        *,
        task: TaskRecord,
        step: PlanStep,
        attempt: int,
        patch_document: PatchDocumentV2,
        shadow_path: Path,
        checkpoint: CheckpointManifest,
        allowed_files: set[str],
        max_ops: int,
        max_files: int,
        full_validation: bool,
    ) -> tuple[list[_CandidateEvaluation], str | None]:
        evaluations: list[_CandidateEvaluation] = []
        for candidate in patch_document.candidates:
            self._restore_shadow_checkpoint(shadow_path, checkpoint.checkpoint_path)
            evaluation = await self._evaluate_single_candidate(
                task=task,
                step=step,
                attempt=attempt,
                candidate=candidate,
                shadow_path=shadow_path,
                checkpoint=checkpoint,
                allowed_files=allowed_files,
                max_ops=max_ops,
                max_files=max_files,
                full_validation=full_validation,
            )
            evaluations.append(evaluation)

        selected = self._select_best_candidate(evaluations)
        selected_id = selected.candidate.candidate_id if selected else None
        ranking_payload = {
            "step_id": step.id,
            "attempt": attempt,
            "selected_candidate_id": selected_id,
            "candidates": [
                {
                    "candidate_id": item.candidate.candidate_id,
                    "score": item.score,
                    "preflight_pass": item.preflight_pass,
                    "validation_pass": item.validation_pass,
                    "changed_lines": item.changed_lines,
                    "touched_files": item.touched_files,
                    "new_file_count": item.new_file_count,
                    "issues": [issue.model_dump(mode="json") for issue in item.preflight_issues],
                    "selected": item.candidate.candidate_id == selected_id,
                }
                for item in evaluations
            ],
        }
        ranking_path = self._write_debug_artifact(
            task.task_id,
            "ranking",
            ranking_payload,
            step_id=step.id,
            attempt=attempt,
        )
        return evaluations, ranking_path

    async def _evaluate_single_candidate(
        self,
        *,
        task: TaskRecord,
        step: PlanStep,
        attempt: int,
        candidate: PatchCandidateV2,
        shadow_path: Path,
        checkpoint: CheckpointManifest,
        allowed_files: set[str],
        max_ops: int,
        max_files: int,
        full_validation: bool,
    ) -> _CandidateEvaluation:
        op_count = len(candidate.patch_ops)
        candidate_files = sorted({op.file for op in candidate.patch_ops})
        if op_count > max_ops or len(candidate_files) > max_files:
            issue = PatchPreflightIssue(
                code=PatchFailureCode.SCOPE_VIOLATION,
                file=candidate_files[0] if candidate_files else None,
                message=(
                    f"Candidate '{candidate.candidate_id}' exceeds limits: "
                    f"ops={op_count}/{max_ops}, files={len(candidate_files)}/{max_files}"
                ),
            )
            breakdown = self._score_candidate(
                preflight_pass=False,
                validation_pass=False,
                changed_lines=0,
                op_count=op_count,
                new_file_count=0,
            )
            return _CandidateEvaluation(
                candidate=candidate,
                score=breakdown.score,
                breakdown=breakdown,
                preflight_issues=[issue],
                validation=None,
                touched_files=[],
                changed_lines=0,
                new_file_count=0,
                preflight_report_path=None,
                validation_report_path=None,
            )

        preflight = await self._patch_engine.preflight_patch_candidate(
            shadow_path,
            candidate,
            allowed_files=allowed_files,
        )
        preflight_path = self._write_debug_artifact(
            task.task_id,
            f"preflight-{candidate.candidate_id}",
            preflight.model_dump(mode="json"),
            step_id=step.id,
            attempt=attempt,
        )
        if not preflight.success:
            breakdown = self._score_candidate(
                preflight_pass=False,
                validation_pass=False,
                changed_lines=0,
                op_count=op_count,
                new_file_count=0,
            )
            return _CandidateEvaluation(
                candidate=candidate,
                score=breakdown.score,
                breakdown=breakdown,
                preflight_issues=preflight.issues,
                validation=None,
                touched_files=[],
                changed_lines=0,
                new_file_count=0,
                preflight_report_path=preflight_path,
                validation_report_path=None,
            )

        try:
            patch_result = await self._patch_engine.apply_patch_candidate(
                shadow_path,
                candidate,
                allowed_files=allowed_files,
            )
        except Exception as exc:
            validation = ValidationResult(
                success=False,
                diagnostics=[
                    Diagnostic(
                        source="patch_apply",
                        message=str(exc),
                        level="error",
                    )
                ],
                duration_ms=0,
            )
            validation_path = self._write_debug_artifact(
                task.task_id,
                f"validation-{candidate.candidate_id}",
                validation.model_dump(mode="json"),
                step_id=step.id,
                attempt=attempt,
            )
            breakdown = self._score_candidate(
                preflight_pass=True,
                validation_pass=False,
                changed_lines=0,
                op_count=op_count,
                new_file_count=0,
            )
            return _CandidateEvaluation(
                candidate=candidate,
                score=breakdown.score,
                breakdown=breakdown,
                preflight_issues=[],
                validation=validation,
                touched_files=[],
                changed_lines=0,
                new_file_count=0,
                preflight_report_path=preflight_path,
                validation_report_path=validation_path,
            )

        touched_files = patch_result.touched_files
        if full_validation:
            validation = await self._validator.run(str(shadow_path))
        else:
            validation = await self._run_fast_validation(str(shadow_path), touched_files)
        validation_path = self._write_debug_artifact(
            task.task_id,
            f"validation-{candidate.candidate_id}",
            validation.model_dump(mode="json"),
            step_id=step.id,
            attempt=attempt,
        )

        checkpoint_snapshot = Path(checkpoint.checkpoint_path)
        changed_lines = self._count_changed_lines(
            checkpoint_snapshot,
            shadow_path,
            touched_files,
        )
        new_file_count = self._count_new_files(
            checkpoint_snapshot,
            shadow_path,
            touched_files,
        )
        breakdown = self._score_candidate(
            preflight_pass=True,
            validation_pass=validation.success,
            changed_lines=changed_lines,
            op_count=op_count,
            new_file_count=new_file_count,
        )
        return _CandidateEvaluation(
            candidate=candidate,
            score=breakdown.score,
            breakdown=breakdown,
            preflight_issues=[],
            validation=validation,
            touched_files=touched_files,
            changed_lines=changed_lines,
            new_file_count=new_file_count,
            preflight_report_path=preflight_path,
            validation_report_path=validation_path,
        )

    def _select_best_candidate(
        self,
        evaluations: list[_CandidateEvaluation],
    ) -> _CandidateEvaluation | None:
        if not evaluations:
            return None

        def sort_key(item: _CandidateEvaluation) -> tuple[float, int, int, str]:
            touched_files_count = len(item.touched_files) if item.touched_files else len(
                {op.file for op in item.candidate.patch_ops}
            )
            return (-item.score, touched_files_count, item.changed_lines, item.candidate.candidate_id)

        return sorted(evaluations, key=sort_key)[0]

    def _score_candidate(
        self,
        *,
        preflight_pass: bool,
        validation_pass: bool,
        changed_lines: int,
        op_count: int,
        new_file_count: int,
    ) -> CandidateScoreBreakdown:
        score = 0.0
        if preflight_pass:
            score += 100.0
        if validation_pass:
            score += 60.0
        score -= 0.05 * float(changed_lines)
        score -= 2.0 * float(op_count)
        score -= 5.0 * float(new_file_count)
        return CandidateScoreBreakdown(
            preflight_pass=preflight_pass,
            validation_pass=validation_pass,
            changed_lines=changed_lines,
            op_count=op_count,
            new_file_count=new_file_count,
            score=score,
        )

    def _count_changed_lines(
        self,
        checkpoint_snapshot: Path,
        shadow_path: Path,
        touched_files: list[str],
    ) -> int:
        changed = 0
        for rel in touched_files:
            before_path = checkpoint_snapshot / rel
            after_path = shadow_path / rel
            before = before_path.read_text(encoding="utf-8").splitlines() if before_path.exists() else []
            after = after_path.read_text(encoding="utf-8").splitlines() if after_path.exists() else []
            for line in difflib.ndiff(before, after):
                if line.startswith("+ ") or line.startswith("- "):
                    changed += 1
        return changed

    def _count_new_files(
        self,
        checkpoint_snapshot: Path,
        shadow_path: Path,
        touched_files: list[str],
    ) -> int:
        count = 0
        for rel in touched_files:
            if not (checkpoint_snapshot / rel).exists() and (shadow_path / rel).exists():
                count += 1
        return count

    def _issues_to_diagnostics(self, issues: list[PatchPreflightIssue]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        for issue in issues:
            diagnostics.append(
                Diagnostic(
                    source=f"patch_preflight:{issue.code.value}",
                    message=issue.message,
                    level="error",
                    file=issue.file,
                )
            )
        return diagnostics

    def _last_failure_from_issues(self, issues: list[PatchPreflightIssue]) -> dict[str, object] | None:
        if not issues:
            return None
        issue = issues[0]
        return {
            "failure_code": issue.code.value,
            "file": issue.file,
            "op_id": issue.op_index,
            "excerpt": issue.message,
        }

    def _artifacts_root(self, task_id: str) -> Path:
        return Path("/tmp/ai-editor-stress") / task_id

    def _write_debug_artifact(
        self,
        task_id: str,
        name: str,
        payload: object,
        *,
        step_id: str | None = None,
        attempt: int | None = None,
    ) -> str | None:
        try:
            root = self._artifacts_root(task_id)
            if step_id:
                root = root / f"step-{step_id}"
            if attempt is not None:
                root = root / f"attempt-{attempt}"
            root.mkdir(parents=True, exist_ok=True)
            output_path = root / f"{name}.json"
            output_path.write_text(
                json.dumps(payload, indent=2, default=str),
                encoding="utf-8",
            )
            return str(output_path)
        except Exception:
            logger.debug("failed to write debug artifact", exc_info=True)
            return None

    def _create_shadow_checkpoint(
        self,
        task: TaskRecord,
        step: PlanStep,
        attempt: int,
        shadow_path: Path,
        *,
        tracked_files: list[str],
    ) -> CheckpointManifest:
        checkpoint_id = f"{step.id}-{attempt}-{uuid4().hex[:8]}"
        checkpoint_root = shadow_path.parent / "_checkpoints" / task.task_id / f"step-{step.id}"
        attempt_root = checkpoint_root / f"attempt-{attempt}"
        snapshot_path = attempt_root / checkpoint_id / "shadow"
        if attempt_root.exists():
            shutil.rmtree(attempt_root)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(shadow_path, snapshot_path)

        return CheckpointManifest(
            task_id=task.task_id,
            step_id=step.id,
            attempt=attempt,
            checkpoint_id=checkpoint_id,
            checkpoint_path=str(snapshot_path),
            shadow_path=str(shadow_path),
            file_hashes_before=self._hash_files(shadow_path, tracked_files=tracked_files),
        )

    def _restore_shadow_checkpoint(self, shadow_path: Path, checkpoint_path: str) -> None:
        snapshot_path = Path(checkpoint_path)
        if shadow_path.exists():
            shutil.rmtree(shadow_path)
        shutil.copytree(snapshot_path, shadow_path)

    def _hash_files(
        self,
        root: Path,
        *,
        tracked_files: list[str],
    ) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for rel in sorted(set(tracked_files)):
            path = root / rel
            if not path.exists() or not path.is_file():
                hashes[rel] = "__missing__"
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[rel] = digest
        return hashes

    def _append_checkpoint(self, task: TaskRecord, checkpoint: CheckpointManifest) -> None:
        for item in task.checkpoints:
            if item.checkpoint_id == checkpoint.checkpoint_id:
                return
        task.checkpoints.append(checkpoint)

    def _collect_workspace_file_index(self, workspace_path: Path) -> list[str]:
        skip_dirs = {
            ".git",
            ".venv",
            "node_modules",
            "target",
            "dist",
            "__pycache__",
            ".agentd",
            ".ai-editor",
        }
        indexed: list[str] = []
        for root, dirs, files in os.walk(workspace_path):
            dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
            for file_name in sorted(files):
                relative = str((Path(root) / file_name).relative_to(workspace_path))
                indexed.append(relative)
                if len(indexed) >= 2000:
                    return indexed
        return indexed

    def _find_unresolved_plan_targets(
        self,
        plan: PlanDocument,
        workspace_files_set: set[str],
        workspace_files_index: list[str],
    ) -> list[dict[str, str | None]]:
        unresolved: list[dict[str, str | None]] = []
        for step in plan.steps:
            for target in step.targets:
                if target in workspace_files_set:
                    continue
                if self._allow_missing_plan_target(step, target):
                    continue
                unresolved.append(
                    {
                        "step_id": step.id,
                        "target": target,
                        "suggestion": self._suggest_workspace_path(target, workspace_files_index),
                    }
                )
        return unresolved

    def _allow_missing_plan_target(self, step: PlanStep, target: str) -> bool:
        target_lower = target.lower()
        goal_lower = step.goal.lower()
        create_intent = any(
            marker in goal_lower
            for marker in (
                "create",
                "new file",
                "add test",
                "write test",
                "generate",
            )
        )
        if create_intent:
            return True
        if target_lower.endswith(".md") and (
            target_lower.startswith("docs/")
            or target_lower.endswith("/readme.md")
            or target_lower == "readme.md"
        ):
            return True
        return False

    def _suggest_workspace_path(
        self,
        target: str,
        workspace_files_index: list[str],
    ) -> str | None:
        target_name = Path(target).name
        by_name = [candidate for candidate in workspace_files_index if Path(candidate).name == target_name]
        if len(by_name) == 1:
            return by_name[0]
        close = difflib.get_close_matches(target, workspace_files_index, n=1, cutoff=0.6)
        return close[0] if close else None

    def _plan_target_diagnostics(
        self,
        unresolved_targets: list[dict[str, str | None]],
    ) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        for item in unresolved_targets[:8]:
            target = item["target"] or "<unknown>"
            step_id = item["step_id"] or "<unknown>"
            suggestion = item["suggestion"]
            message = f"Plan step {step_id} references missing target: {target}"
            if suggestion:
                message += f" (did you mean: {suggestion})"
            diagnostics.append(
                Diagnostic(
                    source="plan_target_validation",
                    message=message,
                    level="error",
                    file=target,
                )
            )
        return diagnostics
