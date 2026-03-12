from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import Diagnostic, TaskRecord, TaskStatus, ValidationResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class PassValidator:
    async def run_touched(self, workspace_path: str, touched_files: list[str]) -> ValidationResult:
        _ = (workspace_path, touched_files)
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)

    async def run(self, workspace_path: str) -> ValidationResult:
        _ = workspace_path
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


class ReplanningReasoner:
    def __init__(self) -> None:
        self.plan_calls = 0
        self.plan_contexts: list[dict[str, object]] = []

    async def create_plan(
        self,
        task: TaskRecord,
        workspace_path: str,
        retrieval_context: dict[str, object],
    ) -> object:
        _ = (task, workspace_path)
        self.plan_calls += 1
        self.plan_contexts.append(dict(retrieval_context))
        if self.plan_calls == 1:
            return {
                "analysis": "bad first plan",
                "steps": [
                    {
                        "id": "S1",
                        "goal": "Update endpoint",
                        "targets": ["agentd/api/tasks.py"],
                        "risk": "low",
                    }
                ],
                "expected_files": ["agentd/api/tasks.py"],
                "stop_conditions": ["tests pass"],
            }
        return {
            "analysis": "fixed plan",
            "steps": [
                {
                    "id": "S1",
                    "goal": "Update endpoint",
                    "targets": ["src/example.py"],
                    "risk": "low",
                }
            ],
            "expected_files": ["src/example.py"],
            "stop_conditions": ["tests pass"],
        }

    async def create_patch(
        self,
        task: TaskRecord,
        workspace_path: str,
        diagnostics: list[Diagnostic],
        retrieval_context: dict[str, object],
        **kwargs: object,
    ) -> object:
        _ = (task, workspace_path, diagnostics, retrieval_context, kwargs)
        return {
            "candidates": [
                {
                    "candidate_id": "c1",
                    "patch_ops": [
                        {
                            "op": "replace_node",
                            "file": "src/example.py",
                            "language": "python",
                            "selector": {"kind": "symbol", "value": "X", "match": "exact"},
                            "content": "class X:\n    pass\n    updated = True\n",
                            "reason": "apply update",
                        }
                    ],
                }
            ]
        }


class AlwaysBadReasoner(ReplanningReasoner):
    async def create_plan(
        self,
        task: TaskRecord,
        workspace_path: str,
        retrieval_context: dict[str, object],
    ) -> object:
        _ = (task, workspace_path)
        self.plan_calls += 1
        self.plan_contexts.append(dict(retrieval_context))
        return {
            "analysis": "bad plan",
            "steps": [
                {
                    "id": "S1",
                    "goal": "Update endpoint",
                    "targets": ["agentd/api/tasks.py"],
                    "risk": "low",
                }
            ],
            "expected_files": ["agentd/api/tasks.py"],
            "stop_conditions": ["tests pass"],
        }


@pytest.mark.asyncio
async def test_orchestrator_replans_when_plan_targets_missing(tmp_path: Path) -> None:
    real_workspace = tmp_path / "real"
    (real_workspace / "src").mkdir(parents=True)
    (real_workspace / "src/example.py").write_text("class X:\n    pass\n", encoding="utf-8")

    store = InMemoryTaskStore()
    task = TaskRecord(
        task_id="task-replan",
        goal="Update endpoint behavior",
        workspace_path=str(real_workspace),
    )
    await store.create(task)

    reasoner = ReplanningReasoner()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=reasoner,
        validator=PassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )

    result = await orchestrator.run_task(task.task_id)

    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert reasoner.plan_calls == 2
    assert "plan_validation_feedback" in reasoner.plan_contexts[1]
    feedback = reasoner.plan_contexts[1]["plan_validation_feedback"]
    assert isinstance(feedback, dict)
    missing_targets = feedback["missing_targets"]
    assert isinstance(missing_targets, list)
    assert missing_targets[0]["target"] == "agentd/api/tasks.py"


@pytest.mark.asyncio
async def test_orchestrator_fails_fast_when_replanned_targets_still_missing(tmp_path: Path) -> None:
    real_workspace = tmp_path / "real"
    (real_workspace / "src").mkdir(parents=True)
    (real_workspace / "src/example.py").write_text("class X:\n    pass\n", encoding="utf-8")

    store = InMemoryTaskStore()
    task = TaskRecord(
        task_id="task-replan-fail",
        goal="Update endpoint behavior",
        workspace_path=str(real_workspace),
    )
    await store.create(task)

    reasoner = AlwaysBadReasoner()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=reasoner,
        validator=PassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )

    result = await orchestrator.run_task(task.task_id)

    assert result.status == TaskStatus.FAILED
    assert reasoner.plan_calls == 2
    assert any(d.source == "plan_target_validation" for d in result.diagnostics)
