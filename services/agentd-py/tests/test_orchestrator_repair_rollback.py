from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import (
    Diagnostic,
    TaskBudget,
    TaskRecord,
    TaskStatus,
    ValidationResult,
)
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class RepairReasoningEngine:
    def __init__(self) -> None:
        self.patch_calls = 0

    async def create_plan(
        self,
        task: TaskRecord,
        workspace_path: str,
        retrieval_context: dict[str, object],
    ) -> object:
        _ = (task, workspace_path, retrieval_context)
        return {
            "analysis": "Insert a marker line after class declaration.",
            "steps": [
                {
                    "id": "S1",
                    "goal": "Insert marker",
                    "targets": ["src/example.py"],
                    "risk": "low",
                }
            ],
            "expected_files": ["src/example.py"],
            "stop_conditions": ["validation passes"],
        }

    async def create_patch(
        self,
        task: TaskRecord,
        workspace_path: str,
        diagnostics: list[Diagnostic],
        retrieval_context: dict[str, object],
    ) -> object:
        _ = (task, workspace_path, diagnostics, retrieval_context)
        self.patch_calls += 1
        return {
            "patch_ops": [
                {
                    "op": "insert_after_symbol",
                    "file": "src/example.py",
                    "anchor": {"symbol": "class X"},
                    "content": "    injected = True",
                    "reason": "repair rollback regression test",
                }
            ]
        }


class FailOnceValidator:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, workspace_path: str) -> ValidationResult:
        _ = workspace_path
        self.calls += 1
        if self.calls == 1:
            return ValidationResult(
                success=False,
                diagnostics=[
                    Diagnostic(source="validator", message="intentional first failure", level="error")
                ],
                duration_ms=1,
            )

        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


@pytest.mark.asyncio
async def test_orchestrator_rolls_back_failed_repair_iteration(tmp_path: Path) -> None:
    real_workspace = tmp_path / "real"
    (real_workspace / "src").mkdir(parents=True)
    target = real_workspace / "src/example.py"
    target.write_text("class X:\n    pass\n", encoding="utf-8")

    store = InMemoryTaskStore()
    task = TaskRecord(
        task_id="task-repair-rollback",
        goal="Insert marker",
        workspace_path=str(real_workspace),
        budget=TaskBudget(max_iterations=3),
    )
    await store.create(task)

    reasoner = RepairReasoningEngine()
    validator = FailOnceValidator()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=reasoner,
        validator=validator,
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )

    result = await orchestrator.run_task(task.task_id)

    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert reasoner.patch_calls == 2
    assert validator.calls == 2
    assert any(event.to_status == TaskStatus.REPAIRING for event in result.events)
    assert result.shadow_workspace_path is not None

    shadow_target = Path(result.shadow_workspace_path) / "src/example.py"
    content = shadow_target.read_text(encoding="utf-8")
    assert content.count("injected = True") == 1
