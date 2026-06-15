"""ControllerLoop — the agentic chat-turn ReAct loop (mirrors PlanningLoop).

Reads always hit the real workspace (no shadow-read flip). Terminal actions own
their own teardown. E1 implements explore (tool_call) + answer; clarify/propose_mode/
edit/submit_changes are added in E2/E3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentd.chat.tool_events import trace_to_tool_events
from agentd.domain.models import AgentToolTrace, ToolCall, ToolResult
from agentd.reasoning.react_common import MALFORMED_CORRECTION, assistant_turn, dedup_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.chat.edit_session import TurnEditSession
    from agentd.domain.models import DiffEntry
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.reasoning.contracts import ReasoningEngine
    from agentd.tools.sources import AggregatingToolRegistry

    EditDecisionCb = Callable[[list[DiffEntry]], Awaitable[dict[str, object]]]
    # Given the files an accepted edit touched, return a compact retrieval-refresh
    # note (pointers only, no bodies) to append to history — or None.
    RetrievalDeltaCb = Callable[[list[str]], Awaitable[str | None]]


class ControllerLoopExhausted(Exception):
    """Raised when the controller emits too many consecutive malformed responses.

    Mirrors PlanningLoop's PlanningBudgetExceededError malformed cap.
    """


# The modes propose_mode may offer; resolution routes on these exact strings
# (resolve_mode: edit/explain re-enter the loop, create_task/resume hand off).
_VALID_MODES = frozenset({"edit", "create_task", "resume", "explain"})

PROPOSE_MODE_CORRECTION = (
    "Your propose_mode was rejected: each option MUST be an object "
    '{"mode": <m>, "label": <short button text>, "description": <one line>} where '
    "<m> is one of edit | create_task | resume | explain, and the top-level "
    '"recommended" MUST be one of those same values. You used an invalid mode name '
    "or the wrong keys (e.g. \"type\" instead of \"mode\"). Re-emit propose_mode with "
    "valid modes — typically offer BOTH edit (make the change inline now) and "
    "create_task (plan it as a reviewed task), plus explain."
)


def _propose_mode_correction(resp: dict[str, object]) -> str | None:
    """Return None if the propose_mode OPTIONS are well-formed, else a correction.

    Enforces the mode vocabulary the same way the phase SM enforces action types:
    a weak model that invents modes ("create") or wrong keys (options[].type) gets
    corrected and retried rather than surfacing an unusable gate. `recommended` is a
    non-blocking hint — it's normalized in the emit branch, not required here (weak
    models reliably emit good options but drop the recommended field)."""
    options = resp.get("options")
    if not isinstance(options, list) or not options:
        return PROPOSE_MODE_CORRECTION
    for opt in options:
        if not isinstance(opt, dict) or opt.get("mode") not in _VALID_MODES:
            return PROPOSE_MODE_CORRECTION
    return None


def _normalized_recommended(resp: dict[str, object]) -> str:
    """The model's recommended mode if valid, else the first option's mode (a hint,
    never blocks the gate — see _propose_mode_correction)."""
    rec = resp.get("recommended")
    if rec in _VALID_MODES:
        return str(rec)
    options = resp.get("options")
    if isinstance(options, list) and options and isinstance(options[0], dict):
        return str(options[0].get("mode", ""))
    return ""


@dataclass
class ControllerOutcome:
    kind: str  # "answer" | "clarify" | "propose_mode" | "submit_changes"
    text: str = ""
    payload: dict[str, object] | None = None
    history: list[dict[str, object]] | None = None
    # Durable tool pills (ToolEventView shape) for the turn — persisted onto the
    # agent message so they survive a reload (live SSE pills die). None until the
    # loop finalizes it in run().
    tool_events: list[dict[str, object]] | None = None


class ControllerLoop:
    def __init__(
        self,
        reasoning: ReasoningEngine,
        registry: AggregatingToolRegistry,
        broadcaster: EventBroadcaster,
        *,
        channel_id: str,
        phase_sm: ControllerPhaseSM,
        edit_session: TurnEditSession | None = None,
    ) -> None:
        self._reasoning = reasoning
        self._registry = registry
        self._broadcaster = broadcaster
        self._channel_id = channel_id
        self._sm = phase_sm
        self._edit = edit_session
        self._calls: list[ToolCall] = []
        self._results: list[ToolResult] = []

    async def run(
        self,
        plan_context: dict[str, object],
        *,
        max_iters: int = 32,
        seed_history: list[dict[str, object]] | None = None,
        auto_accept_edits: bool = False,
        edit_decision_cb: EditDecisionCb | None = None,
        retrieval_delta_cb: RetrievalDeltaCb | None = None,
    ) -> ControllerOutcome:
        tool_defs = [d.model_dump() for d in self._registry.definitions()]
        history = [dict(m) for m in seed_history] if seed_history else []
        seen: dict[str, int] = {}
        # Bail only after this many CONSECUTIVE malformed responses (mirror PlanningLoop).
        _MAX_MALFORMED = 3
        consecutive_malformed = 0
        plan_context = {**plan_context, "max_iters": max_iters}
        # Tool trace accumulated across the turn → persisted as durable pills (reload).
        self._calls = []
        self._results = []
        try:
            outcome = await self._iterate(
                plan_context, history, tool_defs, seen, max_iters,
                _MAX_MALFORMED, consecutive_malformed,
                auto_accept_edits=auto_accept_edits,
                edit_decision_cb=edit_decision_cb,
                retrieval_delta_cb=retrieval_delta_cb,
            )
            if outcome.tool_events is None and self._calls:
                outcome.tool_events = trace_to_tool_events(
                    AgentToolTrace(step_id="chat", calls=self._calls, results=self._results),
                    "execution")
            return outcome
        finally:
            # The per-turn shadow is discarded at turn end on ANY exit (submit, budget
            # exhaustion, exhaustion-raise, or a patch crash) — no shadow leak.
            if self._edit is not None:
                await self._edit.close()

    async def _iterate(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_defs: list[dict[str, object]],
        seen: dict[str, int],
        max_iters: int,
        _MAX_MALFORMED: int,
        consecutive_malformed: int,
        *,
        auto_accept_edits: bool,
        edit_decision_cb: EditDecisionCb | None,
        retrieval_delta_cb: RetrievalDeltaCb | None,
    ) -> ControllerOutcome:
        for iteration in range(max_iters + 1):
            # Live "thinking" status so the chat UI isn't blank during the first model
            # call (the frontend maps chat_agent_thinking → the thinking pane). Only the
            # first iteration: subsequent activity is conveyed by tool pills + the live
            # work-bar timer, so re-emitting each turn would just spam duplicate entries.
            if iteration == 0:
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "chat_agent_thinking", "payload": {"message": "Thinking…"}})
            resp = await self._reasoning.create_controller_step(
                plan_context=plan_context, history=history,
                tool_definitions=tool_defs, phase=self._sm.phase,
            )
            atype = str(resp.get("type", ""))
            if atype == "answer":
                history.append(assistant_turn(resp))
                return ControllerOutcome(
                    kind="answer", text=str(resp.get("answer", "")), history=history)
            # Malformed = wrong action type for the phase, OR a propose_mode whose
            # mode vocabulary is invalid (enforced like the SM enforces action types).
            correction = (
                MALFORMED_CORRECTION
                if atype not in self._sm.allowed_types()
                else _propose_mode_correction(resp) if atype == "propose_mode"
                else None
            )
            if correction is not None:
                if atype == "propose_mode":
                    logging.getLogger(__name__).warning(
                        "[controller] propose_mode REJECTED: recommended=%r options=%r",
                        resp.get("recommended"), resp.get("options"))
                consecutive_malformed += 1
                if consecutive_malformed > _MAX_MALFORMED:
                    raise ControllerLoopExhausted(
                        f"Controller returned {consecutive_malformed} consecutive malformed "
                        f"responses (last type={atype!r})")
                history.append(assistant_turn(resp))
                history.append({"role": "tool_result", "tool": "", "content": correction})
                continue
            consecutive_malformed = 0
            if atype == "tool_call":
                if iteration >= max_iters:
                    return ControllerOutcome(
                        kind="answer", text="(step budget exhausted)", history=history)
                tool = str(resp.get("tool", ""))
                raw_args = resp.get("args")
                args: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}
                key = dedup_key(tool, args)
                if key in seen:
                    history.append({"role": "assistant", "content": "{}"})
                    history.append({
                        "role": "tool_result", "tool": tool,
                        "content": f"DUPLICATE BLOCKED (iter {seen[key]}); do differently.",
                    })
                    continue
                seen[key] = iteration + 1
                # Live tool pill: tool_call before execute, tool_result after. The
                # frontend pairs these by source ("execution") into a pill with thought.
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "tool_call",
                    "payload": {"tool": tool, "thought": str(resp.get("thought", "")),
                                "args": args}})
                out = await self._registry.execute(tool, args)
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "tool_result",
                    "payload": {"output": out.output, "is_error": out.is_error}})
                # Record into the turn trace → persisted as durable pills (reload).
                call_id = f"c{iteration}"
                self._calls.append(ToolCall(
                    call_id=call_id, tool_name=tool, arguments=args,
                    thought=str(resp.get("thought", "")) or None))
                self._results.append(ToolResult(
                    call_id=call_id, tool_name=tool, output=out.output, is_error=out.is_error))
                history.append(assistant_turn(resp))
                history.append({"role": "tool_result", "tool": tool, "content": out.output})
                continue
            if atype == "clarify":
                history.append(assistant_turn(resp))
                return ControllerOutcome(
                    kind="clarify", text=str(resp.get("question", "")), history=history)
            if atype == "propose_mode":
                history.append(assistant_turn(resp))
                return ControllerOutcome(kind="propose_mode", payload={
                    "plan_sketch": resp.get("plan_sketch", ""),
                    "recommended": _normalized_recommended(resp),
                    "reason": resp.get("reason", ""),
                    "options": resp.get("options", []),
                }, history=history)
            if atype == "edit":
                # EDIT phase is only reachable with an edit_session (phase SM gate).
                assert self._edit is not None
                raw_ops = resp.get("patch_ops")
                ops: list[dict[str, object]] = raw_ops if isinstance(raw_ops, list) else []
                try:
                    diff = await self._edit.apply(ops)
                except Exception as exc:
                    # A bad search string / policy violation / ambiguous selector raises
                    # (PatchEngine.apply_patch_candidate). Feed it back so the agent can
                    # read the file and re-emit — mirrors ToolLoop._apply_patch_inline —
                    # instead of crashing the whole turn.
                    history.append(assistant_turn(resp))
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"PATCH FAILED: {exc}. Read the file and re-emit a "
                                   "corrected patch (check the exact search text)."})
                    continue
                self._broadcaster.broadcast(self._channel_id, {
                    "type": "diff_ready",
                    "payload": {"diff_entries": [
                        {"path": d.path, "additions": d.additions,
                         "deletions": d.deletions, "unified_diff": d.unified_diff}
                        for d in diff]},
                })
                # Auto-accept (instant promote) OR hold for a per-edit review decision.
                # The cb holds the SSE stream open + renders the diff via /live
                # (Strategy: pluggable accept policy, reuses step_review semantics).
                if auto_accept_edits or edit_decision_cb is None:
                    await self._edit.accept()
                    accepted = True
                    reason = ""
                else:
                    decision = await edit_decision_cb(diff)
                    accepted = decision.get("decision") == "accept"
                    reason = str(decision.get("reason", ""))
                    if accepted:
                        await self._edit.accept()
                    else:
                        await self._edit.reject()  # restore shadow from real (shadow==real)
                history.append(assistant_turn(resp))
                if accepted:
                    touched = [d.path for d in diff]
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"applied+promoted: {touched}"})
                    # Append-only retrieval delta (spec §6): never rewrites the seed,
                    # only adds a compact refresh note to the cached tail.
                    if retrieval_delta_cb is not None:
                        delta = await retrieval_delta_cb(touched)
                        if delta:
                            history.append({
                                "role": "tool_result", "tool": "retrieval_refresh",
                                "content": delta})
                else:
                    history.append({
                        "role": "tool_result", "tool": "edit",
                        "content": f"REJECTED by user: {reason}. Revise and re-emit."})
                continue
            if atype == "submit_changes":
                # The shadow is closed by run()'s finally on return (no double-close).
                history.append(assistant_turn(resp))
                return ControllerOutcome(
                    kind="submit_changes", text=str(resp.get("summary", "")), history=history)
            raise NotImplementedError(atype)
        return ControllerOutcome(kind="answer", text="(loop ended)", history=history)
