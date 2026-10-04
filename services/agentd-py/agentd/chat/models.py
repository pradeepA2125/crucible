from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from agentd.domain.models import (
    CommandDecision,
    FailureSummary,
    McpToolDecision,
    RunSummary,
    TaskNarrative,
)


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
    # Stable per-message anchor for chat rewind. Deliberately nullable with NO
    # default_factory: model_validate runs against raw dicts out of messages_json,
    # so a factory would mint a fresh random id on every read of every message
    # persisted before this feature existed. Nullable means those carry None and
    # simply do not offer a rewind anchor.
    id: str | None = None
    type: Literal["text", "plan_card", "diff_card", "diff_summary", "task_card", "scope_card",
                  "agent_dispatch"] = "text"
    task_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRecord(BaseModel):
    """One sub-agent (spec §3.1) — a `chat_agents` row. v2 makes it a durable record that
    can run more than once (activations), so the model history and the definition it was
    dispatched with are stored, not just the outcome."""
    agent_id: str
    thread_id: str
    turn_id: str
    parent_agent_id: str | None = None
    depth: int
    name: str
    label: str
    prompt: str
    # queued | running | waiting (live) — completed | awaiting_peer | partial | failed |
    # failed_transient | stopped (idle)
    status: str
    report: str = ""  # the full report, never truncated (D8)
    files_changed: list[str] = Field(default_factory=list)
    stale_refusals: int = 0
    transcript: list[ChatMessage] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    definition: dict[str, Any] = Field(default_factory=dict)
    activation_count: int = 0
    last_seq: int = 0
    on_finish: str | None = None
    team_id: str | None = None
    dispatcher_id: str | None = None
    checkpoint_seq: int = -1
    report_delivered_at: datetime | None = None
    inherited: dict[str, bool] = Field(default_factory=dict)
    stop_reason: str | None = None
    activation_started_at: datetime | None = None
    activation_ended_at: datetime | None = None
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    limiter_wait_ms: int = 0

    def summary(self) -> dict[str, Any]:
        """The list view (spec §11.3): no transcript, a 200-character UI-only preview."""
        # Pills persist per segment, each message holding its segment's full list
        # (AgentTranscript.upsert_pills), so the sum over messages is the call count.
        tool_count = sum(len(m.metadata.get("tool_events") or []) for m in self.transcript)
        return {
            "agent_id": self.agent_id, "turn_id": self.turn_id,
            "parent_agent_id": self.parent_agent_id, "depth": self.depth,
            "name": self.name, "label": self.label, "status": self.status,
            "files_changed_count": len(self.files_changed),
            "tool_count": tool_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "report_preview": self.report[:200],
        }


class CapturedFile(BaseModel):
    """One path's pre-edit state inside a rewind checkpoint.

    `existed=False` means the turn created it (nothing was copied; restoring deletes
    it). `oversize=True` means it was too large to snapshot, so a rewind reports it
    as not-restored rather than restoring it wrong.
    """
    path: str
    existed: bool
    oversize: bool = False


class Checkpoint(BaseModel):
    """A rewind point: the state of a thread just before one turn started.

    The four controller_* blobs are snapshotted WHOLE rather than truncated at rewind
    time. controller_history_json is a flat list of assistant-action / tool-result
    pairs with no alignment to transcript messages, so "truncate the history to match
    the transcript" has no correct implementation; restoring a verbatim earlier copy
    sidesteps alignment and picks up todos, active skill and the pinned seed for free.
    """
    thread_id: str
    seq: int
    anchor_message_id: str
    turn_id: str
    created_at: datetime
    files: list[CapturedFile] = Field(default_factory=list)
    controller_history_json: str | None = None
    controller_seed_json: str | None = None
    controller_todo_json: str | None = None
    controller_active_skill_json: str | None = None
    memory_anchor_md: str | None = None


GateKind = Literal["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool"]


class GateAgent(BaseModel):
    """Which sub-agent raised a gate (spec §4.5). Absent on the main agent's gates."""
    id: str
    label: str
    name: str


class GateTeam(BaseModel):
    """Which team raised a gate (spec §3.8). A team gate is not the main agent's, so a new
    user turn must not clear it."""
    id: str
    name: str


