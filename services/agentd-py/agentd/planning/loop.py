"""Explore-then-commit ReAct loop for the PlanningAgent."""
from __future__ import annotations

import json
import logging
import os
from uuid import uuid4

from agentd.domain.models import (
    AgentToolTrace,
    PlanRevisionResult,
    PlanningResult,
    RevisedStep,
    TaskBudget,
    ToolCall,
    ToolResult,
)
from agentd.orchestrator.broadcaster import PatchEventBroadcaster
from agentd.planning.registry import PlanningToolRegistry
from agentd.reasoning.contracts import ReasoningEngine

logger = logging.getLogger(__name__)

_MAX_PLANNING_RESULT_CHARS = int(os.environ.get("AI_EDITOR_PLANNING_RESULT_MAX_CHARS", "100000"))


class PlanningBudgetExceededError(Exception):
    """Raised when the planning loop exhausts its tool-call budget."""

    def __init__(self, message: str, partial_trace: "AgentToolTrace | None" = None) -> None:
        super().__init__(message)
        self.partial_trace = partial_trace


def _validate_no_duplicate_file_targets(steps: list[dict[str, object]]) -> list[str]:
    """Check that no file path appears in more than one step's targets."""
    seen: dict[str, str] = {}
    errors: list[str] = []
    for step in steps:
        step_id = str(step.get("id", step.get("step_id", "?")))
        targets = step.get("targets", [])
        if not isinstance(targets, list):
            continue
        for target in targets:
            path = target.get("path", "") if isinstance(target, dict) else str(target)
            if path in seen:
                errors.append(
                    f"File '{path}' appears in both step '{seen[path]}' and step '{step_id}'. "
                    "Consolidate all changes to this file into one step."
                )
            else:
                seen[path] = step_id
    return errors


