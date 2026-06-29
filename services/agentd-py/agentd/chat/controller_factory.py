"""Flag-select the chat handler: the new ChatController vs the legacy ChatAgent.

`AI_EDITOR_CHAT_CONTROLLER` is a TEMPORARY migration flag (not a long-term setting):
ship the controller behind it, default off until smoke-verified, flip to on, then
delete the legacy explore→classify→route pipeline at the `=0` retirement (Phase K).
Both handlers expose the same surface: handle_message(thread_id, message, channel_id,
step_review=None) plus `_store`/`_broadcaster` attrs the route reads.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_FLAG_ENV = "AI_EDITOR_CHAT_CONTROLLER"
_TASK_SUBSYSTEM_ENV = "AI_EDITOR_TASK_SUBSYSTEM"
_TRUTHY = {"1", "true", "yes", "on"}


def is_controller_enabled() -> bool:
    return os.getenv(_FLAG_ENV, "0").strip().lower() in _TRUTHY


def is_task_subsystem_enabled() -> bool:
    """Whether the task-based path (create_task/resume + task UI) is active. Default OFF:
    the controller handles small + large changes inline via the todo ledger. Opt in with
    AI_EDITOR_TASK_SUBSYSTEM=1 (only coherent with AI_EDITOR_CHAT_CONTROLLER=1)."""
    return os.getenv(_TASK_SUBSYSTEM_ENV, "0").strip().lower() in _TRUTHY


def is_memory_enabled() -> bool:
    """Whether the memory harness (compaction + recall/remember) is active. Default OFF;
    opt in with AI_EDITOR_MEMORY_ENABLED=1. Gates the controller's memory tools + prompt."""
    from agentd.memory.config import MemoryConfig
    return MemoryConfig.from_env(os.environ).enabled


def is_project_instructions_enabled() -> bool:
    """Whether a workspace AGENTS.md is injected into the controller system
    prompt. Default ON — reading the project's AGENTS.md is table-stakes parity.
    Kill-switch only: AI_EDITOR_PROJECT_INSTRUCTIONS=0 (or false/no/off)."""
    return os.getenv("AI_EDITOR_PROJECT_INSTRUCTIONS", "1").strip().lower() in _TRUTHY


def warn_if_incoherent_flags(logger: logging.Logger) -> None:
    """Task-subsystem OFF only works when the controller is ON (the legacy ChatAgent's
    large_change branch has nowhere to go without create_task). Warn — do not fail."""
    if not is_task_subsystem_enabled() and not is_controller_enabled():
        logger.warning(
            "incoherent flags: AI_EDITOR_TASK_SUBSYSTEM is off but AI_EDITOR_CHAT_CONTROLLER "
            "is also off — large changes have no path. Set AI_EDITOR_CHAT_CONTROLLER=1."
        )


def select_chat_handler(
    *,
    workspace_path: str,
    transport: Any,
    model: str,
    thread_store: Any,
    orchestrator: Any | None,
    broadcaster: Any,
    retrieval_client: Any | None = None,
    shell_policy: Any = None,
    command_decision_timeout_sec: float = 0.0,
) -> Any:
    """Return the flag-selected chat handler. The controller wraps transport+model
    in a ReasoningEngineImpl (it drives the loop through the engine seam, scriptable);
    the legacy agent takes the raw transport+model directly. shell_policy /
    command_decision_timeout_sec gate run_command in controller EDIT turns (same env
    knobs as the task path); ignored by the legacy ChatAgent (no run_command path)."""
    if is_controller_enabled():
        import os

        from agentd.chat.controller import ChatController
        from agentd.domain.models import ShellPolicy
        from agentd.instructions.loader import ProjectInstructionsLoader
        from agentd.memory.config import MemoryConfig
        from agentd.memory.harness import build_memory_harness
        from agentd.reasoning.engine import DefaultReasoningEngine

        # Within-run compaction + cross-session memory for controller turns (no-op unless
        # AI_EDITOR_MEMORY_ENABLED). workspace_path enables consolidation (workspace scope).
        memory_harness = build_memory_harness(
            MemoryConfig.from_env(os.environ), transport, model, workspace_path=workspace_path)
        # Auto-inject the workspace AGENTS.md into the controller system prompt (default on;
        # mtime-cached so an edit self-updates without a restart). Frozen workspace_path.
        project_instructions_loader = (
            ProjectInstructionsLoader(workspace_path)
            if is_project_instructions_enabled()
            else None
        )
        return ChatController(
            workspace_path=workspace_path,
            reasoning_engine=DefaultReasoningEngine(
                model=model,
                transport=transport,
                project_instructions_loader=project_instructions_loader,
            ),
            thread_store=thread_store,
            orchestrator=orchestrator,
            broadcaster=broadcaster,
            retrieval_client=retrieval_client,
            shell_policy=shell_policy or ShellPolicy.ASK,
            command_decision_timeout_sec=command_decision_timeout_sec,
            memory_harness=memory_harness,
        )

    from agentd.chat.agent import ChatAgent

    return ChatAgent(
        workspace_path=workspace_path,
        transport=transport,
        model=model,
        thread_store=thread_store,
        orchestrator=orchestrator,
        broadcaster=broadcaster,
        retrieval_client=retrieval_client,
    )