class PendingGate(BaseModel):
    """One gate a thread is waiting on. A thread may hold several (spec §4.5).

    command/step/scope/validation are derived from the active *task* status
    (see live_state._GATE_FIELD) and carry a synthetic id "task:{task_id}:{kind}".
    mode/edit/clarify/mcp_tool/command are *controller* gates — the controller has no
    task, so they live on the thread (pending_controller_gates).

    gate_id is never a default_factory: that would mint a fresh id on every read of a
    stored row (the ChatMessage.id lesson — see CLAUDE.md "Chat rewind"), so decision
    routes could never address a gate. "" means "not stored yet": the store assigns a
    uuid on add_controller_gate. Callers that need the id before storing (to key a
    decision future) mint it with PendingGate.new().
    """
    gate_id: str = ""
    kind: GateKind
    payload: dict[str, Any] = Field(default_factory=dict)
    agent: GateAgent | None = None
    team: GateTeam | None = None

    def is_main(self) -> bool:
        """The main agent's own gate — the only kind a new user turn supersedes."""
        return self.agent is None and self.team is None

    @classmethod
    def new(cls, kind: GateKind, payload: dict[str, Any],
            agent: GateAgent | None = None) -> PendingGate:
        return cls(gate_id=uuid4().hex, kind=kind, payload=payload, agent=agent)


class GateNotFoundError(LookupError):
    """A decision named a gate_id that is not pending on the thread (route → 404). Benign
    by design: a card click can race an auto-accept or a stop that just removed it."""


class GateAmbiguousError(ValueError):
    """A decision omitted gate_id while several gates of that kind are pending (→ 409)."""


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
    # Controller-turn gates. The controller has no task, so its gates live here (durable,
    # surfaced by /live via resolve_thread_live). A list: sub-agents can raise gates
    # concurrently with each other (spec §4.5).
    pending_controller_gates: list[PendingGate] = Field(default_factory=list)
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
    # Thread-scoped active skill ({"name", "body"} or None) — exactly one at a time,
    # replaced (not accumulated) on the next read_skill. Survives every turn boundary
    # (including "answer" outcomes, unlike controller_todos) since a skill's mid-flow
    # steps can themselves be presented as an answer awaiting open-ended user reply
    # (e.g. brainstorming's "user reviews spec" step) — clearing on outcome kind would
    # drop it right when the next turn needs it most. The natural eviction is a
    # DIFFERENT skill being read (e.g. brainstorming handing off to writing-plans).
    controller_active_skill: dict[str, Any] | None = None


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
    # Every pending gate, controller gates first (spec §4.5).
    pending_gates: list[PendingGate] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    # Durable lifecycle telemetry (Tier B): failure_summary only at FAILED/ABORTED,
    # run_summary whenever present. Lets the Error/Review cards render from state on reload.
    failure_summary: FailureSummary | None = None
    run_summary: RunSummary | None = None
    task_narrative: TaskNarrative | None = None
    # The request's live todo checklist (raw item dicts), surfaced regardless of an active
    # task/gate so the UI can show progress. None when no list exists.
    todos: list[dict[str, Any]] | None = None
    # Live exec sessions for this thread ({id, command, status, exit_code,
    # started_at}); None when the feature is off or no sessions. STABLE rows
    # only — no age_sec/unread_bytes, they'd churn the /live dedup signature
    # every tick (the webview computes age locally from started_at).
    sessions: list[dict[str, Any]] | None = None
    # The in-flight turn's sub-agent tree (spec §11.1); None when there is none.
    agents: list[dict[str, Any]] | None = None


class ChatCommandDecisionRequest(CommandDecision):
    """POST /chat/threads/{id}/command-decision body. gate_id is optional: omitted means
    "the single pending command gate" (pre-multi-gate clients). Request-only — the route
    hands the controller a plain CommandDecision."""
    gate_id: str | None = None


class ChatMcpDecisionRequest(McpToolDecision):
    """POST /chat/threads/{id}/mcp-decision body; gate_id as ChatCommandDecisionRequest."""
    gate_id: str | None = None