class PlanningLoop:
    """Implements the explore-then-commit ReAct loop for PlanningAgent.

    Calls reasoning_engine.create_planning_step() each iteration.
    Returns when the agent emits emit_plan or emit_revision.
    Raises PlanningBudgetExceededError if budget exhausted without emitting.
    """

    def __init__(
        self,
        reasoning_engine: ReasoningEngine,
        registry: PlanningToolRegistry,
        broadcaster: PatchEventBroadcaster,
        task_id: str,
        chat_channel_id: str | None = None,
    ) -> None:
        self._reasoning = reasoning_engine
        self._registry = registry
        self._broadcaster = broadcaster
        self._task_id = task_id
        self._chat_channel_id = chat_channel_id

    def _broadcast(self, event: dict) -> None:
        self._broadcaster.broadcast(self._task_id, event)
        if self._chat_channel_id:
            self._broadcaster.broadcast(self._chat_channel_id, event)
            event_type = event.get("type", "?")
            payload = event.get("payload", {})
            if event_type == "planning_tool_call":
                logger.info("[chat→task] planning_tool_call: tool=%s iter=%s → %s",
                            payload.get("tool"), payload.get("iteration"), self._chat_channel_id)
            elif event_type == "planning_complete":
                logger.info("[chat→task] planning_complete: confidence=%s → %s",
                            payload.get("confidence"), self._chat_channel_id)

    async def run(
        self,
        plan_context: dict[str, object],
        budget: TaskBudget,
        *,
        revision_mode: bool = False,
    ) -> PlanningResult | PlanRevisionResult:
        """Run one planning loop. Returns PlanningResult or PlanRevisionResult."""
        tool_defs = [t.model_dump() for t in self._registry.definitions()]
        max_calls = (
            budget.max_revision_tool_calls if revision_mode else budget.max_planning_tool_calls
        )
        emit_type = "emit_revision" if revision_mode else "emit_plan"
        return await self._run_single_pass(
            plan_context=plan_context,
            tool_defs=tool_defs,
            max_calls=max_calls,
            emit_type=emit_type,
        )

    async def _run_single_pass(
        self,
        plan_context: dict[str, object],
        tool_defs: list[dict[str, object]],
        max_calls: int,
        emit_type: str,
    ) -> PlanningResult | PlanRevisionResult:
        trace = AgentToolTrace(step_id="planning")
        history: list[dict[str, object]] = []
        # key = (tool_name, canonical_args_json) → first iteration it was called
        _seen_calls: dict[str, int] = {}

        _MAX_STEP_RETRIES = 2

        for iteration in range(max_calls + 1):
            def _on_thinking(chunk: str, _iter: int = iteration) -> None:
                self._broadcast({
                    "type": "planning_thinking_chunk",
                    "payload": {"chunk": chunk, "iteration": _iter + 1},
                })

            last_step_exc: Exception | None = None
            response: dict[str, object] = {}
            for _attempt in range(_MAX_STEP_RETRIES + 1):
                try:
                    response = await self._reasoning.create_planning_step(
                        plan_context=plan_context,
                        history=history,
                        tool_definitions=tool_defs,
                        on_thinking=_on_thinking,
                    )
                    last_step_exc = None
                    break
                except Exception as exc:
                    last_step_exc = exc
                    logger.warning(
                        "[plan] create_planning_step failed at iter=%d attempt=%d/%d: %s",
                        iteration, _attempt + 1, _MAX_STEP_RETRIES + 1, exc,
                    )
            if last_step_exc is not None:
                raise last_step_exc

            action_type = str(response.get("type", ""))
            thought = str(response.get("thought", ""))

            if action_type == "emit_plan":
                plan_markdown = response.get("plan_markdown")
                if not plan_markdown or not str(plan_markdown).strip():
                    raise PlanningBudgetExceededError(
                        f"emit_plan response missing or empty 'plan_markdown' at iteration {iteration}",
                        partial_trace=trace,
                    )
                files_examined = list(response.get("files_examined", []))
                confidence = str(response.get("confidence", "medium"))
                if confidence not in ("high", "medium", "low"):
                    confidence = "medium"
                self._broadcast({
                    "type": "planning_complete",
                    "payload": {"files_examined": files_examined, "confidence": confidence},
                })
                return PlanningResult(
                    plan_markdown=str(plan_markdown),
                    files_examined=files_examined,
                    confidence=confidence,  # type: ignore[arg-type]
                    tool_trace=trace,
                )

            if action_type == "emit_revision":
                raw_steps = response.get("revised_steps")
                if not isinstance(raw_steps, list) or len(raw_steps) == 0:
                    raise PlanningBudgetExceededError(
                        f"emit_revision response missing or empty 'revised_steps' at iteration {iteration}",
                        partial_trace=trace,
                    )
                revised_steps = [
                    RevisedStep(
                        step_id=str(s.get("step_id", "")),
                        goal=str(s.get("goal", "")),
                        targets=s.get("targets", []),  # type: ignore[arg-type]
                        implementation_details=str(s.get("implementation_details", "")),
                        edge_cases=str(s.get("edge_cases", "")),
                        testing_strategy=str(s.get("testing_strategy", "")),
                        risk=str(s.get("risk", "low")),
                    )
                    for s in raw_steps
                    if isinstance(s, dict)
                ]
                reverted_step_ids = list(response.get("reverted_step_ids", []))
                revision_summary = str(response.get("revision_summary", ""))
                return PlanRevisionResult(
                    revised_steps=revised_steps,
                    reverted_step_ids=reverted_step_ids,
                    revision_summary=revision_summary,
                    tool_trace=trace,
                )

            if action_type != "tool_call":
                raise PlanningBudgetExceededError(
                    f"Unexpected planning response type '{action_type}' at iteration {iteration}; "
                    "expected tool_call, emit_plan, or emit_revision",
                    partial_trace=trace,
                )

            if iteration >= max_calls:
                raise PlanningBudgetExceededError(
                    f"Planning loop used {max_calls} tool calls without emitting {emit_type}",
                    partial_trace=trace,
                )

            tool_name = str(response.get("tool", ""))
            raw_args = response.get("args")
            args: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}

            args_repr = json.dumps(args, default=str)[:300]
            logger.info(
                "[plan] iter=%d/%d  tool=%s  args=%s",
                iteration + 1, max_calls, tool_name, args_repr,
            )

            # Duplicate call guard: if exact (tool, args) was seen before, inject correction
            # instead of executing — prevents infinite search_code loops.
            # For search_code, normalize out context_lines so bumping it doesn't bypass the guard.
            _dedup_args = dict(args)
            if tool_name == "search_code":
                _dedup_args.pop("context_lines", None)
            _call_key = f"{tool_name}:{json.dumps(_dedup_args, sort_keys=True, default=str)}"
            if _call_key in _seen_calls:
                _first_iter = _seen_calls[_call_key]
                _dedup_msg = (
                    f"DUPLICATE CALL BLOCKED: you already called `{tool_name}` with these "
                    f"exact arguments at iteration {_first_iter}. Repeating it will return "
                    "the same result. You MUST do something different:\n"
                    "  • If you need to read more of a file, use `read_file` with explicit "
                    "`start_line` and `end_line` from the line numbers you already saw.\n"
                    f"  • If you have enough context, call `{emit_type}` now.\n"
                    "Do NOT call the same tool with the same args again."
                )
                logger.warning(
                    "[plan] iter=%d/%d  DUPLICATE BLOCKED: tool=%s first_seen_at_iter=%d",
                    iteration + 1, max_calls, tool_name, _first_iter,
                )
                history.append({"role": "assistant", "content": json.dumps(response, default=str)})
                history.append({"role": "tool_result", "tool": tool_name, "content": _dedup_msg})
                continue
            _seen_calls[_call_key] = iteration + 1

            self._broadcast({
                "type": "planning_tool_call",
                "payload": {"tool": tool_name, "thought": thought[:300], "iteration": iteration + 1},
            })

            tool_output = await self._registry.execute(tool_name, args)

            out_chars = len(tool_output.output)
            preview = tool_output.output[:200].replace("\n", "↵")
            logger.info(
                "[plan] iter=%d/%d  tool=%s  →  chars=%d  is_error=%s  preview=%r",
                iteration + 1, max_calls, tool_name, out_chars, tool_output.is_error, preview,
            )

            self._broadcast({
                "type": "planning_tool_result",
                "payload": {"tool": tool_name, "output": tool_output.output[:500], "is_error": tool_output.is_error, "iteration": iteration + 1},
            })

            call_id = f"plan-{uuid4().hex[:8]}"
            trace.calls.append(ToolCall(call_id=call_id, tool_name=tool_name, arguments=args))
            trace.results.append(ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                output=tool_output.output[:_MAX_PLANNING_RESULT_CHARS],
                is_error=tool_output.is_error,
            ))

            history.append({"role": "assistant", "content": json.dumps(response, default=str)})
            history.append({
                "role": "tool_result",
                "tool": tool_name,
                "content": tool_output.output[:_MAX_PLANNING_RESULT_CHARS],
            })

        raise PlanningBudgetExceededError("Planning loop exited without result", partial_trace=trace)
