"""Engine-side tests for the shell-command approval callback (T4).

Mirrors the local-helper pattern from tests/test_engine_scope_decision.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.domain.models import (
    CommandDecision,
    CommandRule,
    ShellPolicy,
    TaskRecord,
    TaskStatus,
)
from agentd.domain.state_machine import transition
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _AlwaysPassValidator:
    async def run(self, workspace_path):
        from agentd.domain.models import ValidationResult
        return ValidationResult(success=True, diagnostics=[], duration_ms=0)


def _make_orchestrator(
    tmp_path: Path,
    *,
    shell_policy: ShellPolicy = ShellPolicy.ASK,
    command_decision_timeout_sec: float = 0.0,
) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        shell_policy=shell_policy,
        command_decision_timeout_sec=command_decision_timeout_sec,
    )


async def _seed_task(orch: AgentOrchestrator, task_id: str = "t1", workspace: str = ".") -> TaskRecord:
    task = TaskRecord(task_id=task_id, goal="g", workspace_path=workspace)
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "approval")
    task = transition(task, TaskStatus.PLANNED, "planned")
    task = transition(task, TaskStatus.EXECUTING, "executing")
    task.execution_state.current_step_id = "s1"
    await orch._store.create(task)
    return task


@pytest.mark.asyncio
async def test_allow_all_skips_gate(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, shell_policy=ShellPolicy.ALLOW_ALL)
    task = await _seed_task(orch)
    cb = orch._build_command_approval_callback(task.task_id)
    decision = await cb("pytest", ["-q"], "services/agentd-py")
    assert decision.approve is True
    assert task.task_id not in orch._pending_command_decisions


@pytest.mark.asyncio
async def test_remembered_per_task_rule_skips_gate(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, shell_policy=ShellPolicy.ASK)
    task = await _seed_task(orch)
    task.execution_state.approved_commands.append(
        CommandRule(type="binary", value="pytest", added_at="t")
    )
    await orch._store.save(task)
    cb = orch._build_command_approval_callback(task.task_id)
    decision = await cb("pytest", ["-q"], "services/agentd-py")
    assert decision.approve is True


@pytest.mark.asyncio
async def test_ask_pauses_then_resumes_on_approval(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, shell_policy=ShellPolicy.ASK)
    task = await _seed_task(orch, workspace=str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()

    cb = orch._build_command_approval_callback(task.task_id)
    gate = asyncio.create_task(cb("python", ["-c", "print(1)"], str(tmp_path / "ws")))

    # Wait for the gate to register its future.
    for _ in range(100):
        await asyncio.sleep(0)
        if task.task_id in orch._pending_command_decisions:
            break
    assert task.task_id in orch._pending_command_decisions
    fut = orch._pending_command_decisions[task.task_id]
    assert not fut.done()

    # Status flipped to AWAITING_COMMAND_DECISION + pending_command_request set.
    paused = await orch._store.get(task.task_id)
    assert paused.status == TaskStatus.AWAITING_COMMAND_DECISION
    assert paused.execution_state.pending_command_request is not None
    assert paused.execution_state.pending_command_request.command == "python"
    assert paused.execution_state.pending_command_request.step_id == "s1"

    fut.set_result(CommandDecision(
        approve=True, remember=True, scope="prefix", rule_value="python -c",
    ))
    decision = await gate
    assert decision.approve is True

    # On resume: status back to EXECUTING, pending cleared, rule persisted to
    # both the per-task set and the per-workspace store.
    resumed = await orch._store.get(task.task_id)
    assert resumed.status == TaskStatus.EXECUTING
    assert resumed.execution_state.pending_command_request is None
    assert any(r.value == "python -c" for r in resumed.execution_state.approved_commands)

    from agentd.tools.command_rules import CommandRuleStore
    assert CommandRuleStore(resumed.workspace_path).matches("python -c 'print(2)'")


@pytest.mark.asyncio
async def test_per_task_override_beats_orchestrator_default(tmp_path: Path) -> None:
    """task.shell_policy overrides the orchestrator default."""
    orch = _make_orchestrator(tmp_path, shell_policy=ShellPolicy.ASK)
    task = await _seed_task(orch)
    task.shell_policy = ShellPolicy.ALLOW_ALL
    await orch._store.save(task)
    cb = orch._build_command_approval_callback(task.task_id)
    decision = await cb("pytest", ["-q"], ".")
    assert decision.approve is True
