"""ChatController — dynamic agentic chat handler (flag-selected vs ChatAgent).

Mirrors ChatAgent's public surface (handle_message + _store/_broadcaster attrs
the route reads) but runs ONE ControllerLoop per turn instead of the
explore→classify→route pipeline. F1 implements QA + clarify; propose_mode gate
(F2) and the per-edit review gate (F3) build on this.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from agentd.chat.controller_factory import (
    is_skills_enabled,
    is_subagents_enabled,
    is_task_subsystem_enabled,
)
from agentd.chat.controller_loop import ControllerLoop, ControllerOutcome
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.chat.models import (
    AgentRecord,
    ChatMessage,
    GateAgent,
    GateAmbiguousError,
    GateNotFoundError,
    PendingGate,
)
from agentd.chat.protected_paths import (
    AgentProtection,
    MainProtection,
    command_mentions_protected,
    is_protected,
)
from agentd.chat.rewind import RewindStore
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.chat.turn_control import ChatTurnControl
from agentd.domain.models import (
    ApprovalOutcome,
    CommandDecision,
    DeniedBy,
    McpToolDecision,
    ShellPolicy,
)
from agentd.memory.harness import NO_OP_HARNESS, MemoryHarness
from agentd.memory.models import ObservedPrompt
from agentd.prompting.tagged import RenderContext
from agentd.providers.availability import ProviderUnavailable
from agentd.reasoning.react_common import assistant_turn
from agentd.skills.loader import SkillCatalogLoader
from agentd.skills.tool_source import SkillToolSource, cap_skill_body
from agentd.subagents.agent_files import AgentCatalogLoader
from agentd.subagents.config import subagent_max_concurrent, subagent_max_depth, subagent_max_iters
from agentd.subagents.context import AgentContext, new_agent_id
from agentd.subagents.definitions import (
    BUILTIN_AGENTS,
    AgentDefinition,
    definition_from_json,
    definition_to_json,
)
from agentd.subagents.events import SequencedBroadcaster
from agentd.subagents.framing import frame
from agentd.subagents.inbox import InboxItem
from agentd.subagents.permissions import (
    child_allowed_types,
    child_tool_names,
    definition_allows_edit,
    effective_permission,
    follows_live_review,
)
from agentd.subagents.runtime import (
    IDLE_STATUSES,
    LIVE_STATUSES,
    AgentBusyError,
    AgentHandle,
    AgentNotFoundError,
    AgentNotYoursError,
    AgentSupervisor,
    ChildResult,
    DispatchRequest,
    agent_channel,
)
from agentd.subagents.tool_source import SubAgentToolSource, format_dispatch_result
from agentd.subagents.transcript import AgentTranscript
from agentd.subagents.vcs_guard import vcs_refusal
from agentd.subagents.write_log import MAIN_AGENT_ID, WorkspaceWriteLog, WriteGuard
from agentd.tools.command_rules import CommandRuleStore, rule_from_decision
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.promote import promote_files

if TYPE_CHECKING:
    from agentd.chat.storage import ChatThreadStore
    from agentd.domain.models import DiffEntry
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.reasoning.contracts import ReasoningEngine
    from agentd.retrieval.artifact_client import RetrievalArtifactClient

logger = logging.getLogger(__name__)

# Seconds to hold a per-edit review gate open before auto-rejecting (0 = forever).
# Mirrors CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC; guards against a dropped SSE client
# leaving the turn hung on a future that never resolves.
_EDIT_DECISION_TIMEOUT_ENV = "CRUCIBLE_CHAT_EDIT_DECISION_TIMEOUT_SEC"


def _explore_context_from_history(
    history: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Derive the planner's pre_explored_context from the controller's turn history.

    Walks the verbatim conversation and emits one entry per *tool call* — pairing
    each ``tool_call`` assistant turn with its following ``tool_result`` turn. Edits,
    terminals (answer/clarify/propose_mode/submit_changes), correction/dedup ``{}``
    turns and retrieval-refresh notes have no ``type=="tool_call"`` assistant and are
    naturally excluded. The result is the tool_result's full content — uncapped, since
    the history holds the verbatim output (unlike the 4000-capped tool_events pills).
    ``is_error`` is not carried in the history shape, so it defaults to False; the
    error text, when any, is already in the result content.
    """
    out: list[dict[str, object]] = []
    for index, entry in enumerate(history):
        if entry.get("role") != "assistant":
            continue
        raw = entry.get("content")
        if not isinstance(raw, str):
            continue
        try:
            action = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(action, dict) or action.get("type") != "tool_call":
            continue
        nxt = history[index + 1] if index + 1 < len(history) else None
        result = ""
        if isinstance(nxt, dict) and nxt.get("role") == "tool_result":
            result = str(nxt.get("content", ""))
        out.append({
            "tool": action.get("tool", ""),
            "args": action.get("args", {}),
            "result": result,
            "is_error": False,
        })
    return out


def _divider_text(activation_input: str) -> str:
    """`↩ Message from main: "<first line>"` (spec §3.2 item 4): the source line, then the
    first line of what was said, so the transcript shows why the agent ran again."""
    lines = [line.strip() for line in activation_input.splitlines() if line.strip()]
    if not lines:
        return "↩ resumed"
    # The failed-run note puts the old report first, and a report can contain lines that
    # end in ':' — so the "Message from …:" line wins when there is one.
    source = next((line for line in lines if line.startswith("Message from")),
                  next((line for line in lines if line.endswith(":")), lines[0]))
    after = lines[lines.index(source) + 1:] if source in lines else []
    return f'↩ {source} "{after[0]}"' if after else f"↩ {source}"


