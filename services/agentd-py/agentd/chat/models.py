from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentd.domain.models import FailureSummary, RunSummary, TaskNarrative


class IntentType(StrEnum):
    QA = "qa"
    SMALL_CHANGE = "small_change"
    LARGE_CHANGE = "large_change"
    RESUME = "resume"
    CLARIFY = "clarify"


class IntentClassification(BaseModel):
    intent: IntentType
    rationale: str
    files_examined: list[str] = Field(default_factory=list)
    likely_targets: list[str] = Field(default_factory=list)
    answer: str | None = None
    clarify_question: str | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "agent"]
    content: str
    type: Literal["text", "plan_card", "diff_card", "diff_summary", "task_card", "scope_card"] = "text"
    task_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingGate(BaseModel):
    """The one gate a thread is waiting on, if any.

    command/step/scope/validation are derived from the active *task* status
    (see live_state._GATE_FIELD). mode/edit are *controller* gates — the
    controller has no task, so they live on the thread (pending_controller_gate).
    """
    kind: Literal["command", "step", "scope", "validation", "mode", "edit"]
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatThread(BaseModel):
    thread_id: str
    workspace_path: str
    title: str = "New Chat"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    messages: list[ChatMessage] = Field(default_factory=list)
    touched_files: list[str] = Field(default_factory=list)
    # The thread's current task. Set when a task is created or resumed from the
    # thread; resume updates it to the child id. The durable thread->task link
    # that lets the UI follow task-id churn without losing the gate/plan view.
    active_task_id: str | None = None
    # Controller-turn gate (mode/edit). The controller has no task, so its gate
    # lives here (durable, surfaced by /live via resolve_thread_live).
    pending_controller_gate: PendingGate | None = None
    # The controller loop's verbatim turn history (assistant action + tool_result
    # pairs), replayed as seed_history on the next turn. Durable so a backend
    # restart doesn't drop the conversation the transcript still shows — mirrors
    # TaskRecord.planning_conversation_history. None until the first turn writes it.
    controller_conversation_history: list[dict[str, Any]] | None = None
    # The thread's frozen retrieval seed (the cache-prefix head placed BEFORE history).
    # Pinned on first compute and replayed byte-for-byte so the KV prefix stays stable
    # across a backend restart even if the snapshot was re-indexed meanwhile — retrieval
    # changes ride the history tail as delta notes, never the seed. Mirrors the planner's
    # TaskRecord.planning_initial_context. None until the first turn computes it.
    controller_retrieval_seed: dict[str, Any] | None = None
    # Request-scoped todo ledger (raw item dicts), surfaced to /live so the user sees the
    # live checklist. Populated from controller_todo_json; None until the first write_todos.
    controller_todos: list[dict[str, Any]] | None = None


class ChatEvent(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ThreadLiveState(BaseModel):
    """Everything the chat UI needs to render a thread's current actionable state.

    Resolved from the thread's active task: its status, the single active gate
    (if waiting), and the current actionable plan (only at AWAITING_PLAN_APPROVAL).
    The UI renders from this (state-driven), so reloads and resume task-id churn
    self-heal on the next poll.
    """
    active_task_id: str | None = None
    # True while a controller turn (or a held-open controller gate) is in flight. The
    # /live route sets it from ChatController._active_turns so the FE can keep input
    # disabled across a webview reload (the ephemeral inputEnabled flag resets on mount).
    turn_active: bool = False
    status: str | None = None
    pending_gate: PendingGate | None = None
    plan: dict[str, Any] | None = None
    # Durable lifecycle telemetry (Tier B): failure_summary only at FAILED/ABORTED,
    # run_summary whenever present. Lets the Error/Review cards render from state on reload.
    failure_summary: FailureSummary | None = None
    run_summary: RunSummary | None = None
    task_narrative: TaskNarrative | None = None
    # The request's live todo checklist (raw item dicts), surfaced regardless of an active
    # task/gate so the UI can show progress. None when no list exists.
    todos: list[dict[str, Any]] | None = None
