"""Tests for AgentOrchestrator's scope-extension callback factory."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.domain.models import (
    PlanStep,
    PlanTarget,
    PlanTargetIntent,
    ScopePolicy,
    ScopeRemember,
    ScopeTrigger,
    TaskRecord,
    TaskStatus,
)
from agentd.orchestrator.engine import AgentOrchestrator, _is_nearby_file
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.tools.loop import ScopeDecision
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
    scope_policy: ScopePolicy = ScopePolicy.STRICT,
    scope_trigger: ScopeTrigger = ScopeTrigger.NEARBY,
    scope_remember: ScopeRemember = ScopeRemember.TASK,
    scope_timeout_sec: float = 600.0,
) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_AlwaysPassValidator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
        scope_policy=scope_policy,
        scope_trigger=scope_trigger,
        scope_remember=scope_remember,
        scope_timeout_sec=scope_timeout_sec,
    )


async def _seed_task(orchestrator: AgentOrchestrator, task_id: str = "t1") -> TaskRecord:
    """Create a task in EXECUTING (so AWAITING_SCOPE_DECISION transition is valid)."""
    task = TaskRecord(task_id=task_id, goal="g", workspace_path=".")
    # Walk through valid states to EXECUTING
    from agentd.domain.state_machine import transition
    task = transition(task, TaskStatus.CONTEXT_READY, "ctx")
    task = transition(task, TaskStatus.AWAITING_PLAN_APPROVAL, "approval")
    task = transition(task, TaskStatus.PLANNED, "planned")
    task = transition(task, TaskStatus.EXECUTING, "executing")
    await orchestrator._store.create(task)
    return task


def _make_step(target_paths: list[str]) -> PlanStep:
    return PlanStep(
        id="s1", goal="g", risk="low",
        targets=[PlanTarget(path=p, intent=PlanTargetIntent.NEW) for p in target_paths],
    )


@pytest.mark.asyncio
async def test_scope_callback_strict_policy_rejects(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, scope_policy=ScopePolicy.STRICT)
    await _seed_task(orch)
    step = _make_step(["tests/test_x.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    decision = await cb(["tests/__init__.py"], "pytest needs init")
    assert decision.approve is False
    assert "strict" in decision.reason


@pytest.mark.asyncio
async def test_scope_callback_auto_policy_approves(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, scope_policy=ScopePolicy.AUTO)
    task = await _seed_task(orch)
    task.artifacts_root_path = str(tmp_path / "artifacts" / "t1")
    await orch._store.save(task)
    step = _make_step(["tests/test_x.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    decision = await cb(["tests/__init__.py"], "pytest needs init")
    assert decision.approve is True
    assert decision.extended_files == ["tests/__init__.py"]


@pytest.mark.asyncio
async def test_scope_callback_ask_policy_resolves_via_future(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, scope_policy=ScopePolicy.ASK, scope_timeout_sec=5.0)
    await _seed_task(orch)
    step = _make_step(["tests/test_x.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    async def resolve_after_pause():
        # Wait for the callback to register its future, then resolve it.
        for _ in range(20):
            if "t1" in orch._pending_scope_decisions:
                break
            await asyncio.sleep(0.05)
        future = orch._pending_scope_decisions["t1"]
        future.set_result(ScopeDecision(
            approve=True, extended_files=["tests/__init__.py"], remember=False,
        ))

    resolver = asyncio.create_task(resolve_after_pause())
    decision = await cb(["tests/__init__.py"], "pytest needs init")
    await resolver

    assert decision.approve is True
    # Task must be back in EXECUTING after resolution
    task = await orch._store.get("t1")
    assert task.status == TaskStatus.EXECUTING
    assert task.execution_state.pending_scope_request is None


@pytest.mark.asyncio
async def test_scope_callback_ask_policy_times_out_to_reject(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, scope_policy=ScopePolicy.ASK, scope_timeout_sec=0.1)
    await _seed_task(orch)
    step = _make_step(["tests/test_x.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    decision = await cb(["tests/__init__.py"], "pytest needs init")
    assert decision.approve is False
    assert "timeout" in decision.reason.lower()
    # Task back in EXECUTING after timeout (not stuck in AWAITING_SCOPE_DECISION)
    task = await orch._store.get("t1")
    assert task.status == TaskStatus.EXECUTING


@pytest.mark.asyncio
async def test_scope_callback_remember_persists_within_task(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path, scope_policy=ScopePolicy.ASK, scope_timeout_sec=5.0)
    await _seed_task(orch)
    step = _make_step(["tests/test_x.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    async def resolve_with_remember():
        for _ in range(20):
            if "t1" in orch._pending_scope_decisions:
                break
            await asyncio.sleep(0.05)
        orch._pending_scope_decisions["t1"].set_result(ScopeDecision(
            approve=True, extended_files=["tests/__init__.py"], remember=True,
        ))

    asyncio.create_task(resolve_with_remember())
    await cb(["tests/__init__.py"], "first")

    # Second call for the same file should auto-approve via auto_approved_scope_files
    second = await cb(["tests/__init__.py"], "again")
    assert second.approve is True
    # No new pending request was registered the second time
    assert "t1" not in orch._pending_scope_decisions


@pytest.mark.asyncio
async def test_scope_callback_nearby_filter_blocks_purely_distant_batch(tmp_path: Path) -> None:
    orch = _make_orchestrator(
        tmp_path, scope_policy=ScopePolicy.AUTO, scope_trigger=ScopeTrigger.NEARBY,
    )
    task = await _seed_task(orch)
    task.artifacts_root_path = str(tmp_path / "artifacts" / "t1")
    await orch._store.save(task)
    step = _make_step(["src/calc.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    # Whole batch is non-nearby — `any()` filter rejects without prompting.
    decision = await cb(["docs/whatever.md", "scripts/build.sh"], "off-topic")
    assert decision.approve is False
    assert "nearby" in decision.reason.lower()


@pytest.mark.asyncio
async def test_scope_callback_nearby_filter_passes_mixed_batch_to_user(tmp_path: Path) -> None:
    """If at least ONE requested file is nearby, the whole batch is surfaced to the
    user — they decide. AUTO policy auto-approves so we can verify the request reached
    the policy layer instead of being filtered upstream."""
    orch = _make_orchestrator(
        tmp_path, scope_policy=ScopePolicy.AUTO, scope_trigger=ScopeTrigger.NEARBY,
    )
    task = await _seed_task(orch)
    task.artifacts_root_path = str(tmp_path / "artifacts" / "t1")
    await orch._store.save(task)
    # Step targets tests/test_x.py.
    # tests/__init__.py is nearby (pattern), pytest.ini is NOT nearby.
    step = _make_step(["tests/test_x.py"])
    cb = orch._build_scope_callback("t1", "s1", step)

    decision = await cb(["tests/__init__.py", "pytest.ini"], "scaffolding")
    assert decision.approve is True
    assert decision.extended_files == ["tests/__init__.py", "pytest.ini"]


def test_is_nearby_file_same_dir() -> None:
    assert _is_nearby_file("tests/extra.py", ["tests/test_x.py"]) is True


def test_is_nearby_file_init_pattern() -> None:
    assert _is_nearby_file("src/__init__.py", ["src/calc.py"]) is True


def test_is_nearby_file_distant_path() -> None:
    assert _is_nearby_file("docs/foo.md", ["src/calc.py"]) is False


def test_is_nearby_file_empty_allowed() -> None:
    assert _is_nearby_file("anything.py", []) is False


def test_is_nearby_file_inside_directory_target() -> None:
    """When a target is a directory (e.g. step targets `tests/`), files inside it count."""
    assert _is_nearby_file("tests/.gitkeep", ["tests"]) is True
    assert _is_nearby_file("tests/sub/foo.py", ["tests"]) is True


def test_is_nearby_file_unrelated_dir() -> None:
    assert _is_nearby_file("docs/readme.md", ["tests"]) is False