class ChatController:
    def __init__(
        self,
        *,
        workspace_path: str,
        reasoning_engine: ReasoningEngine,
        thread_store: ChatThreadStore,
        orchestrator: AgentOrchestrator | None,
        broadcaster: EventBroadcaster,
        retrieval_client: RetrievalArtifactClient | None = None,
        shell_policy: ShellPolicy = ShellPolicy.ASK,
        command_decision_timeout_sec: float = 0.0,
        memory_harness: MemoryHarness = NO_OP_HARNESS,
        mcp_manager: object | None = None,
        exec_session_manager: object | None = None,
        rewind_store: RewindStore | None = None,
    ) -> None:
        self._workspace_path = workspace_path
        self._reasoning = reasoning_engine
        self._store = thread_store
        self._orchestrator = orchestrator
        self._broadcaster = broadcaster
        self._retrieval = retrieval_client
        self._memory_harness = memory_harness
        # Rewind checkpoints. None when no store is wired (tests, legacy factories) —
        # every call site guards on it, so rewind is purely additive.
        self._rewind = rewind_store
        # Task subsystem flag (default OFF): gates create_task/resume mode handoff and the
        # task-mode prompt injection. Process-fixed — resolved once, like the controller flag.
        self._task_subsystem_enabled = is_task_subsystem_enabled()
        # run_command gating for ACTIVE turns (PLAN bars it entirely — see
        # controller_loop._decide_state_change_correction). Mirrors the task path's
        # CRUCIBLE_SHELL_POLICY / CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC.
        self._shell_policy = shell_policy
        self._command_decision_timeout_sec = max(0.0, command_decision_timeout_sec)
        # Per-thread controller conversation history — the cache prefix replayed as
        # seed_history on the next turn (clarify/discuss resume, spec §12).
        # TODO(controller): unbounded per-thread growth; eviction/compaction is owned
        # by the future agent-memory module (spec §6 defers it). Fine for v1.
        self._histories: dict[str, list[dict[str, object]]] = {}
        # Per-thread retrieval seed — computed once, never rewritten (spec §6 cache
        # discipline: a frozen pointer-set placed before history).
        self._seeds: dict[str, dict[str, object] | None] = {}
        # Per-thread carried prompt-size observation — closes the same turn-boundary
        # gap _histories/_seeds already close for their own state. Without this, a
        # fresh ControllerLoop resets its observation to None every run(), so every
        # turn's first compaction decision fell back to the character estimate on the
        # largest history the thread had ever accumulated, and the exact provider
        # count could only ever matter inside a single long turn (final whole-branch
        # review, finding 1). Threaded through ControllerLoop.run()'s observed_prompt
        # param and read back via loop.partial_observed_prompt(); None until the first
        # usage-reporting call, and cleared whenever a compaction rewrites history
        # (ControllerLoop invalidates it internally — see its _iterate).
        self._observed_prompts: dict[str, ObservedPrompt | None] = {}
        # Held-open decision futures, keyed by gate_id (spec §4.5 — a thread can hold several
        # gates at once). resolve_edit/resolve_command/resolve_mcp fire them; each raise
        # site pops its own entry and removes its own gate in its finally.
        self._pending_edit: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._pending_command: dict[str, asyncio.Future[CommandDecision]] = {}
        # MCP: process-scoped connection manager (None unless CRUCIBLE_MCP_ENABLED —
        # constructed in select_chat_handler, connected in main.py's startup hook).
        self._mcp_manager = mcp_manager
        # PTY exec sessions: process-scoped SessionManager (None unless
        # CRUCIBLE_EXEC_SESSIONS_ENABLED — built in select_chat_handler; main.py
        # registers its startup reap + shutdown kill-all). Sessions are
        # thread-scoped and survive across turns.
        self._exec_sessions = exec_session_manager
        # One write log per thread for the process lifetime (spec §7.1) — only when
        # sub-agents are enabled; the parent's read watermarks persist across turns.
        self._write_logs: dict[str, WorkspaceWriteLog] = {}
        # The sub-agent runtime (spec §6.2): process-wide, built once when enabled.
        self._subagents: AgentSupervisor | None = (
            AgentSupervisor(
                subagent_max_concurrent(), on_status=self._on_agent_status,
                children_of=self._store.child_agent_ids, on_leftover=self._on_leftover)
            if is_subagents_enabled() else None)
        # Agent definitions (spec §9): files in front of the built-ins, re-read per dispatch
        # source so a new file takes effect next turn with no restart.
        self._agent_catalog_loader: AgentCatalogLoader | None = (
            AgentCatalogLoader(workspace_path) if is_subagents_enabled() else None)
        # The turn whose dispatch tree /live reports (spec §11.1) — registered for exactly
        # loop.run's lifetime, like _active_loops.
        self._live_turns: dict[str, str] = {}
        # The main agent's dispatch that is awaiting its children, so a /stop can still
        # tell the next turn what they did (spec §11.4).
        self._inflight_dispatch: dict[str, list[AgentHandle]] = {}
        # gate_id → future for an in-flight mcp_tool gate; same lifecycle as
        # _pending_command.
        self._pending_mcp: dict[str, asyncio.Future[McpToolDecision]] = {}
        # Per-thread "Review each edit" toggle from the message that opened the mode
        # gate — read back in resolve_mode so the edit re-entry honors it (the
        # /mode-decision POST carries no step_review; smoke-found gap #4).
        self._step_review_by_thread: dict[str, bool | None] = {}
        # In-memory registry of the one detached turn per thread (mirrors the
        # orchestrator's _running_tasks). Earns its keep three ways: the in-flight
        # 409 guard (routes), the durable `turn_active` input signal (/live), and the
        # task handle stop_turn cancels. A backend restart clears it — the orphaned
        # turn is dead anyway (the transcript + pending_controller_gates survive in sqlite).
        self._active_turns: dict[str, asyncio.Task] = {}
        # The ControllerLoop currently running for a thread, so the mid-turn durable
        # writers (_write_breadcrumb, _edit_record_cb) can tell it a message just landed
        # in the transcript — see _mark_pills_boundary. Registered/released around
        # loop.run() in _run_loop; absent between turns, which makes the mark a no-op for
        # every out-of-turn breadcrumb (a restart-orphan crumb, ✗ Stopped, and the
        # mode/clarify decision crumbs, which precede a brand-new loop).
        self._active_loops: dict[str, ControllerLoop] = {}
        # Live preferences for the one in-flight turn per thread (chat-side sibling of
        # the orchestrator's _task_controls). Registered/released alongside
        # _active_loops; absent between turns, which is exactly how set_review_pref
        # tells "no turn running" (→ 409) from a live one.
        self._turn_controls: dict[str, ChatTurnControl] = {}

    def launch_turn(
        self, thread_id: str, coro, *, channel_id: str | None = None,
    ) -> asyncio.Task:
        """Detach a turn: create the task, register it, return the handle.

        create_task + the dict assignment have no `await` between them, so the
        in-flight guard (routes: `thread_id in _active_turns`) is race-safe in
        asyncio — same posture as the task routes' `_in_flight_*` guards."""
        task = asyncio.create_task(self._run_turn(thread_id, coro, channel_id))
        self._active_turns[thread_id] = task
        return task

    async def _run_turn(
        self, thread_id: str, coro, channel_id: str | None = None,
    ) -> None:
        """Run a turn coroutine and unconditionally clear its registry entry.

        The `finally` fires on normal completion, on error, AND on cancellation
        (stop_turn) — the single owner releasing its own slot so the thread never
        stays falsely `turn_active`. An unexpected exception is swallowed + logged
        and a failsafe chat_done is broadcast so the detached relay never hangs
        (a crashed turn that emitted no chat_done)."""
        try:
            await coro
        except asyncio.CancelledError:
            raise  # stop_turn / shutdown — re-raise so the task is marked cancelled
        except Exception:
            logger.exception("[controller] turn failed (thread=%s)", thread_id)
            if channel_id is not None:
                self._broadcaster.broadcast(
                    channel_id, {"type": "chat_done", "payload": {}})
        finally:
            self._active_turns.pop(thread_id, None)

    def _write_log_for(self, thread_id: str) -> WorkspaceWriteLog | None:
        """The thread's write log, created on first use; None when sub-agents are off
        (no guard, no read tracking — the parent behaves exactly as before P5)."""
        if not is_subagents_enabled():
            return None
        return self._write_logs.setdefault(thread_id, WorkspaceWriteLog())

    def _build_registry(
        self,
        command_approval_callback: object | None = None,
        todo_ledger: TodoLedger | None = None,
        todo_persist_cb: Callable[[str | None], Awaitable[None]] | None = None,
        active_skills: dict[str, str] | None = None,
        active_skill_persist_cb: Callable[[str | None], Awaitable[None]] | None = None,
        mcp_approval_cb: object | None = None,
        exec_session_source: object | None = None,
        thread_id: str = "",
        read_observer: Callable[[str], None] | None = None,
        extra_sources: list[object] | None = None,
    ) -> AggregatingToolRegistry:
        sources: list[object] = [BuiltinToolSource(
            shadow_root=Path(self._workspace_path),
            real_workspace_path=Path(self._workspace_path),
            semantic_index=getattr(self._retrieval, "_semantic_index", None),
            command_approval_callback=command_approval_callback,
            read_observer=read_observer,
        )]
        if todo_ledger is not None:
            sources.append(TodoToolSource(todo_ledger, on_mutate=todo_persist_cb))
        # run_id = thread_id tags explicit remembers so a rewind can retire them.
        mts = self._memory_harness.memory_tool_source(thread_id)  # None unless enabled
        if mts is not None:
            sources.append(mts)
        if is_skills_enabled() and active_skills is not None:
            sources.append(SkillToolSource(
                SkillCatalogLoader(self._workspace_path), active_skills,
                on_activate=active_skill_persist_cb))
        if self._mcp_manager is not None and mcp_approval_cb is not None:
            from agentd.mcp.tool_source import McpToolSource

            sources.append(McpToolSource(self._mcp_manager, mcp_approval_cb))
        if exec_session_source is not None:
            sources.append(exec_session_source)
        sources.extend(extra_sources or [])
        return AggregatingToolRegistry(sources)

    async def _persist_todos(self, thread_id: str, raw: str | None) -> None:
        """Persist the in-flight ledger the moment write_todos mutates it, so GET /live
        renders the checklist WHILE the EDIT turn is still running. The DB column is /live's
        source of truth; without this mid-turn write the card never appears for a single
        continuous EDIT turn (the end-of-turn persistence in _run_loop is too late, and a
        terminal submit_changes clears the row anyway)."""
        self._store.set_controller_todos(thread_id, raw or None)

    async def _persist_active_skill(self, thread_id: str, raw: str | None) -> None:
        """Persist the thread's active skill the moment read_skill activates it — mirrors
        _persist_todos. Deliberately no clearing counterpart called from _run_loop's
        terminal handling (see ChatThread.controller_active_skill docstring): the next
        read_skill's replacement is the only eviction path."""
        self._store.set_controller_active_skill(thread_id, raw)

    def _seed_for(self, thread_id: str) -> list[dict[str, object]]:
        """The thread's prior controller turn history to replay as seed_history.

        In-memory cache first; on a miss (e.g. a backend restart cleared it) rehydrate
        from the durable store and re-cache — so the conversation the transcript still
        shows is not lost from the model's context (mirrors the planner replaying
        TaskRecord.planning_conversation_history on a feedback round)."""
        cached = self._histories.get(thread_id)
        if cached is not None:
            return cached
        thread = self._store.get_thread(thread_id)
        history = (thread.controller_conversation_history if thread else None) or []
        self._histories[thread_id] = history
        return history

    def _retrieval_seed(self, thread_id: str, goal: str) -> dict[str, object] | None:
        """Compute the thread's retrieval seed once, then reuse it byte-for-byte so
        the cached payload prefix stays stable across turns (spec §6) AND across a
        backend restart: the seed is pinned durably and replayed verbatim, mirroring
        the planner's planning_initial_context. Retrieval changes ride the history
        tail as delta notes, so the seed itself is frozen for the thread's life — a
        re-indexed snapshot must NOT recompute it (that would break the KV prefix)."""
        if thread_id in self._seeds:
            return self._seeds[thread_id]
        # Rehydrate a pinned seed from the store (the restart path) before recomputing.
        thread = self._store.get_thread(thread_id)
        if thread is not None and thread.controller_retrieval_seed is not None:
            self._seeds[thread_id] = thread.controller_retrieval_seed
            return thread.controller_retrieval_seed
        seed: dict[str, object] | None = None
        if self._retrieval is not None:
            try:
                context, _ = self._retrieval.load_context(self._workspace_path, goal)
                seed = context.as_prompt_payload()
            except Exception:
                logger.debug("[controller] retrieval seed failed", exc_info=True)
        self._seeds[thread_id] = seed
        # Pin on first compute so a later restart replays these exact bytes.
        self._store.set_controller_seed(thread_id, seed)
        return seed

    async def handle_message(
        self, thread_id: str, message: str, channel_id: str, step_review: bool | None = None,
        forced_skills: list[str] | None = None,
        mentioned_files: list[dict[str, str]] | None = None,
        plan_mode: bool | None = None,
        message_id: str | None = None,
    ) -> None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id!r} not found")
        # A new turn supersedes only the MAIN agent's cards (spec §3.8): a late decision on
        # a superseded card hits `gate is None` and no-ops (resolve_mode/resolve_edit
        # already guard on this). A sub-agent's or a team's gate belongs to work that keeps
        # running, so it stays. A clarify sets no gate, so the clarify resume path is
        # unaffected.
        self._store.clear_main_gates(thread_id)
        # Auto-name the thread from its first user message (mirrors ChatAgent).
        if not any(m.role == "user" for m in thread.messages):
            title = message.strip().replace("\n", " ")[:50]
            self._store.update_title(thread_id, title)
            self._broadcaster.broadcast(channel_id, {
                "type": "thread_title_updated",
                "payload": {"thread_id": thread_id, "title": title},
            })
        # @-mentions are turn-scoped: the model sees the referenced file content
        # only for THIS turn (folded into turn_message below, which feeds
        # _run_loop's goal / plan_context["goal"]). The persisted/display message
        # and conversation history keep the short original text, tagged with the
        # mentioned paths only (never content) so the transcript can render
        # clickable mentions without duplicating file content into chat storage.
        turn_message = message
        mentioned_paths: list[str] = []
        if mentioned_files:
            mentioned_paths = [f["path"] for f in mentioned_files if f.get("path")]
            blocks = "\n\n".join(
                f"### {f['path']}\n```\n{f['content']}\n```"
                for f in mentioned_files if f.get("path")
            )
            if blocks:
                turn_message = f"{message}\n\n---\nReferenced files:\n{blocks}"
        # One id for this turn's in-flight pills message AND its rewind checkpoint.
        # Assigned here (not just before _run_loop) because open_checkpoint needs it.
        turn_id = uuid4().hex
        # The client may supply the id: the webview echoes the user's message
        # optimistically, before this call persists it, so letting it choose the id keeps
        # the echoed bubble and the stored message in agreement — otherwise the echo has
        # no rewind anchor and the affordance is missing on the message you just sent
        # until the thread is reloaded. None (any other client) still gets a fresh uuid.
        anchor_message_id = self._store.append_message(thread_id, ChatMessage(
            role="user", content=message, id=message_id,
            metadata={"mentioned_files": mentioned_paths} if mentioned_paths else {}))
        if self._rewind is not None and anchor_message_id is not None:
            # `thread` is the object read at the top of this function — pre-turn history,
            # todos, skill and seed, which is exactly what a rewind to here restores.
            self._rewind.open_checkpoint(
                thread_id, anchor_message_id, turn_id, thread=thread,
                memory_anchor_md=self._memory_harness.anchor_markdown(thread_id))
        # A new turn invalidates any prior in-flight pills marker (a stopped/orphaned
        # earlier turn). Drop it so this turn's switch-back dedup is scoped to its own
        # message (finding 5); the orphan's pills stay as a normal message.
        self._store.clear_inflight_markers(thread_id)
        # Remember this turn's review toggle so a propose_mode → "implement" re-entry
        # (resolved via /mode-decision, which carries no step_review) honors it.
        self._step_review_by_thread[thread_id] = step_review

        seed = self._seed_for(thread_id)
        # On a continued turn (discuss), append the user's reply to the prior history
        # and replay it as the cache prefix (spec §12 clarify resume).
        seed_history = (seed + [{"role": "user", "content": turn_message}]) if seed else None
        # Clarify-resume is now driven by resolve_clarify (the gate carries resume_phase),
        # not a fresh user message: the main composer is disabled while a clarify gate is
        # pending, so the answer arrives via the card. A plain message here always
        # supersedes any pending gate (cleared above) and starts fresh, in the phase the
        # sticky Plan Mode toggle selects (NEW-I6 — this MUST be the plan_mode
        # computation, never a stray None, or the toggle silently has no effect).
        resume_phase = "PLAN" if plan_mode else "ACTIVE"
        outcome = await self._run_loop(
            thread_id, channel_id, turn_message, seed_history=seed_history,
            step_review=step_review, phase=resume_phase, turn_id=turn_id,
            # A plain message is ALWAYS a fresh entry, never a resume — unlike
            # resolve_clarify's identical-looking expression below, `resume_phase`
            # here is the toggle-derived starting phase for a brand-new turn, not
            # "the phase we're resuming an in-flight feature into." Passing
            # (resume_phase == "ACTIVE") would evaluate True for every default
            # (Plan Mode off) message, suppressing the C1b entry hint on every
            # follow-up turn in a thread — exactly the mis-route active_entry
            # exists to prevent.
            edit_is_resume=False, forced_skills=forced_skills)
        await self._finish(thread_id, channel_id, outcome, step_review, turn_id=turn_id)

    async def _run_loop(
        self, thread_id: str, channel_id: str, goal: str, *,
        seed_history: list[dict[str, object]] | None, step_review: bool | None,
        phase: str | None = None, turn_id: str | None = None,
        edit_is_resume: bool = False, forced_skills: list[str] | None = None,
    ) -> ControllerOutcome:
        # Three callers feed `phase` with different intents: handle_message (the
        # plan_mode-derived starting phase — Task 7), resolve_mode's "implement"
        # dispatch (always "ACTIVE"), resolve_clarify ("PLAN"/"ACTIVE"/None
        # defensively). Anything not one of the two legal values falls back to the
        # default, "ACTIVE".
        sm = ControllerPhaseSM(start=phase if phase in ("PLAN", "ACTIVE") else "ACTIVE")
        # Request-scoped todo ledger: rehydrate so it survives the PLAN->ACTIVE (mode
        # gate) and clarify-resume loop boundaries within one request.
        ledger = TodoLedger.from_json(self._store.get_controller_todos(thread_id))
        # ACTIVE is now the default phase for every plain turn — the session (which
        # needs the orchestrator's workspace_manager/patch_engine) must be buildable
        # from ANY phase, not just a mode-gated entry. Build a closure unconditionally
        # (free) and let ControllerLoop construct the real session lazily, on the
        # first actual `edit` dispatch (C1) — a pure Q&A/PLAN turn never pays for it.
        write_log = self._write_log_for(thread_id)
        edit_session_factory = (
            (lambda: TurnEditSession(
                turn_id=thread_id, real_path=Path(self._workspace_path),
                workspace_manager=self._orchestrator._workspace_manager,
                patch_engine=self._orchestrator._patch_engine,
                checkpoint_cb=(
                    partial(self._rewind.capture, thread_id)
                    if self._rewind is not None else None),
                write_guard=(
                    WriteGuard(write_log, MAIN_AGENT_ID, "main", "main")
                    if write_log is not None else None),
                protection=MainProtection()))
            if self._orchestrator is not None else None)
        # run_command (ACTIVE-only; PLAN rejects it) is gated through the controller's
        # command callback — closes over this turn's thread/channel like edit_cb.
        command_cb = partial(self._command_approval_cb, thread_id, channel_id)
        # MCP tool calls gate through the same thread-gate machinery (kind="mcp_tool").
        mcp_cb = partial(self._mcp_approval_cb, thread_id, channel_id)
        # Persist the ledger mid-turn on every write_todos so /live renders it during the turn.
        todo_persist_cb = partial(self._persist_todos, thread_id)
        # PTY exec sessions (thread-scoped; start gated through the SAME command
        # approval gate as run_command). Available in PLAN and ACTIVE by design.
        exec_source = None
        if self._exec_sessions is not None:
            from agentd.exec_sessions.manager import SessionManager
            from agentd.exec_sessions.tool_source import ExecSessionToolSource

            assert isinstance(self._exec_sessions, SessionManager)
            exec_source = ExecSessionToolSource(
                self._exec_sessions, thread_id, command_cb)
        # Shared active-skills map: SkillToolSource (in the registry) writes activated bodies
        # here, the loop re-injects them into the dynamic tail each iteration. Rehydrated
        # from the thread's persisted single active skill (survives every turn boundary —
        # see ChatThread.controller_active_skill) so a multi-round flow like brainstorming's
        # "ask one question at a time" doesn't re-pay read_skill on every clarify round-trip.
        # A /skill forced-load then overrides it so the body is active from iteration 1.
        active_skills: dict[str, str] = {}
        stored_skill_raw = self._store.get_controller_active_skill(thread_id)
        if is_skills_enabled() and stored_skill_raw:
            stored_skill = json.loads(stored_skill_raw)
            active_skills[stored_skill["name"]] = stored_skill["body"]
        if is_skills_enabled() and forced_skills:
            catalog = SkillCatalogLoader(self._workspace_path).load_catalog()
            for name in forced_skills:
                manifest = next((m for m in catalog if m.name == name), None)
                if manifest is not None:
                    try:
                        active_skills.clear()
                        active_skills[name] = manifest.body_path.read_text(encoding="utf-8")
                    except OSError:
                        pass
        active_skill_persist_cb = partial(self._persist_active_skill, thread_id)
        # Same loader class the forced_skills seeding above already uses (own instance —
        # SkillCatalogLoader's mtime cache is per-instance and cheap to build). Lets
        # ControllerLoop force-load a skill a just-saved plan doc's own text names as
        # REQUIRED, without the model having to notice and call read_skill itself
        # (see ControllerLoop._maybe_force_required_subskill).
        skill_catalog_loader = (
            SkillCatalogLoader(self._workspace_path) if is_skills_enabled() else None)
        # The parent's dispatch_agents (spec §6.1) — only when sub-agents are enabled.
        dispatch_sources: list[object] = (
            [self._dispatch_source(thread_id, turn_id, None)]
            if self._subagents is not None and turn_id else [])
        loop = ControllerLoop(
            self._reasoning,
            self._build_registry(command_cb, ledger, todo_persist_cb,
                                  active_skills=active_skills,
                                  active_skill_persist_cb=active_skill_persist_cb,
                                  thread_id=thread_id,
                                  mcp_approval_cb=mcp_cb,
                                  exec_session_source=exec_source,
                                  read_observer=(
                                      write_log.read_observer(
                                          Path(self._workspace_path), MAIN_AGENT_ID)
                                      if write_log is not None else None),
                                  extra_sources=dispatch_sources), self._broadcaster,
            channel_id=channel_id, phase_sm=sm, edit_session_factory=edit_session_factory,
            todo_ledger=ledger,
            task_subsystem_enabled=self._task_subsystem_enabled,
            memory_harness=self._memory_harness, active_skills=active_skills,
            skill_catalog_loader=skill_catalog_loader,
            active_skill_persist_cb=active_skill_persist_cb,
            progress_note_cb=partial(self._progress_note_cb, thread_id),
            # Store half of the pills-segment boundary: freeze the in-flight pills message
            # in place so the next tool result appends a fresh one after whatever durable
            # message landed in between (the loop owns WHEN — see mark_pills_boundary).
            pills_seal_cb=(
                partial(self._store.seal_inflight_pills, thread_id, turn_id)
                if turn_id else None))
        plan_context: dict[str, object] = {
            "goal": goal, "workspace_path": self._workspace_path,
            # run_id keys the per-thread compaction segments + anchored summary.
            "run_id": thread_id}
        # A clarify-resume continues an in-flight EDIT feature — NOT a fresh entry — so the
        # loop must NOT show the "first action, decide your approach" entry hint. The per-turn
        # _edit_applied flag resets on the new loop, and a cohesive (no-list) edit leaves the
        # ledger empty, so without this signal the entry hint would wrongly re-appear mid-feature.
        # (A history scan can't tell this feature's prior edit from an earlier feature's — this
        # explicit flag can.) resolve_mode("implement") is a fresh entry → default False.
        plan_context["edit_is_resume"] = edit_is_resume
        # Debug-artifact keys (KV-safe: build_controller_step_payload ignores them) so
        # create_controller_step can dump the exact per-iteration LLM bytes under
        # chat/<thread_id>/<turn_id>/ (controller analog of the task path's plan-turn-NN).
        if turn_id:
            plan_context["artifact_thread_id"] = thread_id
            plan_context["artifact_turn_id"] = turn_id
            # Baseline so per-iteration artifact numbering is 0-based WITHIN this turn
            # (history includes the replayed seed_history from prior turns).
            plan_context["artifact_seed_len"] = len(seed_history or [])
        seed = self._retrieval_seed(thread_id, goal)
        if seed:
            plan_context["retrieval_seed"] = seed
        # "Review each edit" on → hold each patch for a decision; off → instant promote.
        # LIVE for the whole turn: the composer checkbox posts /review-pref, which mutates
        # this control, and the loop re-reads it before every edit. The message's
        # step_review is only the STARTING value.
        is_review = step_review is True
        control = ChatTurnControl(auto_accept_edits=not is_review)
        # Always wired, even when the turn starts in auto-accept — otherwise a mid-turn
        # flip TO review would have no gate to call and would silently keep promoting.
        edit_cb = partial(self._edit_decision_cb, thread_id, channel_id)
        # Single durable-record writer for every edit resolution (both modes): persists
        # an inert diff_card, + a breadcrumb (gated) or a live render (auto-accepted).
        # Whether an edit was gated is decided per edit BY THE LOOP (trailing arg), not
        # frozen here — mid-turn flips make it vary within one turn.
        record_cb = partial(self._edit_record_cb, thread_id, channel_id)
        # Incremental durable pill persistence (finding 5): upsert the in-flight pills
        # message per tool result so a switch/reopen mid-turn reconstructs them.
        pills_cb = partial(self._persist_inflight_pills, thread_id, turn_id) \
            if turn_id else None
        max_iters = int(os.environ.get("CRUCIBLE_CONTROLLER_MAX_ITERS", "500"))
        # Reachable by the mid-turn durable writers for exactly the loop's lifetime.
        self._active_loops[thread_id] = loop
        if turn_id:
            self._live_turns[thread_id] = turn_id
        # Same lifetime, for the same reason: /review-pref reaches in here mid-turn.
        self._turn_controls[thread_id] = control
        try:
            outcome = await loop.run(
                plan_context, max_iters=max_iters, seed_history=seed_history,
                observed_prompt=self._observed_prompts.get(thread_id),
                turn_control=control, edit_decision_cb=edit_cb,
                edit_record_cb=record_cb, retrieval_delta_cb=self._retrieval_delta_cb,
                on_pills_update=pills_cb)
        except asyncio.CancelledError:
            # /stop cancels the turn's asyncio.Task, raising here BEFORE the normal post-run
            # persistence below ever runs. Capture what the turn accumulated — its exploration
            # AND any edits it already instant-promoted to the real workspace — so the NEXT turn
            # rehydrates it instead of seeding from stale pre-turn state ("forgot what it just
            # did"). The diff_cards were persisted live (edit_record_cb), but those live in the
            # transcript column, NOT in controller_conversation_history (the model's replayed
            # context), so without this the model has no memory of its own stopped-turn edits.
            # Sync writes only (no further await) → the re-raised cancellation can't interrupt
            # them; then re-raise so stop_turn's own teardown/breadcrumb proceeds.
            partial_hist = loop.partial_history()
            # A dispatch the stop interrupted (spec §11.4): its tool_call never returned,
            # so append it and its result — each child's status, files and report — or the
            # next turn would not know what the children already promoted.
            interrupted = self._inflight_dispatch.pop(thread_id, None)
            if interrupted:
                partial_hist.append(assistant_turn({
                    "type": "tool_call", "thought": "(dispatch interrupted by stop)",
                    "tool": "dispatch_agents",
                    "args": {"agents": [
                        {"agent": h.context.name, "label": h.context.label, "prompt": h.prompt}
                        for h in interrupted]}}))
                partial_hist.append({
                    "role": "tool_result", "tool": "dispatch_agents",
                    "content": format_dispatch_result([
                        (h, h.result or ChildResult(
                            status="stopped", report="Stopped before it started.",
                            files_changed=[]))
                        for h in interrupted])})
            if partial_hist:
                self._histories[thread_id] = partial_hist
                self._store.set_controller_history(thread_id, partial_hist)
            # Captured explicitly (not left to the shared line after this try/except):
            # a re-raise skips everything below, so this branch is the only place that
            # would ever record it.
            self._observed_prompts[thread_id] = loop.partial_observed_prompt()
            self._store.set_controller_todos(
                thread_id, ledger.to_json() if ledger.items else None)
            raise
        except Exception as exc:
            self._inflight_dispatch.pop(thread_id, None)
            # A provider call (or any other step of the loop) raised something we don't
            # have specific recovery for — e.g. a cloud model exhausting its whole output
            # budget on thinking and returning no text content (observed live: Ollama
            # Cloud's Nemotron-3-Super burned 32768 tokens of <think> and never emitted a
            # response). Before this handler existed, that exception propagated straight
            # out of the SSE route uncaught: the stream died mid-flight with no chat_done,
            # no breadcrumb, nothing — turn_active still flipped false (cleanup elsewhere
            # runs regardless) so the composer re-enabled, but the todo list stayed frozen
            # at whatever was last persisted and nothing in the transcript or the live UI
            # ever indicated a failure happened. From the user's chair this reads as the
            # agent silently giving up mid-task, not as an error — the worst version of
            # "the UI looks stuck" because there isn't even a stuck state to notice.
            #
            # Fix: treat it like a normal turn-ending "answer" so it flows through the
            # already-correct chat_response + chat_done broadcast path below (_finish),
            # instead of inventing a new outcome kind every caller/frontend would need to
            # learn. Same partial-state persistence as the cancellation branch, but no
            # re-raise — the turn ends cleanly with a visible, actionable message.
            logger.exception(
                "[controller] turn failed with an unhandled exception (thread=%s)", thread_id)
            partial_hist = loop.partial_history()
            if partial_hist:
                self._histories[thread_id] = partial_hist
                self._store.set_controller_history(thread_id, partial_hist)
            self._store.set_controller_todos(
                thread_id, ledger.to_json() if ledger.items else None)
            outcome = ControllerOutcome(
                kind="answer",
                text=f"⚠️ The turn failed and had to stop: {exc}",
                history=partial_hist,
            )
        finally:
            # Released on EVERY exit (including the re-raised cancel) so a later
            # out-of-turn breadcrumb can never mark a dead loop's boundary, and so a
            # /review-pref arriving after the turn ends answers 409 instead of
            # mutating a control nothing reads.
            self._active_loops.pop(thread_id, None)
            self._turn_controls.pop(thread_id, None)
            self._live_turns.pop(thread_id, None)
        self._histories[thread_id] = outcome.history or []
        # Reached for the normal-completion AND generic-exception branches (the
        # cancellation branch already recorded its own and re-raised past this point).
        # loop.partial_observed_prompt() is read here rather than duplicated in the
        # exception branch above because loop.run() itself sets it before returning —
        # one accessor covers both live paths.
        self._observed_prompts[thread_id] = loop.partial_observed_prompt()
        # Turn trace artifact (controller analog of tool-trace.json): the whole turn's
        # info in one file for offline debugging — phase, verbatim history, pills,
        # thinking, outcome. Best-effort, never fails the turn.
        if turn_id:
            self._write_turn_trace(thread_id, turn_id, goal, sm.phase, outcome)
        # Durably persist the verbatim turn history so a backend restart rehydrates
        # seed_history instead of re-exploring cold (mirrors the planner persisting
        # planning_conversation_history on the TaskRecord).
        self._store.set_controller_history(thread_id, outcome.history or [])
        # Persist the ledger across this request's loop boundaries; clear on a terminal
        # outcome so the next request starts fresh. propose_mode/clarify are non-terminal —
        # the follow-on loop rehydrates the in-progress list.
        if outcome.kind in ("submit_changes", "answer"):
            self._store.set_controller_todos(thread_id, None)
        else:
            self._store.set_controller_todos(
                thread_id, ledger.to_json() if ledger.items else None)
        # A clarify raised mid-EDIT must resume in EDIT when answered. Carry the resume
        # target IN the gate payload (resolve_clarify reads it) rather than a side map
        # keyed on thread. sm.phase reflects the phase the loop ran in (EDIT is one-way,
        # never transitions back).
        if outcome.kind == "clarify":
            payload = dict(outcome.payload or {})
            payload["resume_phase"] = sm.phase if sm.phase in ("PLAN", "ACTIVE") else None
            outcome.payload = payload
        return outcome

    async def _retrieval_delta_cb(self, touched: list[str]) -> str | None:
        """Append-only retrieval delta after an accepted edit (spec §6).

        v1 returns a compact pointer note rather than recomputed neighbors: the
        edits are instant-promoted to real, so the live tools (read_file/search_code/
        query_graph) are the always-current source; a real neighbor recompute would
        need a fresh snapshot, which the self-updating watcher rebuilds async. The
        note never touches `retrieval_seed` (cache-prefix immutability)."""
        if not touched:
            return None
        return (
            f"Workspace changed: edited {touched}. These edits are live on the real "
            "workspace — use read_file/search_code for current contents and query_graph "
            "for updated neighbors. (The retrieval seed is from session start.)")

    async def _finish(
        self, thread_id: str, channel_id: str, outcome: ControllerOutcome,
        step_review: bool | None, turn_id: str | None = None,
    ) -> None:
        if outcome.kind == "answer":
            # Persist the turn's tool pills + thinking onto the message so they survive
            # a reload (live SSE pills/thinking die) — mirrors ChatAgent's metadata.
            self._write_turn_message(thread_id, turn_id, outcome.text, outcome)
            self._broadcaster.broadcast(
                channel_id, {"type": "chat_response", "payload": {"chunk": outcome.text}})
            self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
        elif outcome.kind == "clarify":
            await self._present_clarify_choice(thread_id, channel_id, outcome)
        elif outcome.kind == "submit_changes":
            # Cross-session memory (Phase 2): an edit-promoting turn (NOT answer/clarify/QnA)
            # consolidates into durable memory — closes the short-thread inline-edit hole.
            # Best-effort, fire-and-forget; no-op unless memory + a consolidator are enabled.
            if (outcome.text or "").strip():
                self._memory_harness.schedule_consolidation(
                    run_id=thread_id, scope_kind="workspace",
                    scope_id=str(self._workspace_path), transcript=outcome.text or "",
                    seq_lo=None, seq_hi=None)
            # Persist the EDIT turn's summary + exploration pills/thinking. The per-edit
            # diff_cards are already durable (edit_record_cb); without this closing
            # message the turn's pills/thinking vanish on reload (smoke-found gap #3).
            summary = outcome.text or ""
            if summary or self._turn_metadata(outcome):
                self._write_turn_message(thread_id, turn_id, summary, outcome)
                if summary:
                    self._broadcaster.broadcast(
                        channel_id, {"type": "chat_response", "payload": {"chunk": summary}})
            self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
        elif outcome.kind == "propose_mode":
            await self._present_mode_choice(thread_id, channel_id, outcome)

    def _write_turn_trace(
        self, thread_id: str, turn_id: str, goal: str, phase: str,
        outcome: ControllerOutcome,
    ) -> None:
        """One-file turn trace for offline debugging (controller analog of the task
        path's tool-trace.json). Best-effort — never fails the turn."""
        try:
            from agentd.runtime.artifacts import chat_turn_artifacts_root

            out = chat_turn_artifacts_root(thread_id, turn_id, self._workspace_path)
            out.mkdir(parents=True, exist_ok=True)
            (out / "turn-trace.json").write_text(
                json.dumps({
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "goal": goal,
                    "phase": phase,
                    "outcome_kind": outcome.kind,
                    "outcome_text": outcome.text,
                    "history": outcome.history or [],
                    "tool_events": outcome.tool_events or [],
                    "thinking_log": outcome.thinking_log or [],
                }, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("[controller] turn-trace dump failed", exc_info=True)

    async def _persist_inflight_pills(
        self, thread_id: str, turn_id: str,
        tool_events: list[dict[str, object]], thinking_log: list[str],
    ) -> None:
        """Per-tool-result callback (finding 5): upsert the in-flight turn's durable
        pills message so a switch/reopen mid-turn reconstructs them from the transcript."""
        self._store.upsert_inflight_pills(
            thread_id, turn_id, tool_events, thinking_log or None)

    def _write_turn_message(
        self, thread_id: str, turn_id: str | None, content: str, outcome: ControllerOutcome,
    ) -> None:
        """Write the turn's closing agent message. If pills were persisted incrementally
        during the turn, FINALIZE that same in-flight message (set content, drop the
        marker) — no duplicate. Otherwise (a turn with no tool calls) append fresh."""
        metadata = self._turn_metadata(outcome)
        if turn_id and self._store.finalize_inflight_pills(
            thread_id, turn_id, content,
            metadata.get("tool_events") or [],  # type: ignore[arg-type]
            metadata.get("thinking_log"),  # type: ignore[arg-type]
        ):
            return
        self._store.append_message(
            thread_id, ChatMessage(role="agent", content=content, metadata=metadata))

    @staticmethod
    def _turn_metadata(outcome: ControllerOutcome) -> dict[str, object]:
        """Durable pills + thinking for a turn's agent message (reload survival)."""
        metadata: dict[str, object] = {}
        if outcome.tool_events:
            metadata["tool_events"] = outcome.tool_events
        if outcome.thinking_log:
            metadata["thinking_log"] = outcome.thinking_log
        return metadata

    async def _present_mode_choice(
        self, thread_id: str, channel_id: str, outcome: ControllerOutcome,
    ) -> None:
        """Class-A gate: set a durable thread gate (/live renders it via LiveSlot,
        survives reload) and END the message stream. No SSE mode event — chat gates
        render purely from the /live poll (CLAUDE.md). Resolved by /mode-decision (F2)."""
        # Persist the exploration pills + thinking as a durable record (mirrors ChatAgent
        # writing a pills-only message before task cards) so they survive a reload; the
        # gate itself is durable via pending_controller_gates.
        metadata = self._turn_metadata(outcome)
        if metadata:
            self._store.append_message(thread_id, ChatMessage(
                role="agent", content="", metadata=metadata))
        self._store.add_controller_gate(
            thread_id, PendingGate.new("mode", outcome.payload or {}))
        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})

    async def _present_clarify_choice(
        self, thread_id: str, channel_id: str, outcome: ControllerOutcome,
    ) -> None:
        """Class-A gate: render the clarify question + options as a durable live card
        (/live → ClarifyGate), survives reload. No chat bubble — the question lives in
        the card; resolve_clarify writes the combined Q→A breadcrumb on resolution.
        Resolved by POST /clarify-decision. Mirrors _present_mode_choice."""
        metadata = self._turn_metadata(outcome)
        if metadata:
            self._store.append_message(thread_id, ChatMessage(
                role="agent", content="", metadata=metadata))
        self._store.add_controller_gate(
            thread_id, PendingGate.new("clarify", outcome.payload or {}))
        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})

    async def _edit_decision_cb(
        self, thread_id: str, channel_id: str, diff: list[DiffEntry], *,
        child: AgentHandle | None = None,
    ) -> dict[str, object]:
        """Hold the SSE stream open while a per-edit review gate is pending.

        Sets the durable `edit` thread gate (/live renders the diff), creates the
        decision future, and awaits it — mirroring _pause_for_step_review. On a
        dropped client (no decision) it auto-rejects after the timeout so the loop
        unwinds cleanly. The gate clears in place in the finally (Class-A)."""
        # temp_path: the card's view-diff button opens the native diff against the shadow
        # copy; without it every live edit gate could only say "shadow path missing".
        payload: dict[str, object] = {"diff_entries": [
            {"path": d.path, "additions": d.additions, "deletions": d.deletions,
             "unified_diff": d.unified_diff, "temp_path": d.temp_path}
            for d in diff]}
        if any(is_protected(d.path) for d in diff):
            # Resolved only by an explicit decision on this card (spec §3.9).
            payload["protected"] = True
        if child is not None:
            # A child edits in its own shadow (spec §7.5). Informational: a child gate is
            # never recovered after a restart (§11.5).
            payload["shadow_key"] = f"chatturn-{thread_id}-{child.agent_id}"
        gate = self._store.add_controller_gate(
            thread_id, PendingGate.new("edit", payload, agent=self._gate_agent(child)))
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending_edit[gate.gate_id] = fut
        self._set_child_status(child, "waiting")
        timeout = float(os.environ.get(_EDIT_DECISION_TIMEOUT_ENV, "0") or "0")
        try:
            if timeout > 0:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        except TimeoutError:
            return {"decision": "reject", "reason": "decision timed out"}
        finally:
            self._pending_edit.pop(gate.gate_id, None)
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._set_child_status(child, "running")

    async def _edit_record_cb(
        self, thread_id: str, channel_id: str,
        diff: list[DiffEntry], decision: str, reason: str, was_gated: bool,
    ) -> None:
        """Durably record a resolved edit (the loop's single transcript writer).

        Persists an inert diff_card (renders Applied/Discarded on reload, never
        interactive — mirrors engine._write_chat_step_diff_record). temp_path is
        omitted: the edit is instant-promoted (shadow==real) so a native diff is
        meaningless, and the turn-shadow is rmtree'd at turn end. When the edit was
        gated the live EditGate already showed the diff, so we add a breadcrumb (the
        card materializes on reload); when it auto-accepted there was no gate, so we
        render the inert card live too.

        `was_gated` is what happened to THIS edit, decided per edit by the loop against
        the live preference — not the turn's starting mode, which a mid-turn
        /review-pref may have flipped since (a gated edit would lose its breadcrumb,
        and an auto-accepted one would get a false ✓)."""
        diff_payload = [
            {"path": d.path, "additions": d.additions,
             "deletions": d.deletions, "unified_diff": d.unified_diff}
            for d in diff]
        resolved = "applied" if decision == "accept" else "discarded"
        # A mid-turn durable message: pills from after the edit belong after this card.
        self._mark_pills_boundary(thread_id)
        self._store.append_message(thread_id, ChatMessage(
            role="agent", content="", type="diff_card",
            metadata={"diff_entries": diff_payload, "resolved": resolved}))
        # Render the inert card live in BOTH modes so the accepted/rejected diff stays
        # in the transcript without waiting for a reload. In review mode the live
        # EditGate (pinned /live slot) has already cleared by now, so this fills the
        # hole it leaves; `resolved` is set so the card is inert (no dead buttons).
        self._broadcaster.broadcast(channel_id, {
            "type": "diff_ready",
            "payload": {"diff_entries": diff_payload, "resolved": resolved}})
        if decision == "stale":
            # Refused by the write guard (spec §7.4): always recorded, gated or not — the
            # user may have clicked Accept on an edit that nevertheless did not land.
            self._write_breadcrumb(thread_id, channel_id, f"✗ Not applied: {reason}")
            return
        files = ", ".join(d.path for d in diff) or "(no files)"
        if was_gated:
            if decision == "accept":
                text = f"✓ Edit accepted: {files}"
            else:
                text = f"✗ Edit rejected: {files}"
                if reason:
                    text += f" — {reason}"  # surface the user's reason in the record
            self._write_breadcrumb(thread_id, channel_id, text)

    async def set_review_pref(self, thread_id: str, *, auto_accept: bool) -> bool:
        """Live-mutable "Review each edit" preference for an IN-FLIGHT turn (chat-side
        twin of POST /tasks/{id}/review-pref). Returns False when no turn is running,
        which the route answers 409 with — the value still governs the next message,
        since the composer sends it with every send.

        Flipping to auto-accept while an edit gate is open resolves that gate as accept
        too, for the same consistent-intent reason the task route resolves a pending
        step review: the diff on screen would otherwise contradict the switch just
        flipped. The other direction only governs future edits — it never retroactively
        gates an edit that already promoted.

        Race-safe without a lock: single-process asyncio, and the mutation + the future's
        set_result happen with no `await` in between, so the loop can never observe a
        half-applied flip.
        """
        # Record it for a mode/clarify RESUME first. Those gates end the loop (chat_done),
        # so a flip made while one is on screen finds no live control — yet the gate pause
        # is exactly where "fine, stop asking me about each edit" gets clicked, and
        # resolve_mode/resolve_clarify re-enter from this map, not from the POST (neither
        # decision route carries step_review). Harmless when nothing is pending: the next
        # message overwrites this entry with its own value.
        self._step_review_by_thread[thread_id] = not auto_accept
        control = self._turn_controls.get(thread_id)
        if control is None:
            return False
        control.auto_accept_edits = auto_accept
        if auto_accept:
            thread = self._store.get_thread(thread_id)
            for gate in (thread.pending_controller_gates if thread is not None else []):
                if gate.kind != "edit":
                    continue
                if gate.payload.get("protected") or gate.payload.get("review_required"):
                    continue  # an explicit decision only (spec §3.9, §3.12)
                future = self._pending_edit.get(gate.gate_id)
                if future is not None and not future.done():
                    future.set_result({
                        "decision": "accept", "reason": "auto-accept turned on"})
        return True

    def _select_gate(
        self, thread_id: str, kind: str, gate_id: str | None,
    ) -> PendingGate | None:
        """The pending gate a decision targets (spec §4.5 route semantics).

        gate_id given → that gate, else GateNotFoundError (→ 404). gate_id omitted → the
        single pending gate of that kind (keeps pre-multi-gate clients working), None when
        there is none, GateAmbiguousError (→ 409) when there are several."""
        thread = self._store.get_thread(thread_id)
        gates = [g for g in (thread.pending_controller_gates if thread is not None else [])
                 if g.kind == kind]
        if gate_id is not None:
            match = next((g for g in gates if g.gate_id == gate_id), None)
            if match is None:
                raise GateNotFoundError(f"no pending {kind} gate {gate_id!r} on {thread_id}")
            return match
        if len(gates) > 1:
            raise GateAmbiguousError(
                f"{len(gates)} {kind} gates are pending on {thread_id}; pass gate_id")
        return gates[0] if gates else None

    async def resolve_edit(
        self, thread_id: str, decision: dict[str, object], gate_id: str | None = None,
    ) -> bool:
        """Resolve the per-edit gate (POST /edit-decision). Fires the future when a
        live waiter exists (never mutates/persists during the await — Class-A safety).

        Backend-restart orphan: when the EditGate persisted in sqlite but the in-memory
        waiter is gone (no future for its gate_id), clear the stale gate + write a
        breadcrumb so the UI unwedges (turn_active is already False post-restart → input
        re-enables). The user re-issues the edit. Matches the orphaned-task degradation."""
        gate = self._select_gate(thread_id, "edit", gate_id)
        if gate is None:
            return False
        fut = self._pending_edit.get(gate.gate_id)
        if fut is None or fut.done():
            # No live waiter (worker died mid-turn). The generation is NOT lost: the
            # edit was already applied to the shadow — only the promote didn't run —
            # and the persisted gate carries the exact paths it covered. So an accept
            # is reconstructible from durable state alone. Live incident: a 288-line
            # pathfinding.py was discarded here and survived only because the shadow
            # had not yet been rmtree'd by the next edit.
            promoted: list[str] = []
            if str(decision.get("decision", "")) == "accept":
                promoted = self._promote_orphaned_edit(thread_id, gate)
            # Clear AFTER promoting: the gate is the only record of which paths the
            # edit covered, so losing it first would strand the shadow again.
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._write_breadcrumb(
                thread_id, f"chat:{thread_id}",
                (f"✓ Recovered {len(promoted)} file(s) from the interrupted turn: "
                 + ", ".join(promoted))
                if promoted else
                "Previous turn ended — please re-send your request.")
            return bool(promoted)
        fut.set_result(decision)
        return True

    def _promote_orphaned_edit(self, thread_id: str, gate: PendingGate) -> list[str]:
        """Promote a dead turn's shadow edit into the real workspace.

        Reuses promote_files (the same call TurnEditSession.accept makes) so there is
        one promote path, and promotes ONLY the paths the gate recorded — never
        whatever else happens to be sitting in the shadow. Best-effort: recovery must
        never raise into the decision route.
        """
        if self._orchestrator is None:
            return []
        entries = gate.payload.get("diff_entries") or []
        paths = [str(e.get("path")) for e in entries if isinstance(e, dict) and e.get("path")]
        if not paths:
            return []
        try:
            wm = self._orchestrator._workspace_manager
            shadow = wm._resolve_shadow_path(f"chatturn-{thread_id}")
            if not shadow.exists():
                return []
            real = Path(self._workspace_path)
            present = [p for p in paths if (shadow / p).exists()]
            if not present:
                return []
            promote_files(shadow, real, present)
            logger.info("[controller] recovered %d orphaned edit file(s) for %s: %s",
                        len(present), thread_id, present)
            return present
        except Exception:
            logger.warning("[controller] orphaned-edit recovery failed for %s",
                           thread_id, exc_info=True)
            return []

    async def _command_approval_cb(
        self, thread_id: str, channel_id: str,
        command: str, args: list[str], cwd: str, *, child: AgentHandle | None = None,
    ) -> ApprovalOutcome:
        """Gate a run_command in a chat EDIT turn (mirror engine._build_command_approval_
        callback on the controller's thread-gate machinery instead of task status).

        ALLOW_ALL skips the gate; a workspace-remembered rule auto-approves (the same
        CommandRuleStore the task path uses, so approvals carry across both surfaces);
        otherwise raise a durable kind="command" gate and await /command-decision. The
        gate clears in place in the finally (Class-A); on approve+remember the rule is
        persisted via the shared rule_from_decision derivation."""
        if self._shell_policy == ShellPolicy.ALLOW_ALL:
            return ApprovalOutcome.allow()
        if CommandRuleStore(self._workspace_path).matches(command, args):
            return ApprovalOutcome.allow()

        gate = self._store.add_controller_gate(thread_id, PendingGate.new(
            "command", {"command": command, "args": args, "cwd": cwd,
                        "mentions_protected": command_mentions_protected(command, args)},
            agent=self._gate_agent(child)))
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[CommandDecision] = loop.create_future()
        self._pending_command[gate.gate_id] = fut
        # Instant-render poke (consistency with the task path's command_approval_requested):
        # the card still renders FROM /live (durable on reload) — this only nudges the FE
        # poll so it appears immediately instead of on the next 1s tick. Registered the
        # waiter first so a fast decision always finds it.
        poke: dict[str, object] = {
            "decision_id": uuid4().hex,
            "command": command,
            "args": args,
            "cwd": cwd,
            "step_id": "",
        }
        if child is not None:
            poke["agent"] = {"id": child.agent_id, "label": child.context.label,
                             "name": child.context.name}
        self._broadcaster.broadcast(channel_id, {
            "type": "command_approval_requested", "payload": poke})
        self._set_child_status(child, "waiting")
        denied_by: DeniedBy = "user"
        timeout = self._command_decision_timeout_sec
        try:
            decision = await (asyncio.wait_for(fut, timeout) if timeout > 0 else fut)
        except TimeoutError:
            decision = CommandDecision(approve=False)
            denied_by = "timeout"
        finally:
            self._pending_command.pop(gate.gate_id, None)
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._set_child_status(child, "running")

        rule = rule_from_decision(decision, command, args)
        if rule is not None:
            CommandRuleStore(self._workspace_path).add(rule)
        self._gate_breadcrumb(
            thread_id, channel_id,
            f"✓ Command approved: {command}" if decision.approve
            else f"✗ Command rejected: {command}", child)
        return ApprovalOutcome.from_command(decision, denied_by=denied_by)

    async def resolve_command(
        self, thread_id: str, decision: CommandDecision, gate_id: str | None = None,
    ) -> bool:
        """Resolve the run_command gate (POST /command-decision). Fires the live waiter;
        never mutates/persists during the await (Class-A). Restart orphan (gate in sqlite
        but the in-memory waiter died) clears the stale gate + a breadcrumb — mirrors
        resolve_edit so the UI unwedges and the user re-sends."""
        gate = self._select_gate(thread_id, "command", gate_id)
        if gate is None:
            return False
        fut = self._pending_command.get(gate.gate_id)
        if fut is None or fut.done():
            # Restart orphan: the gate outlived its waiter. Clear it + breadcrumb.
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._write_breadcrumb(
                thread_id, f"chat:{thread_id}",
                "Previous turn ended — please re-send your request.")
            return False
        fut.set_result(decision)
        return True

    async def _mcp_approval_cb(
        self, thread_id: str, channel_id: str,
        server: str, tool: str, args: dict[str, object], *,
        child: AgentHandle | None = None,
    ) -> ApprovalOutcome:
        """Gate an MCP tool call (mirror of _command_approval_cb on the same
        thread-gate machinery). A remembered (server, tool) rule auto-approves;
        otherwise raise a durable kind="mcp_tool" gate and await /mcp-decision."""
        from agentd.mcp.config import mcp_decision_timeout_sec
        from agentd.mcp.rules import McpRuleStore

        if McpRuleStore(self._workspace_path).matches(server, tool):
            return ApprovalOutcome.allow()

        gate = self._store.add_controller_gate(thread_id, PendingGate.new(
            "mcp_tool", {"server": server, "tool": tool, "args": args},
            agent=self._gate_agent(child)))
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[McpToolDecision] = loop.create_future()
        self._pending_mcp[gate.gate_id] = fut
        # Instant-render poke — the card still renders FROM /live (durable on reload).
        poke: dict[str, object] = {"server": server, "tool": tool, "args": args}
        if child is not None:
            poke["agent"] = {"id": child.agent_id, "label": child.context.label,
                             "name": child.context.name}
        self._broadcaster.broadcast(channel_id, {
            "type": "mcp_approval_requested", "payload": poke})
        self._set_child_status(child, "waiting")
        denied_by: DeniedBy = "user"
        timeout = mcp_decision_timeout_sec()
        try:
            decision = await (asyncio.wait_for(fut, timeout) if timeout > 0 else fut)
        except TimeoutError:
            decision = McpToolDecision(approve=False)
            denied_by = "timeout"
        finally:
            self._pending_mcp.pop(gate.gate_id, None)
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._set_child_status(child, "running")

        if decision.approve and decision.remember:
            McpRuleStore(self._workspace_path).add(server, tool)
        self._gate_breadcrumb(
            thread_id, channel_id,
            f"✓ MCP tool approved: {server}.{tool}" if decision.approve
            else f"✗ MCP tool rejected: {server}.{tool}", child)
        if decision.approve:
            return ApprovalOutcome.allow()
        return ApprovalOutcome.deny(denied_by)

    async def resolve_mcp(
        self, thread_id: str, decision: McpToolDecision, gate_id: str | None = None,
    ) -> bool:
        """Resolve the mcp_tool gate (POST /mcp-decision). Fires the live waiter;
        never mutates/persists during the await (Class-A). Restart orphan clears
        the stale gate + breadcrumb — mirrors resolve_command."""
        gate = self._select_gate(thread_id, "mcp_tool", gate_id)
        if gate is None:
            return False
        fut = self._pending_mcp.get(gate.gate_id)
        if fut is None or fut.done():
            # Restart orphan: the gate outlived its waiter. Clear it + breadcrumb.
            self._store.remove_controller_gate(thread_id, gate.gate_id)
            self._write_breadcrumb(
                thread_id, f"chat:{thread_id}",
                "Previous turn ended — please re-send your request.")
            return False
        fut.set_result(decision)
        return True

    def reap_subagents(self) -> None:
        """Startup reap (spec §11.5): children never survive a restart. Fail their rows,
        drop their gates (with a breadcrumb), and delete their leftover shadows. The
        parent's orphaned-edit recovery (_promote_orphaned_edit) is unchanged."""
        reaped = self._store.reap_agents("backend restarted")
        for thread_id in self._store.remove_child_gates():
            self._store.append_message(thread_id, ChatMessage(
                role="agent",
                content="✗ Sub-agent approvals were cleared — the backend restarted.",
                metadata={"breadcrumb": True}))
        if self._orchestrator is not None:
            root = self._orchestrator._workspace_manager._root_path
            for shadow in root.glob("chatturn-*-agent-*"):
                shutil.rmtree(shadow, ignore_errors=True)
        logger.info("[subagent] reap rows=%d", reaped)

    def forget_rewound_agents(
        self, thread_id: str, turn_ids: list[str], from_seq: int | None = None,
    ) -> int:
        """Remove what the rewound turns' children left (spec §11.6): their rows, their
        memory runs (best-effort), and the thread's write log — reset, because a stale
        log would refuse edits citing agents the rewind just erased; an empty one is
        always safe (§7.1)."""
        # By stamp (spec §8.10) — catches agents from continuation turns, which open no
        # checkpoint — plus by turn id for rows written before stamps existed.
        stamped = (self._store.delete_agents_from_checkpoint(thread_id, from_seq)
                   if from_seq is not None else [])
        agent_ids = stamped + self._store.delete_agents_for_turns(thread_id, turn_ids)
        for agent_id in agent_ids:
            try:
                self._memory_harness.forget_run(f"{thread_id}:{agent_id}")
            except Exception:  # noqa: BLE001 — memory never fails a rewind
                logger.warning("[subagent] memory cleanup failed id=%s", agent_id,
                               exc_info=True)
        self._write_logs.pop(thread_id, None)
        return len(agent_ids)

    async def stop_turn(self, thread_id: str) -> bool:
        """Cancel a detached turn (POST /stop) — a slimmer cousin of task /abort.

        Cancels the asyncio.Task; the turn's own finally chain does the cleanup:
        _run_turn pops _active_turns, ControllerLoop.run's finally closes the turn-
        shadow, and a held-open EditGate's _edit_decision_cb finally clears the gate +
        pops _pending_edit. Then broadcast chat_done so the relay closes, and write a
        durable ✗ Stopped breadcrumb. Benign no-op (False) if no active turn."""
        task = self._active_turns.get(thread_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task  # let the cancellation unwind (finally chain runs)
        except asyncio.CancelledError:
            pass
        channel_id = f"chat:{thread_id}"
        self._write_breadcrumb(thread_id, channel_id, "✗ Stopped")
        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
        return True

    def _mark_pills_boundary(self, thread_id: str) -> None:
        """Tell the running loop a durable message just landed in the transcript, so the
        pills it persists from here on start a NEW message positioned after it.

        Without this, the in-flight pills message (created at the turn's FIRST tool result
        and updated in place) keeps absorbing every later pill — and the closing text
        finalizes that same message — so on reload the whole turn's pills render as one
        block ahead of every breadcrumb/diff_card that actually preceded them. The live
        webview has no such divergence: its `appendMessage` reducer seals the streaming
        bubble for EVERY appended message (webview-ui/src/hooks/useAppState.ts), which is
        the rule this mirrors on the durable side.

        No-op between turns (see self._active_loops)."""
        loop = self._active_loops.get(thread_id)
        if loop is not None:
            loop.mark_pills_boundary()

    def _on_agent_status(self, handle: AgentHandle) -> None:
        """Persist a sub-agent status change and announce it on the THREAD channel — the
        low-volume events (spec §5.5, §11.2); everything else stays on the child's own."""
        self._store.update_agent(handle.agent_id, status=handle.status,
                                 started_at=handle.started_at, ended_at=handle.ended_at)
        if handle.status == "stopped":
            self._store.update_agent(handle.agent_id, stop_reason=handle.stop_reason)
        thread_channel = f"chat:{handle.thread_id}"
        self._broadcaster.broadcast(thread_channel, {
            "type": "agent_status",
            "payload": {"agent_id": handle.agent_id, "status": handle.status}})
        if handle.status in IDLE_STATUSES and handle.result is not None:
            result = handle.result
            self._store.update_agent(handle.agent_id, report=result.report,
                                     files_changed=result.files_changed,
                                     stale_refusals=result.stale_refusals)
            self._broadcaster.broadcast(thread_channel, {
                "type": "agent_finished",
                "payload": {"agent_id": handle.agent_id, "status": result.status,
                            "files_changed": result.files_changed}})

    @staticmethod
    def _gate_agent(child: AgentHandle | None) -> GateAgent | None:
        if child is None:
            return None
        return GateAgent(id=child.agent_id, label=child.context.label, name=child.context.name)

    def _set_child_status(self, child: AgentHandle | None, status: str) -> None:
        if child is not None and self._subagents is not None:
            self._subagents.set_status(child, status)

    def _gate_breadcrumb(
        self, thread_id: str, channel_id: str, text: str, child: AgentHandle | None,
    ) -> None:
        if child is None:
            self._write_breadcrumb(thread_id, channel_id, text)
        else:
            self._child_breadcrumb(child, text)

    def _child_breadcrumb(self, child: AgentHandle, text: str) -> None:
        """A sub-agent's decision record goes to ITS transcript and channel, never the
        parent's (spec §5.5); the boundary is the child loop's, not the parent's."""
        if isinstance(child.loop, ControllerLoop):
            child.loop.mark_pills_boundary()
        if child.broadcaster is not None:
            child.broadcaster.broadcast(agent_channel(child.thread_id, child.agent_id), {
                "type": "chat_breadcrumb", "payload": {"text": text, "task_id": ""}})
        if child.transcript is not None:
            child.transcript.append(ChatMessage(
                role="agent", content=text, metadata={"breadcrumb": True}))

    async def _child_command_approval_cb(
        self, child: AgentHandle, command: str, args: list[str], cwd: str,
    ) -> ApprovalOutcome:
        if (self._shell_policy == ShellPolicy.ALLOW_ALL
                or CommandRuleStore(self._workspace_path).matches(command, args)):
            return ApprovalOutcome.allow()
        if child.context.permission == "dontAsk":
            # dontAsk: only a remembered rule or allow_all runs a command; nobody is asked
            # (spec §5.6), and the model is told the truth (rev 11 §4.6.4).
            return ApprovalOutcome.deny("policy")
        return await self._command_approval_cb(
            child.thread_id, f"chat:{child.thread_id}", command, args, cwd, child=child)

    async def _child_mcp_approval_cb(
        self, child: AgentHandle, server: str, tool: str, args: dict[str, object],
    ) -> ApprovalOutcome:
        from agentd.mcp.rules import McpRuleStore

        if McpRuleStore(self._workspace_path).matches(server, tool):
            return ApprovalOutcome.allow()
        if child.context.permission == "dontAsk":
            return ApprovalOutcome.deny("policy")
        return await self._mcp_approval_cb(
            child.thread_id, f"chat:{child.thread_id}", server, tool, args, child=child)

    async def _child_edit_decision_cb(
        self, child: AgentHandle, diff: list[DiffEntry],
    ) -> dict[str, object]:
        return await self._edit_decision_cb(
            child.thread_id, f"chat:{child.thread_id}", diff, child=child)

    def live_agents(self, thread_id: str) -> list[dict[str, object]]:
        """/live's roster (spec §11.1): every agent of the in-flight turn's dispatch tree,
        running and finished. Empty once the turn ends (the UI then reads the routes)."""
        turn_id = self._live_turns.get(thread_id)
        if self._subagents is None or turn_id is None:
            return []
        log = self._write_log_for(thread_id)
        roster: list[dict[str, object]] = []
        for handle in self._subagents.registry.for_turn(thread_id, turn_id):
            calls = handle.loop.tool_calls if isinstance(handle.loop, ControllerLoop) else []
            now = ""
            if calls:
                last = calls[-1]
                target = last.arguments.get("path") or last.arguments.get("command") or ""
                now = f"{last.tool_name} {target}".strip()
            if handle.result is not None:
                files = len(handle.result.files_changed)
            elif log is not None:
                files = len(log.files_changed_by(
                    self._subagents.registry.subtree_ids(handle.agent_id)))
            else:
                files = 0
            roster.append({
                "agent_id": handle.agent_id, "parent_agent_id": handle.context.parent_agent_id,
                "depth": handle.context.depth, "name": handle.context.name,
                "label": handle.context.label, "status": handle.status, "now": now,
                "tool_count": len(calls), "files_changed_count": files,
                "started_at": handle.started_at.isoformat() if handle.started_at else None,
                "ended_at": handle.ended_at.isoformat() if handle.ended_at else None,
                # UI-only preview; the model always gets the full report (D8).
                "report_preview": handle.result.report[:200] if handle.result else "",
            })
        return roster

    def _report_guard(self, handle: AgentHandle) -> str | None:
        """A child cannot report while its agents run or their reports wait undrained
        (spec §4.2): an agent turns idle before its report is drained."""
        assert self._subagents is not None
        running = [self._store.get_agent(i) for i in self._store.child_agent_ids(handle.agent_id)
                   if self._subagents.is_active(i)]
        if running:
            labels = ", ".join(r.label for r in running if r is not None)
            return f"You have running agents ({labels}) — call wait_agents or stop_agent first."
        if self._subagents.has_pending_report(handle.agent_id):
            return "New reports arrived from your agents — read them before reporting."
        return None

    def _on_leftover(self, handle: AgentHandle, items: list[InboxItem]) -> None:
        """Input that arrived after the last drain re-activates a lone agent (spec §3.6).
        Scheduled, not run inline: the supervisor calls this from inside the finishing
        activation's task."""
        text = "\n\n".join(
            "New message:\n" + frame(i.author or i.source_id or "agent", i.kind, i.text)
            for i in items)
        asyncio.get_running_loop().call_soon(self._start_activation, handle, text)

    def _start_activation(self, handle: AgentHandle, activation_input: str) -> None:
        assert self._subagents is not None
        fresh = self._handle_from_record(handle.thread_id, handle.agent_id)
        fresh.activation_input = activation_input
        self._subagents.enqueue(fresh, self._activate)

    def _handle_from_record(self, thread_id: str, agent_id: str) -> AgentHandle:
        """Rebuild an agent from its row (spec §3.2): idle agents have no in-memory handle,
        and after a restart nothing of them is in memory at all."""
        record = self._store.get_agent(agent_id)
        if record is None or record.thread_id != thread_id:
            raise AgentNotFoundError(f"no agent {agent_id!r} in thread {thread_id!r}")
        definition = definition_from_json(record.definition) if record.definition else (
            self._agent_catalog()[record.name])
        permission = effective_permission(
            definition.permission, "plan" if record.inherited.get("read_only") else None)
        context = AgentContext(
            agent_id=record.agent_id, name=record.name, label=record.label,
            depth=record.depth, parent_agent_id=record.parent_agent_id,
            permission=permission,
            allowed_types=child_allowed_types(permission, can_edit=definition_allows_edit(
                definition.tools, definition.disallowed_tools)),
            persona=definition.persona,
            max_iters=definition.max_turns or subagent_max_iters())
        return AgentHandle(context=context, definition=definition, prompt=record.prompt,
                           thread_id=thread_id, turn_id=record.turn_id, status=record.status)

    def resume_agent(
        self, thread_id: str, agent_id: str, message: str, *, caller_id: str = "main",
    ) -> AgentHandle:
        """Send an idle agent a follow-up; it continues with its full context (spec §4.3).
        The seam Phase 2's `message_agent` tool calls. Returns the queued handle."""
        assert self._subagents is not None
        record = self._store.get_agent(agent_id)
        if record is None or record.thread_id != thread_id:
            raise AgentNotFoundError(f"no agent {agent_id!r} in thread {thread_id!r}")
        if record.dispatcher_id != caller_id:
            raise AgentNotYoursError(
                f"agent {record.label!r} was dispatched by {record.dispatcher_id!r}, "
                f"not {caller_id!r}")
        if self._subagents.is_active(agent_id) or record.status in LIVE_STATUSES:
            raise AgentBusyError(
                f"agent {record.label!r} is still running — wait for its report or stop it")
        caller = "main" if caller_id == "main" else (
            (self._store.get_agent(caller_id) or record).label)
        text = f"Message from {caller}:\n{message}"
        if record.status in ("failed", "failed_transient", "stopped"):
            text = f"Your previous run ended {record.status}: {record.report}\n\n{text}"
        handle = self._handle_from_record(thread_id, agent_id)
        handle.activation_input = text
        self._subagents.enqueue(handle, self._activate)
        return handle

    async def stop_agent(self, thread_id: str, agent_id: str) -> bool:
        """Stop one sub-agent and its subtree; siblings continue (spec §4.4)."""
        if self._subagents is None:
            return False
        record = self._store.get_agent(agent_id)
        if record is None or record.thread_id != thread_id:
            return False
        return await self._subagents.stop(agent_id, "user")

    def _agent_catalog(self) -> dict[str, AgentDefinition]:
        if self._agent_catalog_loader is None:
            return BUILTIN_AGENTS
        return self._agent_catalog_loader.load()

    def _dispatch_source(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
    ) -> SubAgentToolSource:
        return SubAgentToolSource(
            self._agent_catalog(), partial(self._dispatch, thread_id, turn_id, dispatcher))

    async def _dispatch(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
        requests: list[DispatchRequest],
    ) -> list[tuple[AgentHandle, ChildResult]]:
        runtime = self._subagents
        log = self._write_log_for(thread_id)
        if runtime is None or log is None:
            raise RuntimeError("dispatch_agents was offered while sub-agents are disabled")
        parent_permission = dispatcher.context.permission if dispatcher is not None else None
        depth = dispatcher.context.depth + 1 if dispatcher is not None else 1
        handles: list[AgentHandle] = []
        for request in requests:
            permission = effective_permission(request.agent.permission, parent_permission)
            context = AgentContext(
                agent_id=new_agent_id(), name=request.agent.name, label=request.label,
                depth=depth,
                parent_agent_id=dispatcher.agent_id if dispatcher is not None else None,
                permission=permission, allowed_types=child_allowed_types(
                    permission, can_edit=definition_allows_edit(
                        request.agent.tools, request.agent.disallowed_tools)),
                persona=request.agent.persona,
                max_iters=request.agent.max_turns or subagent_max_iters())
            # Writes before this instant are not stale for the new agent (spec §7.3).
            log.register_agent(context.agent_id)
            handles.append(AgentHandle(
                context=context, definition=request.agent, prompt=request.prompt,
                thread_id=thread_id, turn_id=turn_id))
        self._on_dispatch_start(thread_id, turn_id, dispatcher, handles)
        if dispatcher is None:
            self._inflight_dispatch[thread_id] = handles
        for handle in handles:
            handle.activation_input = handle.prompt
        results = await runtime.dispatch(handles, self._activate, dispatcher=dispatcher)
        if dispatcher is None:
            # Not in a finally: a cancel must leave it for _run_loop's stop branch.
            self._inflight_dispatch.pop(thread_id, None)
        return list(zip(handles, results, strict=True))

    def _on_dispatch_start(
        self, thread_id: str, turn_id: str, dispatcher: AgentHandle | None,
        handles: list[AgentHandle],
    ) -> None:
        """Make a dispatch durable before any child runs (spec §6.1), so a mid-run reload
        shows the roster: the anchor message, one row and one agent_started per child."""
        roster = ChatMessage(
            role="agent", content="", type="agent_dispatch",
            metadata={"agent_ids": [h.agent_id for h in handles], "turn_id": turn_id})
        event = {"type": "agent_dispatch",
                 "payload": {"message": roster.model_dump(mode="json")}}
        if dispatcher is None:
            self._mark_pills_boundary(thread_id)
            self._store.append_message(thread_id, roster)
            self._broadcaster.broadcast(f"chat:{thread_id}", event)
        else:
            if isinstance(dispatcher.loop, ControllerLoop):
                dispatcher.loop.mark_pills_boundary()
            # Broadcast first: the persisted copy then records the seq of the event that
            # produced it, so backfill-then-subscribe never shows the roster twice.
            if dispatcher.broadcaster is not None:
                dispatcher.broadcaster.broadcast(
                    agent_channel(thread_id, dispatcher.agent_id), event)
            if dispatcher.transcript is not None:
                dispatcher.transcript.append(roster)
        thread_channel = f"chat:{thread_id}"
        for handle in handles:
            ctx = handle.context
            dispatcher_row = (self._store.get_agent(dispatcher.agent_id)
                              if dispatcher is not None else None)
            # Rewind stamp (spec §8.10): main-dispatched agents take the thread's current
            # checkpoint; an agent's agents inherit it, so a subtree rewinds as one unit.
            stamp = (dispatcher_row.checkpoint_seq if dispatcher_row is not None
                     else self._store.current_checkpoint_seq(thread_id))
            self._store.insert_agent(AgentRecord(
                agent_id=ctx.agent_id, thread_id=thread_id, turn_id=turn_id,
                parent_agent_id=ctx.parent_agent_id, depth=ctx.depth, name=ctx.name,
                label=ctx.label, prompt=handle.prompt, status=handle.status,
                definition=definition_to_json(handle.definition),
                dispatcher_id=dispatcher.agent_id if dispatcher is not None else "main",
                checkpoint_seq=stamp,
                inherited={"read_only": dispatcher is not None
                           and dispatcher.context.permission == "plan"}))
            self._broadcaster.broadcast(thread_channel, {
                "type": "agent_started",
                "payload": {"agent_id": ctx.agent_id, "parent_agent_id": ctx.parent_agent_id,
                            "depth": ctx.depth, "name": ctx.name, "label": ctx.label}})
            logger.info("[subagent] start id=%s parent=%s depth=%d name=%s",
                        ctx.agent_id, ctx.parent_agent_id, ctx.depth, ctx.name)

    async def _activate(self, handle: AgentHandle) -> ChildResult:
        """Build and run one sub-agent (spec §5): its own context window, tool set,
        shadow, channel and transcript, on the shared workspace guarded by the thread's
        write log."""
        ctx = handle.context
        thread_id, turn_id = handle.thread_id, handle.turn_id
        log = self._write_log_for(thread_id)
        assert log is not None and self._subagents is not None
        log.ensure_agent(ctx.agent_id)
        record = self._store.get_agent(ctx.agent_id)
        assert record is not None  # rows are written before any activation runs
        activation = record.activation_count + 1
        activation_input = handle.activation_input or handle.prompt
        channel = agent_channel(thread_id, ctx.agent_id)
        broadcaster = SequencedBroadcaster(self._broadcaster, channel,
                                           initial_seq=record.last_seq)
        transcript = AgentTranscript(
            partial(self._store.set_agent_transcript, ctx.agent_id),
            lambda: broadcaster.last_seq, initial=record.transcript)
        handle.broadcaster, handle.transcript = broadcaster, transcript
        if activation > 1:
            transcript.append(ChatMessage(
                role="agent", content=_divider_text(activation_input),
                metadata={"divider": True}))
        self._store.update_agent(ctx.agent_id, activation_count=activation,
                                 activation_started_at=datetime.now(UTC))
        self._store.clear_report_delivered(ctx.agent_id)
        workspace = Path(self._workspace_path)
        run_id = f"{thread_id}:{ctx.agent_id}"
        ledger = TodoLedger()  # in memory only: never written to thread columns (§5.2)
        active_skills: dict[str, str] = {}
        if handle.definition.skills:
            self._preseed_child_skills(handle, active_skills)
        may_dispatch = ctx.depth < subagent_max_depth()

        def sources(render_ctx: RenderContext | None) -> list[object]:
            built: list[object] = [
                BuiltinToolSource(
                    shadow_root=workspace, real_workspace_path=workspace,
                    semantic_index=getattr(self._retrieval, "_semantic_index", None),
                    command_approval_callback=partial(self._child_command_approval_cb, handle),
                    render_ctx=render_ctx,
                    read_observer=log.read_observer(workspace, ctx.agent_id),
                    command_guard=vcs_refusal),
                TodoToolSource(ledger, render_ctx=render_ctx),
            ]
            memory_source = self._memory_harness.memory_tool_source(
                run_id, allow_remember=False)
            if memory_source is not None:
                built.append(memory_source)
            if is_skills_enabled():
                built.append(SkillToolSource(
                    SkillCatalogLoader(self._workspace_path), active_skills, additive=True))
            if self._mcp_manager is not None:
                from agentd.mcp.tool_source import McpToolSource

                built.append(McpToolSource(
                    self._mcp_manager, partial(self._child_mcp_approval_cb, handle),
                    render_ctx=render_ctx))
            if may_dispatch:
                built.append(self._dispatch_source(thread_id, turn_id, handle))
            return built

        available = [d.name for d in AggregatingToolRegistry(sources(None)).definitions()]
        names = child_tool_names(available, definition_tools=handle.definition.tools,
                                 permission=ctx.permission, may_dispatch=may_dispatch,
                                 definition_disallowed=handle.definition.disallowed_tools)
        render_ctx = RenderContext.for_agent(
            ctx, tools=names,
            shell_policy="allow_all" if self._shell_policy == ShellPolicy.ALLOW_ALL else "ask")
        registry = AggregatingToolRegistry(sources(render_ctx), allowed_tools=names)
        edit_session_factory = (
            (lambda: TurnEditSession(
                turn_id=f"{thread_id}-{ctx.agent_id}", real_path=workspace,
                workspace_manager=self._orchestrator._workspace_manager,
                patch_engine=self._orchestrator._patch_engine,
                # Children's edits land in the parent turn's checkpoint (first-seen-wins),
                # so rewinding before the turn reverts the whole tree (spec §7.5).
                checkpoint_cb=(
                    partial(self._rewind.capture, thread_id)
                    if self._rewind is not None else None),
                write_guard=WriteGuard(log, ctx.agent_id, ctx.label, ctx.name),
                protection=AgentProtection()))
            if self._orchestrator is not None else None)
        parent_control = self._turn_controls.get(thread_id)
        control = (parent_control
                   if follows_live_review(ctx.permission) and parent_control is not None
                   else ChatTurnControl(auto_accept_edits=True))
        engine = (self._reasoning if handle.definition.model == "inherit"
                  else self._reasoning.with_model(handle.definition.model))
        loop = ControllerLoop(
            engine, registry, broadcaster, channel_id=channel,
            phase_sm=ControllerPhaseSM(start="AGENT"),
            edit_session_factory=edit_session_factory, todo_ledger=ledger,
            memory_harness=self._memory_harness, active_skills=active_skills,
            progress_note_cb=partial(self._child_progress_note, handle),
            pills_seal_cb=transcript.seal_pills, render_ctx=render_ctx, agent=ctx)
        handle.loop = loop
        plan_context: dict[str, object] = {
            "goal": activation_input, "workspace_path": self._workspace_path, "run_id": run_id,
            # Nests this child's controller-turn-NN / memory-recall-NN dumps under
            # chat/<thread>/<turn>/agents/<agent>/ (spec §5.2) with no engine change.
            "artifact_thread_id": thread_id,
            "artifact_turn_id": f"{turn_id}/agents/{ctx.agent_id}/a{activation}",
            "artifact_seed_len": 0, "edit_is_resume": False}

        def subtree_files() -> list[str]:
            ids = self._store.subtree_agent_ids(ctx.agent_id)
            stored = {f for i in ids for f in (self._store.get_agent(i) or record).files_changed}
            return sorted(stored | set(log.files_changed_by(ids)))

        status, report = "failed", ""
        try:
            # No retrieval_delta_cb: the delta references a seed children never had.
            outcome = await loop.run(
                plan_context, max_iters=ctx.max_iters, turn_control=control,
                seed_history=[*record.history, {"role": "user", "content": activation_input}],
                iteration_cb=partial(self._store.set_agent_history, ctx.agent_id),
                inbox_drain=partial(self._subagents.drain, ctx.agent_id),
                report_guard=partial(self._report_guard, handle),
                edit_decision_cb=partial(self._child_edit_decision_cb, handle),
                edit_record_cb=partial(self._child_edit_record_cb, handle),
                on_pills_update=transcript.upsert_pills)
            if outcome.kind == "report":
                status = str((outcome.payload or {}).get("status", "completed"))
                if status == "awaiting_peer":
                    status = "partial"  # a lone agent has no peer; teams arrive in Phase 4
                report = outcome.text
                if status == "partial":
                    # The forced-final report: stop the agents it never waited for (§4.2).
                    for child_id in self._store.child_agent_ids(ctx.agent_id):
                        await self._subagents.stop(child_id, "cascade")
            else:
                report = loop.fallback_report(
                    f"ended without a report ({outcome.text or outcome.kind})",
                    subtree_files())
        except asyncio.CancelledError:
            self._store.set_agent_history(ctx.agent_id, loop.partial_history())
            handle.result = self._close_child(handle, "stopped", loop.fallback_report(
                "stopped before reporting", subtree_files(), status="stopped"))
            raise
        except ProviderUnavailable as exc:
            logger.warning("[subagent] provider unavailable id=%s: %s", ctx.agent_id, exc)
            status = "failed_transient"
            report = loop.fallback_report(f"provider unavailable: {exc}", subtree_files(),
                                          status="failed_transient")
        except Exception as exc:
            logger.exception("[subagent] child failed id=%s", ctx.agent_id)
            self._store.set_agent_history(ctx.agent_id, loop.partial_history())
            report = loop.fallback_report(str(exc), subtree_files())
        return self._close_child(handle, status, report)

    def _close_child(self, handle: AgentHandle, status: str, report: str) -> ChildResult:
        """The activation's final bookkeeping on every exit (spec §3.2, §5.5, §8)."""
        ctx = handle.context
        log = self._write_log_for(handle.thread_id)
        ids = self._store.subtree_agent_ids(ctx.agent_id)
        this_activation = log.files_changed_by(ids) if log is not None else []
        files = self._store.merge_agent_files(ctx.agent_id, this_activation)
        for other in ids - {ctx.agent_id}:
            files = sorted(set(files) | set(self._store.get_agent(other).files_changed  # type: ignore[union-attr]
                                            if self._store.get_agent(other) else []))
        result = ChildResult(
            status=status, report=report, files_changed=files,
            stale_refusals=log.stale_refusals(ctx.agent_id) if log is not None else 0)
        if handle.transcript is not None:
            handle.transcript.append(ChatMessage(
                role="agent", content=report, metadata={"report": True, "status": status}))
        if handle.loop is not None and isinstance(handle.loop, ControllerLoop):
            self._store.set_agent_history(ctx.agent_id, handle.loop.partial_history())
        if handle.broadcaster is not None:
            self._store.update_agent(ctx.agent_id, last_seq=handle.broadcaster.last_seq)
        self._store.update_agent(ctx.agent_id, activation_ended_at=datetime.now(UTC))
        # Its gates can never be answered now; its recall caches and replay buffer are
        # done — the replay map is otherwise never pruned (late viewers backfill instead).
        self._store.clear_controller_gates(handle.thread_id, agent_id=ctx.agent_id)
        self._memory_harness.release_run(f"{handle.thread_id}:{ctx.agent_id}")
        self._broadcaster.clear_replay(agent_channel(handle.thread_id, ctx.agent_id))
        return result

    def _preseed_child_skills(self, handle: AgentHandle, active_skills: dict[str, str]) -> None:
        """`skills:` frontmatter pre-loads every listed skill (spec §5.2), capped like
        read_skill. Unknown or unreadable names are skipped with a warning."""
        name = handle.context.name
        if not is_skills_enabled():
            logger.warning("[subagent] agent %s lists skills but CRUCIBLE_SKILLS_ENABLED is "
                           "off — none pre-loaded", name)
            return
        catalog = {m.name: m for m in SkillCatalogLoader(self._workspace_path).load_catalog()}
        for skill in handle.definition.skills:
            manifest = catalog.get(skill)
            if manifest is None:
                logger.warning("[subagent] agent %s: unknown skill %r — skipped", name, skill)
                continue
            try:
                body = manifest.body_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[subagent] agent %s: cannot read skill %r: %s", name, skill, exc)
                continue
            active_skills[skill] = cap_skill_body(skill, body)

    async def _child_edit_record_cb(
        self, child: AgentHandle, diff: list[DiffEntry], decision: str, reason: str,
        was_gated: bool,
    ) -> None:
        """The child twin of _edit_record_cb: the inert diff card and breadcrumbs go to the
        child's transcript and channel (spec §5.5). The event is broadcast first, so the
        persisted message records the seq of the event that produced it."""
        diff_payload = [
            {"path": d.path, "additions": d.additions,
             "deletions": d.deletions, "unified_diff": d.unified_diff}
            for d in diff]
        resolved = "applied" if decision == "accept" else "discarded"
        if isinstance(child.loop, ControllerLoop):
            child.loop.mark_pills_boundary()
        if child.broadcaster is not None:
            child.broadcaster.broadcast(agent_channel(child.thread_id, child.agent_id), {
                "type": "diff_ready",
                "payload": {"diff_entries": diff_payload, "resolved": resolved}})
        if child.transcript is not None:
            child.transcript.append(ChatMessage(
                role="agent", content="", type="diff_card",
                metadata={"diff_entries": diff_payload, "resolved": resolved}))
        files = ", ".join(d.path for d in diff) or "(no files)"
        if decision == "stale":
            self._child_breadcrumb(child, f"✗ Not applied: {reason}")
        elif was_gated and decision == "accept":
            self._child_breadcrumb(child, f"✓ Edit accepted: {files}")
        elif was_gated:
            self._child_breadcrumb(
                child, f"✗ Edit rejected: {files}" + (f" — {reason}" if reason else ""))

    async def _child_progress_note(self, child: AgentHandle, note: str) -> None:
        """Persist a child's progress note to ITS transcript (the loop already broadcast
        chat_progress on the child's channel)."""
        if child.transcript is not None:
            child.transcript.append(ChatMessage(
                role="agent", content=note, metadata={"progress": True}))

    def _write_breadcrumb(self, thread_id: str, channel_id: str, text: str) -> None:
        """Persist a durable transcript breadcrumb AND broadcast it live (mirror
        engine.write_chat_breadcrumb). The live mode/edit gate is ephemeral; this is
        the permanent record of the user's decision so history reads as a narrative."""
        self._mark_pills_boundary(thread_id)
        self._store.append_message(thread_id, ChatMessage(
            role="agent", content=text, type="text", metadata={"breadcrumb": True}))
        self._broadcaster.broadcast(channel_id, {
            "type": "chat_breadcrumb", "payload": {"text": text, "task_id": ""}})

    async def _progress_note_cb(self, thread_id: str, note: str) -> None:
        """Persist a non-terminal `progress` note as a durable transcript message —
        the reload half. The live `chat_progress` event is already broadcast by the
        loop itself (ControllerLoop._iterate), so this must persist ONLY — broadcasting
        here too would duplicate the live signal. async to match the loop's `await
        self._progress_note_cb(note)` call site; the body is a single sync sqlite
        write (mirrors _persist_todos/_persist_active_skill), no thread pool needed.

        The note is a pills-segment boundary like any other mid-turn durable message, so
        it lands after the pills accumulated so far instead of ending up ahead of the
        eventual closing (answer/submit_changes) message, which finalizes that SAME
        in-flight message object in place. The loop marks the boundary itself at the
        `progress` dispatch (ControllerLoop.mark_pills_boundary) — nothing to do here."""
        self._store.append_message(thread_id, ChatMessage(
            role="agent", content=note, type="text", metadata={"progress": True}))

    async def resolve_mode(
        self, thread_id: str, mode: str, *, channel_id: str, goal: str,
    ) -> None:
        """Resolve the mode gate (POST /mode-decision). Clears the gate in place
        (Class-A), writes a breadcrumb, then dispatches: "implement" exits Plan Mode
        and re-enters the loop in ACTIVE (a new streamed turn); create_task/resume
        hand off to the orchestrator."""
        # Precondition + idempotency guard: only a pending `mode` gate may resolve.
        # The read→clear pair has no `await` between it (sqlite is sync), so two
        # concurrent /mode-decision posts can't both dispatch (which would double-
        # create a task). The second finds the gate already cleared and no-ops.
        gate = self._select_gate(thread_id, "mode", None)
        if gate is None:
            logger.info("[controller] resolve_mode no-op: no pending mode gate (thread=%s)",
                        thread_id)
            return
        # Backward-compat (spec edge case): a mode gate persisted BEFORE the merged-phase
        # deploy may carry a legacy mode ("edit"/"explain") the new dispatch no longer
        # knows. Rather than strand it in the graceful-degrade "not available yet" else
        # branch below, map ONLY those two known legacy values to "implement" — the
        # closest live equivalent (exit to ACTIVE and make the change). Deliberately
        # narrow: any OTHER unrecognized mode string (a genuinely invalid/garbled
        # client value, not a known legacy one) still falls through to the existing
        # graceful-degrade path below rather than being silently treated as "go ahead
        # and make a change" — this endpoint takes untrusted input, so only a known,
        # enumerated legacy value gets remapped, not "anything we don't recognize."
        if mode in ("edit", "explain"):
            logger.info("[controller] resolve_mode: legacy mode %r → implement", mode)
            mode = "implement"
        if mode in ("create_task", "resume") and not self._task_subsystem_enabled:
            raise ValueError(
                "task subsystem is disabled (CRUCIBLE_TASK_SUBSYSTEM=0) — only \"implement\" "
                "is available; the controller handles changes inline.")
        # Friendly record of the choice — read the option label from the gate BEFORE
        # clearing it so the breadcrumb reads "▸ You chose: Edit inline now" not a raw mode.
        label = mode
        for opt in (gate.payload.get("options") or []):
            if isinstance(opt, dict) and opt.get("mode") == mode:
                label = str(opt.get("label") or mode)
                break
        # The agreed plan_sketch is the concrete plan the user approved — an LLM synthesis
        # of the WHOLE conversation. Use it as the effective goal for EVERY mode re-entry
        # (read from the gate BEFORE clearing), not the vague trigger message ("let's do
        # this") the route forwards. Falls back to the raw goal if the model omitted it.
        sketch = str(gate.payload.get("plan_sketch") or "").strip()
        effective_goal = sketch or goal
        self._store.remove_controller_gate(thread_id, gate.gate_id)
        # PERSIST + broadcast (mirror engine.write_chat_breadcrumb): a bare broadcast
        # dies on reload, leaving no record of what the user chose.
        self._write_breadcrumb(thread_id, channel_id, f"▸ You chose: {label}")

        if mode == "implement":
            if self._orchestrator is None:
                raise RuntimeError("implement mode requires an orchestrator")
            phase = "ACTIVE"
            review = self._step_review_by_thread.get(thread_id)
            seed_history = self._seed_for(thread_id)
            # The re-entry is a full turn — give it a turn_id too so it gets incremental
            # pill persistence (finding 5) AND debug artifacts, like the handle_message path.
            turn_id = uuid4().hex
            outcome = await self._run_loop(
                thread_id, channel_id, effective_goal,
                seed_history=seed_history, step_review=review, phase=phase, turn_id=turn_id)
            await self._finish(
                thread_id, channel_id, outcome, step_review=review, turn_id=turn_id)
            return

        if mode == "create_task":
            if self._orchestrator is None:
                raise RuntimeError("create_task mode requires an orchestrator")
            # Thread the "Review each step" toggle through to the task (matches the
            # edit path + the old ChatAgent large_change handoff): True → gate each
            # step, None → env default.
            review = self._step_review_by_thread.get(thread_id)
            # Forward every tool call in the thread's history as the planner's
            # pre_explored_context (parity with ChatAgent large_change) so it doesn't
            # re-explore cold. Derived from the verbatim (uncapped) conversation —
            # restart-durable via _seed_for, one source of truth with seed_history.
            explore_context = _explore_context_from_history(self._seed_for(thread_id))
            # effective_goal = the plan_sketch (LLM synthesis of the whole conversation),
            # falling back to the raw goal — see where it's computed above. A task gets no
            # chat history, so this is its ONLY intent signal.
            task_id = await self._orchestrator.create_task_from_chat(
                thread_id=thread_id, goal=effective_goal, workspace_path=self._workspace_path,
                explore_context=explore_context, store=self._store,
                step_review_auto_accept=(not review) if review is not None else None)
            self._store.append_message(thread_id, ChatMessage(
                role="agent", content=task_id, type="task_card", task_id=task_id,
                metadata={"taskId": task_id}))
            self._broadcaster.broadcast(
                channel_id, {"type": "task_card", "payload": {"task_id": task_id}})
            await self._orchestrator.await_plan_ready(task_id)
        else:
            # resume is offered only when a resumable recent task exists; that
            # plumbing isn't wired in v1, so degrade gracefully rather than guess.
            # Persist (not broadcast-only) so the note survives a reload like every
            # other decision record — no live-only crumb (the bug class we're fixing).
            logger.warning("[controller] unhandled mode %r — no dispatch", mode)
            self._write_breadcrumb(
                thread_id, channel_id, f"Mode {mode!r} is not available yet.")
        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})

    async def resolve_clarify(
        self, thread_id: str, answer: str, *, channel_id: str, goal: str,
    ) -> None:
        """Resolve the clarify gate (POST /clarify-decision). Clears the gate in place
        (Class-A), writes ONE combined `❓ q → a` breadcrumb, then re-enters the loop with
        the answer injected as the user's reply — ACTIVE if the clarify fired mid-ACTIVE,
        PLAN if it fired mid-PLAN — a fresh streamed turn, like resolve_mode's dispatch.

        Idempotency + empty guards: the read→clear pair has no `await` between it (sqlite
        is sync), so two concurrent posts can't both re-enter; a blank answer no-ops (the
        card shouldn't submit blank, but defend)."""
        answer = (answer or "").strip()
        gate = self._select_gate(thread_id, "clarify", None)
        if gate is None:
            logger.info("[controller] resolve_clarify no-op: no pending clarify gate (thread=%s)",
                        thread_id)
            return
        if not answer:
            logger.info("[controller] resolve_clarify no-op: empty answer (thread=%s)", thread_id)
            return
        question = str(gate.payload.get("question") or "")
        resume_phase = gate.payload.get("resume_phase")
        resume_phase = resume_phase if resume_phase in ("PLAN", "ACTIVE") else None
        self._store.remove_controller_gate(thread_id, gate.gate_id)
        # PERSIST + broadcast (mirror write_chat_breadcrumb): a bare broadcast dies on
        # reload, leaving no record of the Q the agent asked or the A the user gave.
        self._write_breadcrumb(thread_id, channel_id, f"❓ {question} → {answer}")

        # Re-enter: the answer is the user's reply, seeded onto prior history. goal stays
        # the original goal (the answer rides as history, not as the goal).
        review = self._step_review_by_thread.get(thread_id)
        seed_history = (self._seed_for(thread_id) or []) + [
            {"role": "user", "content": answer}]
        turn_id = uuid4().hex
        outcome = await self._run_loop(
            thread_id, channel_id, goal, seed_history=seed_history,
            step_review=review, phase=resume_phase, turn_id=turn_id,
            edit_is_resume=(resume_phase == "ACTIVE"))
        await self._finish(
            thread_id, channel_id, outcome, step_review=review, turn_id=turn_id)
