"""Resolve a chat thread's current actionable state from its active task.

The chat thread is the durable anchor; its active task id churns (resume creates
a new child id). This pure resolver maps the active task's status to the single
gate it's waiting on plus the current actionable plan, so the UI can render
entirely from state (no in-memory session, no reliance on transient SSE).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from agentd.chat.models import PendingGate, ThreadLiveState
from agentd.domain.models import TaskRecord

_GateKind = Literal["command", "step", "scope", "validation"]

# status -> (gate kind, the execution_state field holding its payload)
_GATE_FIELD: dict[str, tuple[_GateKind, str]] = {
    "AWAITING_COMMAND_DECISION": ("command", "pending_command_request"),
    "AWAITING_STEP_REVIEW": ("step", "pending_step_review"),
    "AWAITING_SCOPE_DECISION": ("scope", "pending_scope_request"),
    "AWAITING_VALIDATION_DECISION": ("validation", "pending_validation"),
}


def _payload(raw: object) -> dict:
    """Normalize a pending_* field (Pydantic model or dict) to a plain dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, BaseModel):
        return raw.model_dump(mode="json")
    return {}


def resolve_live_state(
    active_task_id: str | None,
    get_task: Callable[[str], TaskRecord],
) -> ThreadLiveState:
    """Build the ThreadLiveState for a thread given its active task id.

    `get_task` raises KeyError for an unknown/missing task; that is treated as
    "no active task" (the task was pruned) rather than an error.
    """
    if not active_task_id:
        return ThreadLiveState()
    try:
        task = get_task(active_task_id)
    except KeyError:
        return ThreadLiveState()

    status = str(task.status)
    gate: PendingGate | None = None
    if status in _GATE_FIELD:
        kind, field = _GATE_FIELD[status]
        gate = PendingGate(kind=kind, payload=_payload(getattr(task.execution_state, field, None)))

    plan: dict | None = None
    if status == "AWAITING_PLAN_APPROVAL" and task.plan_markdown:
        plan = {"task_id": task.task_id, "plan_markdown": task.plan_markdown}

    return ThreadLiveState(
        active_task_id=task.task_id,
        status=status,
        pending_gate=gate,
        plan=plan,
    )
