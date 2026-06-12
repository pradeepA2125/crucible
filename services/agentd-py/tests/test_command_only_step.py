"""Command-only steps (no targets — e.g. "run the full test suite") are first-class.

Before this support, a step with nothing to patch was trapped in EXPLORE: the
SM's only EXPLORE exits are patch events and run_command wasn't even available,
so the model ground its budget on reads and escaped only via revision_needed.
Command-only steps now start in POSTPATCH_CLEAN ("workspace is final — verify
it"), run commands that drive TEST_FAILED/TEST_PASSED, and verify_done unlocks
only via TEST_PASSED.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import PlanStep, PlanTarget, PlanTargetIntent, TaskBudget, TaskUsage
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.tools.loop import ToolLoop, VerifyResult, _is_command_only_step, build_tool_registry
from agentd.tools.verify_phase_sm import VerifyPhaseState, VerifyPhaseStateMachine

# ── detection ────────────────────────────────────────────────────────────────


def _step(targets: list[PlanTarget]) -> PlanStep:
    return PlanStep(id="s4", goal="run tests", targets=targets, risk="low",
                    testing_strategy="run pytest tests/")


def test_empty_targets_is_command_only(tmp_path: Path) -> None:
    assert _is_command_only_step(_step([]), tmp_path) is True


def test_folder_target_is_command_only(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    targets = [PlanTarget(path="tests", intent=PlanTargetIntent.EXISTING)]
    assert _is_command_only_step(_step(targets), tmp_path) is True


def test_trailing_slash_target_is_command_only(tmp_path: Path) -> None:
    targets = [PlanTarget(path="tests/", intent=PlanTargetIntent.EXISTING)]
    assert _is_command_only_step(_step(targets), tmp_path) is True


def test_file_target_is_code_step(tmp_path: Path) -> None:
    targets = [PlanTarget(path="src/app.py", intent=PlanTargetIntent.NEW)]
    assert _is_command_only_step(_step(targets), tmp_path) is False


# ── state machine mode ───────────────────────────────────────────────────────


def test_command_only_sm_starts_in_postpatch_clean() -> None:
    sm = VerifyPhaseStateMachine(command_only=True)
    assert sm.state == VerifyPhaseState.POSTPATCH_CLEAN


def test_command_only_sm_gates_verify_done_behind_test_passed() -> None:
    sm = VerifyPhaseStateMachine(command_only=True)
    # POSTPATCH_CLEAN: the "no tests required" skip is NOT available; the model
    # must run the step's commands first.
    assert "verify_done" not in sm.allowed_tools()
    assert "run_command" in sm.allowed_tools()
    assert "emit_patch" not in sm.allowed_tools()
    from agentd.tools.verify_phase_sm import VerifyPhaseEvent
    sm.transition(VerifyPhaseEvent.TEST_FAILED)
    assert "verify_done" not in sm.allowed_tools()
    assert "run_command" in sm.allowed_tools()
    # In-step fixes ARE allowed on failure — the scope gate guards the writes
    # (empty targets make every novel file out-of-scope).
    assert "emit_patch" in sm.allowed_tools()
    sm.transition(VerifyPhaseEvent.TEST_PASSED)
    assert "verify_done" in sm.allowed_tools()


def test_default_sm_unchanged() -> None:
    sm = VerifyPhaseStateMachine()
    assert sm.state == VerifyPhaseState.EXPLORE
    assert "emit_patch" in sm.allowed_tools()


# ── end-to-end loop ──────────────────────────────────────────────────────────


class _RunTestsEngine:
    """Scripted model for a command-only step: run the tests, then verify_done."""

    def __init__(self) -> None:
        self.seen_action_types: list[list[str]] = []
        self._ran = False

    async def create_tool_step(self, step_context, history, tool_definitions,
                               on_thinking=None, state_description="", allowed_action_types=None):
        self.seen_action_types.append(sorted(allowed_action_types or []))
        if not self._ran:
            self._ran = True
            return {"type": "tool_call", "thought": "run the suite",
                    "tool": "run_command", "args": {"command": "python -c \"print('ok')\""}}
        return {"type": "verify_done", "thought": "tests pass", "verified": True,
                "test_output": "ok"}

    async def create_patch(self, *a, **kw): return {}
    async def create_planning_step(self, *a, **kw): return {}
    async def create_plan(self, *a, **kw): return {}


@pytest.mark.asyncio
async def test_command_only_step_runs_command_and_verifies(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "tests").mkdir()

    engine = _RunTestsEngine()
    registry = build_tool_registry(shadow, None, real_workspace_path=real)
    loop = ToolLoop(
        engine, registry, EventBroadcaster(), "task-x", None, shadow,
    )

    outcome = await loop.run(
        _step([]),
        {"goal": "run tests", "workspace_path": str(real)},
        TaskBudget(),
        TaskUsage(),
    )

    assert isinstance(outcome, VerifyResult)
    assert outcome.verified is True
    assert outcome.touched_files == []
    assert outcome.patch_document == {}
    # Happy path (POSTPATCH_CLEAN → TEST_PASSED) never offers emit_patch —
    # patching only unlocks on failure states, where the scope gate guards it.
    for types in engine.seen_action_types:
        assert "emit_patch" not in types
