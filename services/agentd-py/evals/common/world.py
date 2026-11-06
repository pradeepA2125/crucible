"""The real chat controller, built for a trial's temp workspace (injection spec §3,
observability spec §6.3). Every collaborator is the factory's own; the reasoning engine is
only wrapped to record the actions it chose (and, in the harnesses' own tests, replaced by a
script)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentd.chat.controller import ChatController
from agentd.chat.controller_factory import select_chat_handler
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ShellPolicy
from agentd.reasoning.react_common import accepts_kwarg
from evals.common.cards import GateRecord

MAX_CONTINUATIONS = 3


class ActionRecorder:
    """Wraps the reasoning engine and keeps every action the agent chose, including edits the
    patch engine later rejects (those leave no diff card) and calls the loop refuses."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.actions: list[dict[str, Any]] = []

    async def create_controller_step(self, **kwargs: Any) -> Any:
        fn = self._inner.create_controller_step
        step = await fn(**{k: v for k, v in kwargs.items() if accepts_kwarg(fn, k)})
        if isinstance(step, dict):
            self.actions.append(step)
        return step

    def __getattr__(self, name: str) -> Any:
        # The Auto judge and provider plumbing read the engine's transport and model.
        return getattr(self._inner, name)


def build_controller(
    ws: Path, state: Path, store: ChatThreadStore, *, engine: Any, backend: str,
    model: str,
) -> tuple[ChatController, ActionRecorder]:
    """`engine` is a scripted engine for a harness's own tests; None means the live provider
    from the environment."""
    # Built inside the trial's environment: flags and Path.home() are read at construction.
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.patch.engine import PatchEngine
    from agentd.reasoning.engine import DefaultReasoningEngine
    from agentd.storage.sqlite_store import SQLiteTaskStore
    from agentd.validation.command_validator import CommandValidator
    from agentd.workspace.shadow import ShadowWorkspaceManager

    scripted = engine
    if scripted is None:
        from agentd.providers.factory import build_transport, resolve_model
        transport = build_transport(backend)
        model = model or resolve_model(backend)
    else:
        transport = None
    # The chat controller's edit sessions use the orchestrator's shadow manager and patch
    # engine, exactly as main.py wires them; no task ever runs here.
    orchestrator = AgentOrchestrator(
        store=SQLiteTaskStore(database_path=state / "agentd.sqlite3"),
        reasoning_engine=scripted or DefaultReasoningEngine(model=model, transport=transport),
        validator=CommandValidator.from_env(), patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=state / "shadows"),
        chat_store=store)
    ctrl = select_chat_handler(
        workspace_path=str(ws), transport=transport, model=model, thread_store=store,
        orchestrator=orchestrator, broadcaster=orchestrator.broadcaster,
        shell_policy=ShellPolicy.ASK)
    if not isinstance(ctrl, ChatController):
        raise RuntimeError("the eval needs the chat controller (CRUCIBLE_CHAT_CONTROLLER)")
    # Every collaborator stays the factory's; the engine is only wrapped (and, in the
    # harness's own tests, replaced by a script).
    actions = ActionRecorder(scripted if scripted is not None else ctrl._reasoning)  # noqa: SLF001
    ctrl._reasoning = actions  # noqa: SLF001
    return ctrl, actions


async def start_controller(ctrl: ChatController, *, live: bool, backend: str, model: str) -> None:
    """What main.py does at startup: connect MCP servers, start the sandbox, and apply the
    configured reasoning effort to the transport."""
    manager = getattr(ctrl, "_mcp_manager", None)
    if manager is not None:
        await manager.start()
    sandbox = ctrl.sandbox_runtime
    if sandbox is not None:
        try:
            await sandbox.start()
        except Exception:  # noqa: BLE001 — as in main.py: commands then run unsandboxed
            pass
    if live and os.environ.get("CRUCIBLE_REASONING_EFFORT"):
        from agentd.providers.reasoning_effort import parse_effort
        from agentd.providers.runtime import ProviderRuntime
        engine = ctrl._reasoning._inner  # noqa: SLF001
        runtime = ProviderRuntime(
            backend=backend, model=str(getattr(engine, "_model", model)), engines=[engine],
            transport=getattr(engine, "_transport", None),
            reasoning_effort=parse_effort(os.environ["CRUCIBLE_REASONING_EFFORT"]))
        await runtime.apply_reasoning_effort(runtime.reasoning_effort)


async def stop_controller(ctrl: ChatController) -> None:
    manager = getattr(ctrl, "_mcp_manager", None)
    try:
        if manager is not None:
            await manager.shutdown()
    finally:
        try:
            if ctrl.sandbox_runtime is not None:
                await ctrl.sandbox_runtime.close()
        finally:
            await close_transport(getattr(ctrl._reasoning, "_inner", ctrl._reasoning))  # noqa: SLF001


async def close_transport(engine: Any) -> None:
    """Each trial builds its own provider transport; close its HTTP client with the trial."""
    close = getattr(getattr(engine, "_transport", None), "aclose", None)
    if close is not None:
        await close()


async def converse(
    ctrl: ChatController, store: ChatThreadStore, thread_id: str, message: str,
    gates: list[GateRecord], *, clarify_answer: str,
    max_continuations: int = MAX_CONTINUATIONS,
) -> str | None:
    """Send the user's message and answer the mode and clarify cards that end a turn early,
    as a careful user would. Returns an error when a card is still waiting at the end."""
    channel = f"chat:{thread_id}"
    await ctrl.handle_message(thread_id, message, channel_id=channel)
    for attempt in range(max_continuations + 1):
        thread = store.get_thread(thread_id)
        pending = {g.kind: g for g in (thread.pending_controller_gates if thread else [])}
        if "mode" not in pending and "clarify" not in pending:
            return None
        if attempt == max_continuations:
            break
        if "mode" in pending:
            gates.append(GateRecord("mode", dict(pending["mode"].payload), "implement"))
            await ctrl.resolve_mode(thread_id, "implement", channel_id=channel, goal=message)
        else:
            gates.append(GateRecord("clarify", dict(pending["clarify"].payload),
                                    clarify_answer))
            await ctrl.resolve_clarify(thread_id, clarify_answer, channel_id=channel,
                                       goal=message)
    return f"turn still waiting on a card after {max_continuations} answers"
