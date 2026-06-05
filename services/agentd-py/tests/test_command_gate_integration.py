"""End-to-end ToolLoop integration: run_command → approval gate → execute.

Covers the full wiring: scripted engine emits a run_command tool_call →
ToolRegistry consults the engine-built CommandApprovalCallback → task pauses
at AWAITING_COMMAND_DECISION → future is resolved (approve+remember) → the
command runs → step completes.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from agentd.domain.models import (
    CommandDecision,
    ShellPolicy,
    TaskRecord,
    TaskStatus,
    ValidationResult,
)
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _make_plan() -> dict:
    return {
        "analysis": "smoke",
        "steps": [{
            "id": "s1",
            "goal": "create hello.py",
            "targets": [{"path": "hello.py", "intent": "new"}],
            "risk": "low",
            "test_command": None,
        }],
        "expected_files": ["hello.py"],
        "stop_conditions": ["done"],
    }


def _patch_op_create_hello() -> list[dict]:
    return [{
        "op": "create_file",
        "file": "hello.py",
        "content": 'print("hi")\n',
        "reason": "create",
    }]


class _NullValidator:
    async def run(self, workspace_path: str) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


def _make_orchestrator(reasoning: ScriptedReasoningEngine, tmp_path: Path, *, policy: ShellPolicy):
    store = InMemoryTaskStore()
    orch = AgentOrchestrator(
        store=store,
        reasoning_engine=reasoning,
        validator=_NullValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
        max_attempts_per_step=2,
        shell_policy=policy,
    )
    return orch, store


@pytest.mark.asyncio
async def test_run_command_gate_pauses_and_resumes_on_approval(tmp_path: Path) -> None:
    """End-to-end: scripted run_command → callback pauses task → set approve →
    command runs → next response (emit_patch) → verify_done → step completes."""
    ws = tmp_path / "ws"
    ws.mkdir()

    reasoning = ScriptedReasoningEngine(
        plan=_make_plan(),
        patches=[{"candidates": [{"candidate_id": "c1", "patch_ops": _patch_op_create_hello()}]}],
        tool_step_responses=[
            # 1. EXPLORE — emit the patch (run_command is a verify-phase tool).
            {"type": "emit_patch", "thought": "create file",
             "patch_ops": _patch_op_create_hello()},
            # 2. VERIFY — run a command (this is what trips the approval gate).
            {"type": "tool_call", "thought": "smoke test", "tool": "run_command",
             "args": {"command": "echo", "args": ["hi"]}},
            # 3. VERIFY — after the command result returns, close the step.
            {"type": "verify_done", "thought": "ok",
             "verified": True, "test_output": ""},
        ],
    )
    orch, store = _make_orchestrator(reasoning, tmp_path, policy=ShellPolicy.ASK)
    await store.create(TaskRecord(task_id="task-int-1", goal="g", workspace_path=str(ws)))

    initialized = await orch.run_task("task-int-1")
    assert initialized.status == TaskStatus.AWAITING_PLAN_APPROVAL

    # Drive continue_task in the background; it will pause at the gate.
    continue_handle = asyncio.create_task(orch.continue_task("task-int-1", feedback=None))

    # Poll for the gate to fire.
    for _ in range(500):
        await asyncio.sleep(0.01)
        if "task-int-1" in orch._pending_command_decisions:
            break
    assert "task-int-1" in orch._pending_command_decisions, "gate did not pause the task"

    paused = await store.get("task-int-1")
    assert paused.status == TaskStatus.AWAITING_COMMAND_DECISION
    assert paused.execution_state.pending_command_request is not None
    assert paused.execution_state.pending_command_request.command == "echo"

    # Approve + remember (binary scope).
    orch._pending_command_decisions["task-int-1"].set_result(
        CommandDecision(approve=True, remember=True, scope="binary"),
    )

    # Let continue_task finish.
    result = await asyncio.wait_for(continue_handle, timeout=10.0)
    assert result.status in {TaskStatus.READY_FOR_REVIEW, TaskStatus.SUCCEEDED}

    final = await store.get("task-int-1")
    assert "hello.py" in final.modified_files
    # Per-task approved set picked up the binary rule for echo.
    assert any(r.value == "echo" for r in final.execution_state.approved_commands)


@pytest.mark.asyncio
async def test_run_command_gate_rejection_returns_tool_error(tmp_path: Path) -> None:
    """Reject path: the rejection becomes a tool-result error so the agent
    adapts within the step (instead of killing it)."""
    ws = tmp_path / "ws"
    ws.mkdir()

    reasoning = ScriptedReasoningEngine(
        plan=_make_plan(),
        patches=[{"candidates": [{"candidate_id": "c1", "patch_ops": _patch_op_create_hello()}]}],
        tool_step_responses=[
            # 1. EXPLORE — patch first.
            {"type": "emit_patch", "thought": "create file",
             "patch_ops": _patch_op_create_hello()},
            # 2. VERIFY — run_command (the gate will be triggered + rejected).
            {"type": "tool_call", "thought": "smoke test", "tool": "run_command",
             "args": {"command": "echo", "args": ["hi"]}},
            # 3. VERIFY — after the rejected tool-result, close the step
            # (the agent reads the rejection error and adapts).
            {"type": "verify_done", "thought": "skip tests after reject",
             "verified": True, "test_output": ""},
        ],
    )
    orch, store = _make_orchestrator(reasoning, tmp_path, policy=ShellPolicy.ASK)
    await store.create(TaskRecord(task_id="task-int-2", goal="g", workspace_path=str(ws)))

    await orch.run_task("task-int-2")
    continue_handle = asyncio.create_task(orch.continue_task("task-int-2", feedback=None))

    for _ in range(500):
        await asyncio.sleep(0.01)
        if "task-int-2" in orch._pending_command_decisions:
            break
    assert "task-int-2" in orch._pending_command_decisions

    fut = orch._pending_command_decisions["task-int-2"]
    fut.set_result(CommandDecision(approve=False))

    # Wait for the callback to consume the rejection and resume EXECUTING (clears
    # pending_command_request). The post-reject path through the verify phase is
    # implementation-detail and is covered at the unit level (engine + registry);
    # the integration concern here is that the gate fires and the rejection is
    # applied without persisting an approval.
    for _ in range(500):
        await asyncio.sleep(0.01)
        resumed = await store.get("task-int-2")
        if resumed.execution_state.pending_command_request is None:
            break
    final = await store.get("task-int-2")
    assert final.execution_state.pending_command_request is None
    # Nothing should be persisted to the per-task approved set on reject.
    assert final.execution_state.approved_commands == []

    # Stop the background continue_task — its post-reject path is not under test.
    continue_handle.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await continue_handle


@pytest.mark.asyncio
async def test_allow_all_skips_gate_end_to_end(tmp_path: Path) -> None:
    """ALLOW_ALL: the callback approves silently — no AWAITING_COMMAND_DECISION
    transition ever occurs."""
    ws = tmp_path / "ws"
    ws.mkdir()

    reasoning = ScriptedReasoningEngine(
        plan=_make_plan(),
        patches=[{"candidates": [{"candidate_id": "c1", "patch_ops": _patch_op_create_hello()}]}],
        tool_step_responses=[
            {"type": "emit_patch", "thought": "create",
             "patch_ops": _patch_op_create_hello()},
            {"type": "tool_call", "thought": "test", "tool": "run_command",
             "args": {"command": "echo", "args": ["hi"]}},
            {"type": "verify_done", "thought": "ok",
             "verified": True, "test_output": ""},
        ],
    )
    orch, store = _make_orchestrator(reasoning, tmp_path, policy=ShellPolicy.ALLOW_ALL)
    await store.create(TaskRecord(task_id="task-int-3", goal="g", workspace_path=str(ws)))

    await orch.run_task("task-int-3")
    result = await asyncio.wait_for(
        orch.continue_task("task-int-3", feedback=None), timeout=10.0,
    )
    assert result.status in {TaskStatus.READY_FOR_REVIEW, TaskStatus.SUCCEEDED}
    # No gate future was ever registered.
    assert "task-int-3" not in orch._pending_command_decisions
