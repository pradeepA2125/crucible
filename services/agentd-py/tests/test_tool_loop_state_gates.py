"""Loop-level guards for off-phase action types and off-phase tool calls.

The reasoning engine filters the JSON schema each turn so constrained-decoding
providers (Gemini, OpenAI structured, Groq, watsonx) physically cannot sample a
disallowed action type or tool. These tests pin the *fallback* guards in
ToolLoop that catch the same misuse when a provider ignores the schema
(Anthropic, OpenRouter without schema, Ollama text-only) — by simulating that
exact bypass with a scripted engine that returns off-state requests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import (
    PlanStep,
    PlanTarget,
    PlanTargetIntent,
    TaskBudget,
    TaskUsage,
)
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.patch.engine import PatchEngine
from agentd.tools.loop import ToolLoop, VerifyResult
from agentd.tools.registry import ToolRegistry


def _step(path: str = "a.py") -> PlanStep:
    return PlanStep(
        id="s1", goal="g",
        targets=[PlanTarget(path=path, intent=PlanTargetIntent.EXISTING)],
        risk="low",
    )


def _setup_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n")
    return ws


# ── action-type gate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_type_gate_blocks_verify_done_from_explore(tmp_path: Path) -> None:
    """A schema-bypass model emitting verify_done from EXPLORE must be pushed
    back via the action-type gate, never reaching the verify_done handler."""
    calls: list[str] = []

    class _BypassEngine:
        _turn = 0

        async def create_tool_step(
            self, step_context, history, tool_definitions,
            on_thinking=None, state_description="", allowed_action_types=None,
        ):
            _ = (step_context, tool_definitions, on_thinking, state_description, allowed_action_types)
            self._turn += 1
            if self._turn == 1:
                # EXPLORE — verify_done is NOT in allowed_action_types here.
                calls.append("verify_done_from_explore")
                return {"type": "verify_done", "thought": "bypass",
                        "verified": True, "test_output": ""}
            # The gate's pushback should now be in history; recover by emitting a
            # real patch so the test eventually terminates.
            calls.append("emit_patch_recovery")
            return {
                "type": "emit_patch", "thought": "ok",
                "patch_ops": [{"op": "search_replace", "file": "a.py",
                                "search": "x = 1", "replace": "x = 2", "reason": "r"}],
            }

        async def create_patch(self, *a, **kw): return {}
        async def create_planning_step(self, *a, **kw): return {}
        async def create_plan(self, *a, **kw): return {}

    ws = _setup_ws(tmp_path)
    engine = _BypassEngine()
    loop = ToolLoop(
        engine, ToolRegistry(shadow_root=ws, real_workspace_path=ws),
        EventBroadcaster(), "task-gate1",
        patch_engine=PatchEngine(), shadow_path=ws,
    )

    # We don't expect verify_done to complete the step — it should be gated, then
    # the second turn applies a real patch landing in POSTPATCH_CLEAN. The third
    # turn would need verify_done to close; the engine doesn't script that, so
    # add a follow-up. Simpler: just inject a third response via a wrapper.

    class _WrappedEngine(_BypassEngine):
        async def create_tool_step(self, *a, **kw):
            res = await _BypassEngine.create_tool_step(self, *a, **kw)
            return res

    # Use a simpler engine: turn 1 bypass, turn 2 patch, turn 3 verify_done.
    class _ThreeTurnEngine:
        _turn = 0

        async def create_tool_step(
            self, step_context, history, tool_definitions,
            on_thinking=None, state_description="", allowed_action_types=None,
        ):
            _ = (step_context, history, tool_definitions, on_thinking,
                 state_description, allowed_action_types)
            self._turn += 1
            if self._turn == 1:
                return {"type": "verify_done", "thought": "bypass",
                        "verified": True, "test_output": ""}
            if self._turn == 2:
                return {
                    "type": "emit_patch", "thought": "patch",
                    "patch_ops": [{"op": "search_replace", "file": "a.py",
                                    "search": "x = 1", "replace": "x = 2", "reason": "r"}],
                }
            return {"type": "verify_done", "thought": "done",
                    "verified": True, "test_output": "ok"}

        async def create_patch(self, *a, **kw): return {}
        async def create_planning_step(self, *a, **kw): return {}
        async def create_plan(self, *a, **kw): return {}

    e = _ThreeTurnEngine()
    loop2 = ToolLoop(
        e, ToolRegistry(shadow_root=ws, real_workspace_path=ws),
        EventBroadcaster(), "task-gate1b",
        patch_engine=PatchEngine(), shadow_path=ws,
    )
    result = await loop2.run(_step(), {}, TaskBudget(), TaskUsage())

    assert isinstance(result, VerifyResult)
    assert result.verified is True
    assert e._turn == 3, (
        "expected three turns: bypass (gated) → patch → verify_done. "
        "Got {turns}.".format(turns=e._turn)
    )


@pytest.mark.asyncio
async def test_action_type_gate_blocks_emit_patch_from_must_read(tmp_path: Path) -> None:
    """A model emitting emit_patch from PATCH_FAILED_MUST_READ must be pushed
    back before _apply_patch_inline runs — closes the MUST_READ side-channel."""

    apply_calls: list[bool] = []

    class _BypassMustReadEngine:
        _turn = 0

        async def create_tool_step(
            self, step_context, history, tool_definitions,
            on_thinking=None, state_description="", allowed_action_types=None,
        ):
            _ = (step_context, history, tool_definitions, on_thinking, allowed_action_types)
            self._turn += 1
            # Turn 1 (EXPLORE): emit a bad patch to fail and land in MUST_READ.
            if self._turn == 1:
                return {
                    "type": "emit_patch", "thought": "bad",
                    "patch_ops": [{"op": "search_replace", "file": "a.py",
                                    "search": "DOES_NOT_EXIST", "replace": "y", "reason": "r"}],
                }
            # Turn 2 (MUST_READ): bypass schema and try emit_patch again.
            # The action-type gate should block this — _apply_patch_inline must
            # NOT be reached.
            if self._turn == 2:
                assert "PATCH_FAILED" in state_description, state_description
                return {
                    "type": "emit_patch", "thought": "bypass MUST_READ",
                    "patch_ops": [{"op": "search_replace", "file": "a.py",
                                    "search": "x = 1", "replace": "z", "reason": "r"}],
                }
            # Turn 3: recover with a read, then a real patch, then verify_done.
            if self._turn == 3:
                return {"type": "tool_call", "thought": "read",
                        "tool": "read_file", "args": {"path": "a.py"}}
            if self._turn == 4:
                return {
                    "type": "emit_patch", "thought": "real patch",
                    "patch_ops": [{"op": "search_replace", "file": "a.py",
                                    "search": "x = 1", "replace": "x = 2", "reason": "r"}],
                }
            return {"type": "verify_done", "thought": "done",
                    "verified": True, "test_output": "ok"}

        async def create_patch(self, *a, **kw): return {}
        async def create_planning_step(self, *a, **kw): return {}
        async def create_plan(self, *a, **kw): return {}

    ws = _setup_ws(tmp_path)
    engine = _BypassMustReadEngine()
    registry = ToolRegistry(shadow_root=ws, real_workspace_path=ws)
    loop = ToolLoop(
        engine, registry, EventBroadcaster(), "task-gate2",
        patch_engine=PatchEngine(), shadow_path=ws,
    )

    # Wrap _apply_patch_inline to count calls — turn 2's bypassed emit_patch
    # must NOT reach it.
    original_apply = loop._apply_patch_inline

    async def counting_apply(patch_doc, step):
        apply_calls.append(True)
        return await original_apply(patch_doc, step)

    loop._apply_patch_inline = counting_apply  # type: ignore[method-assign]

    result = await loop.run(_step(), {}, TaskBudget(), TaskUsage())
    assert isinstance(result, VerifyResult)
    assert result.verified is True
    # _apply_patch_inline should have been called for turns 1 (bad patch, fails)
    # and 4 (good patch, succeeds). NOT for turn 2 (bypass blocked).
    assert len(apply_calls) == 2, (
        f"expected 2 apply attempts (bad + real), got {len(apply_calls)} — "
        "the MUST_READ bypass must be blocked before _apply_patch_inline"
    )


# ── tool gate ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_gate_blocks_run_command_from_explore(tmp_path: Path) -> None:
    """A schema-bypass model calling run_command from EXPLORE must be pushed
    back BEFORE registry.execute() — preventing the actual subprocess from
    running (no side effects)."""

    register_calls: list[tuple[str, dict]] = []

    class _CountingRegistry(ToolRegistry):
        async def execute(self, name: str, args: dict):  # type: ignore[override]
            register_calls.append((name, dict(args)))
            return await super().execute(name, args)

    class _BypassToolEngine:
        _turn = 0

        async def create_tool_step(
            self, step_context, history, tool_definitions,
            on_thinking=None, state_description="", allowed_action_types=None,
        ):
            _ = (step_context, history, on_thinking, state_description, allowed_action_types)
            tool_names = {t["name"] for t in tool_definitions}
            self._turn += 1
            if self._turn == 1:
                # In EXPLORE — run_command should NOT be in tool_names.
                assert "run_command" not in tool_names, (
                    f"run_command should be schema-filtered in EXPLORE, got: {tool_names}"
                )
                # But bypass the schema and try it anyway.
                return {"type": "tool_call", "thought": "bypass",
                        "tool": "run_command",
                        "args": {"command": "echo", "args": ["touched"]}}
            if self._turn == 2:
                return {
                    "type": "emit_patch", "thought": "recover",
                    "patch_ops": [{"op": "search_replace", "file": "a.py",
                                    "search": "x = 1", "replace": "x = 2", "reason": "r"}],
                }
            return {"type": "verify_done", "thought": "done",
                    "verified": True, "test_output": "ok"}

        async def create_patch(self, *a, **kw): return {}
        async def create_planning_step(self, *a, **kw): return {}
        async def create_plan(self, *a, **kw): return {}

    ws = _setup_ws(tmp_path)
    registry = _CountingRegistry(shadow_root=ws, real_workspace_path=ws)
    loop = ToolLoop(
        _BypassToolEngine(), registry, EventBroadcaster(), "task-gate3",
        patch_engine=PatchEngine(), shadow_path=ws,
    )

    result = await loop.run(_step(), {}, TaskBudget(), TaskUsage())
    assert isinstance(result, VerifyResult)
    assert result.verified is True

    # The bypassed run_command call from turn 1 must NOT have hit registry.execute.
    executed_tools = [name for name, _ in register_calls]
    assert "run_command" not in executed_tools, (
        f"run_command was executed despite being off-phase in EXPLORE: {register_calls}"
    )


@pytest.mark.asyncio
async def test_tool_gate_does_not_burn_budget(tmp_path: Path) -> None:
    """Off-phase tool calls must be gated BEFORE budget enforcement so they
    don't drain the explore/verify counters. The model recovers without
    hitting budget exhaustion."""
    class _ManyBypassesEngine:
        _turn = 0

        async def create_tool_step(
            self, step_context, history, tool_definitions,
            on_thinking=None, state_description="", allowed_action_types=None,
        ):
            _ = (step_context, history, tool_definitions, on_thinking,
                 state_description, allowed_action_types)
            self._turn += 1
            # Burn 8 bypass turns calling an off-phase tool, then finally patch.
            if self._turn <= 8:
                return {"type": "tool_call", "thought": "bypass",
                        "tool": "run_command",
                        "args": {"command": "echo", "args": ["x"]}}
            if self._turn == 9:
                return {
                    "type": "emit_patch", "thought": "patch",
                    "patch_ops": [{"op": "search_replace", "file": "a.py",
                                    "search": "x = 1", "replace": "x = 2", "reason": "r"}],
                }
            return {"type": "verify_done", "thought": "done",
                    "verified": True, "test_output": "ok"}

        async def create_patch(self, *a, **kw): return {}
        async def create_planning_step(self, *a, **kw): return {}
        async def create_plan(self, *a, **kw): return {}

    ws = _setup_ws(tmp_path)
    loop = ToolLoop(
        _ManyBypassesEngine(),
        ToolRegistry(shadow_root=ws, real_workspace_path=ws),
        EventBroadcaster(), "task-gate4",
        patch_engine=PatchEngine(), shadow_path=ws,
    )
    # Set a tight explore budget so we'd hit it if bypasses consumed slots.
    budget = TaskBudget(max_tool_calls_per_step=3, max_verify_calls_per_step=3)
    result = await loop.run(_step(), {}, budget, TaskUsage())

    # If the gate burns budget, 8 bypasses with max_explore=3 would raise
    # ToolBudgetExceededError before reaching the real patch. We should
    # instead reach verify_done successfully.
    assert isinstance(result, VerifyResult)
    assert result.verified is True
