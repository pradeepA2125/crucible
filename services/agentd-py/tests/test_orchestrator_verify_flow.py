"""Integration tests for the two-phase ToolLoop verify flow."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import TaskRecord, TaskStatus, ValidationResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _make_plan_raw(test_command: str | None = None) -> dict:
    step: dict = {
        "id": "s1",
        "goal": "Create hello.py",
        "targets": [{"path": "hello.py", "intent": "new"}],
        "risk": "low",
    }
    if test_command:
        step["test_command"] = test_command
    return {
        "analysis": "test",
        "steps": [step],
        "expected_files": ["hello.py"],
        "stop_conditions": ["done"],
    }


def _make_patch_raw(content: str = 'print("hello")') -> dict:
    return {
        "candidates": [{
            "candidate_id": "c1",
            "patch_ops": [{"op": "create_file", "file": "hello.py", "content": content, "reason": "create"}],
        }]
    }


class _NullValidator:
    async def run(self, workspace_path: str) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


def _make_orchestrator(
    reasoning: ScriptedReasoningEngine,
    tmp_path: Path,
) -> tuple[AgentOrchestrator, InMemoryTaskStore]:
    store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        store=store,
        reasoning_engine=reasoning,
        validator=_NullValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        max_attempts_per_step=2,
    )
    return orchestrator, store


@pytest.mark.asyncio
async def test_null_test_command_always_enters_verify(tmp_path: Path) -> None:
    """Steps without test_command still enter verify phase — agent must emit verify_done."""
    ws = tmp_path / "ws"
    ws.mkdir()

    patch = _make_patch_raw()
    patch_ops = patch["candidates"][0]["patch_ops"]
    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(test_command=None),
        patches=[patch],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "create file", "patch_ops": patch_ops},
            {"type": "verify_done", "thought": "no tests applicable", "verified": True, "test_output": ""},
        ],
    )
    orchestrator, store = _make_orchestrator(reasoning, tmp_path)
    task = TaskRecord(task_id="task-1", goal="create hello.py", workspace_path=str(ws))
    await store.create(task)

    initialized = await orchestrator.run_task("task-1")
    assert initialized.status == TaskStatus.AWAITING_PLAN_APPROVAL
    result = await orchestrator.continue_task("task-1", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert "hello.py" in result.modified_files


@pytest.mark.asyncio
async def test_verify_done_true_completes_step(tmp_path: Path) -> None:
    """emit_patch + verify_done(verified=True) in tool_step_responses completes the step."""
    ws = tmp_path / "ws"
    ws.mkdir()

    patch = _make_patch_raw()
    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(test_command="pytest tests/test_hello.py"),
        patches=[patch],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "create file", "patch_ops": patch["candidates"][0]["patch_ops"]},
            {"type": "verify_done", "thought": "tests pass", "verified": True, "test_output": "1 passed"},
        ],
    )
    orchestrator, store = _make_orchestrator(reasoning, tmp_path)
    task = TaskRecord(task_id="task-2", goal="create", workspace_path=str(ws))
    await store.create(task)

    initialized = await orchestrator.run_task("task-2")
    assert initialized.status == TaskStatus.AWAITING_PLAN_APPROVAL
    result = await orchestrator.continue_task("task-2", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert "hello.py" in result.modified_files


@pytest.mark.asyncio
async def test_verify_done_false_triggers_retry(tmp_path: Path) -> None:
    """verify_done(verified=False) causes engine to restore checkpoint and retry the step."""
    ws = tmp_path / "ws"
    ws.mkdir()

    patch_ops = [{"op": "create_file", "file": "hello.py", "content": "x=1", "reason": "r"}]
    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(test_command="pytest tests/"),
        patches=[],
        tool_step_responses=[
            # Attempt 1: patch then verify fails
            {"type": "emit_patch", "thought": "attempt 1", "patch_ops": patch_ops},
            {"type": "verify_done", "thought": "failed", "verified": False, "test_output": "1 failed"},
            # Attempt 2: patch then verify passes
            {"type": "emit_patch", "thought": "attempt 2", "patch_ops": patch_ops},
            {"type": "verify_done", "thought": "ok", "verified": True, "test_output": "1 passed"},
        ],
    )
    orchestrator, store = _make_orchestrator(reasoning, tmp_path)
    task = TaskRecord(task_id="task-3", goal="create", workspace_path=str(ws))
    await store.create(task)

    initialized = await orchestrator.run_task("task-3")
    assert initialized.status == TaskStatus.AWAITING_PLAN_APPROVAL
    result = await orchestrator.continue_task("task-3", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert "hello.py" in result.modified_files


@pytest.mark.asyncio
async def test_patch_apply_failure_stays_in_explore(tmp_path: Path) -> None:
    """When emit_patch ops fail to apply, agent stays in explore phase and corrects."""
    ws = tmp_path / "ws"
    ws.mkdir()

    # search_replace on nonexistent file will raise from PatchEngine
    bad_ops = [{"op": "search_replace", "file": "nonexistent.py", "search": "x", "replace": "y", "reason": "bad"}]
    good_ops = [{"op": "create_file", "file": "hello.py", "content": "x=1", "reason": "correct"}]

    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(test_command=None),
        patches=[],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "bad patch", "patch_ops": bad_ops},
            # Agent sees failure in history, corrects:
            {"type": "emit_patch", "thought": "corrected", "patch_ops": good_ops},
            # After good patch, enter verify and complete
            {"type": "verify_done", "thought": "no tests", "verified": True, "test_output": ""},
        ],
    )
    orchestrator, store = _make_orchestrator(reasoning, tmp_path)
    task = TaskRecord(task_id="task-4", goal="create", workspace_path=str(ws))
    await store.create(task)

    initialized = await orchestrator.run_task("task-4")
    assert initialized.status == TaskStatus.AWAITING_PLAN_APPROVAL
    result = await orchestrator.continue_task("task-4", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
    assert "hello.py" in result.modified_files


@pytest.mark.asyncio
async def test_verify_context_message_contains_touched_files_and_strategy(tmp_path: Path) -> None:
    """Patch-apply context message includes touched_files and testing_strategy."""
    ws = tmp_path / "ws"
    ws.mkdir()

    captured_histories: list[list[dict]] = []

    class _CapturingEngine(ScriptedReasoningEngine):
        async def create_tool_step(
            self,
            step_context: dict,
            history: list[dict],
            tool_definitions: list[dict],
        ) -> dict:
            captured_histories.append(list(history))
            return await super().create_tool_step(step_context, history, tool_definitions)

    patch = _make_patch_raw()
    patch_ops = patch["candidates"][0]["patch_ops"]
    plan = _make_plan_raw(test_command=None)
    plan["steps"][0]["testing_strategy"] = "run vitest"

    reasoning = _CapturingEngine(
        plan=plan,
        patches=[patch],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "create", "patch_ops": patch_ops},
            {"type": "verify_done", "thought": "ok", "verified": True, "test_output": ""},
        ],
    )
    orchestrator, store = _make_orchestrator(reasoning, tmp_path)
    task = TaskRecord(task_id="task-ctx", goal="create", workspace_path=str(ws))
    await store.create(task)

    await orchestrator.run_task("task-ctx")
    await orchestrator.continue_task("task-ctx", feedback=None)

    # The verify-phase create_tool_step call receives a history containing the patch-apply
    # notification. Find it across all captured calls.
    patch_apply_msgs = [
        msg
        for history in captured_histories
        for msg in history
        if isinstance(msg.get("content"), str) and "Patch applied successfully" in msg["content"]
    ]
    assert patch_apply_msgs, "No patch-apply message found in any captured history"
    content = patch_apply_msgs[0]["content"]
    assert "hello.py" in content, f"touched file missing from verify context: {content}"
    assert "run vitest" in content, f"testing_strategy missing from verify context: {content}"


@pytest.mark.asyncio
async def test_verify_done_empty_output_accepted_when_no_test_command(tmp_path: Path) -> None:
    """verify_done(verified=True, test_output='') is valid when step has no test_command."""
    ws = tmp_path / "ws"
    ws.mkdir()

    patch = _make_patch_raw()
    patch_ops = patch["candidates"][0]["patch_ops"]
    reasoning = ScriptedReasoningEngine(
        plan=_make_plan_raw(test_command=None),
        patches=[patch],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "done", "patch_ops": patch_ops},
            {"type": "verify_done", "thought": "pure config, nothing to test", "verified": True, "test_output": ""},
        ],
    )
    orchestrator, store = _make_orchestrator(reasoning, tmp_path)
    task = TaskRecord(task_id="task-empty", goal="create", workspace_path=str(ws))
    await store.create(task)

    await orchestrator.run_task("task-empty")
    result = await orchestrator.continue_task("task-empty", feedback=None)
    assert result.status == TaskStatus.READY_FOR_REVIEW
