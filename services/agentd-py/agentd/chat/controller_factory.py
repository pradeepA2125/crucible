"""Flag-select the chat handler: the new ChatController vs the legacy ChatAgent.

`CRUCIBLE_CHAT_CONTROLLER` is a TEMPORARY migration flag (not a long-term setting):
ship the controller behind it, default off until smoke-verified, flip to on, then
delete the legacy explore→classify→route pipeline at the `=0` retirement (Phase K).
Both handlers expose the same surface: handle_message(thread_id, message, channel_id,
step_review=None) plus `_store`/`_broadcaster` attrs the route reads.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_FLAG_ENV = "CRUCIBLE_CHAT_CONTROLLER"
_TASK_SUBSYSTEM_ENV = "CRUCIBLE_TASK_SUBSYSTEM"
_TRUTHY = {"1", "true", "yes", "on"}


def is_controller_enabled() -> bool:
    return os.getenv(_FLAG_ENV, "0").strip().lower() in _TRUTHY


def is_task_subsystem_enabled() -> bool:
    """Whether the task-based path (create_task/resume + task UI) is active. Default OFF:
    the controller handles small + large changes inline via the todo ledger. Opt in with
    CRUCIBLE_TASK_SUBSYSTEM=1 (only coherent with CRUCIBLE_CHAT_CONTROLLER=1)."""
    return os.getenv(_TASK_SUBSYSTEM_ENV, "0").strip().lower() in _TRUTHY


def is_memory_enabled() -> bool:
    """Whether the memory harness (compaction + recall/remember) is active. Default ON;
    kill-switch via CRUCIBLE_MEMORY_ENABLED=0/false/no/off. Gates the controller's memory
    tools + prompt."""
    from agentd.memory.config import MemoryConfig
    return MemoryConfig.from_env(os.environ).enabled


def is_project_instructions_enabled() -> bool:
    """Whether a workspace AGENTS.md is injected into the controller system
    prompt. Default ON — reading the project's AGENTS.md is table-stakes parity.
    Kill-switch only: CRUCIBLE_PROJECT_INSTRUCTIONS=0 (or false/no/off)."""
    return os.getenv("CRUCIBLE_PROJECT_INSTRUCTIONS", "1").strip().lower() in _TRUTHY


def is_skills_enabled() -> bool:
    """Whether agentskills.io SKILL.md skills are discovered + offered to the
    controller (catalog + read_skill + /skill forced-load). Default OFF — new
    capability, ship dark. Opt in with CRUCIBLE_SKILLS_ENABLED=1."""
    return os.getenv("CRUCIBLE_SKILLS_ENABLED", "0").strip().lower() in _TRUTHY


def is_mcp_enabled() -> bool:
    """Whether external MCP servers from .ai-editor/mcp.json are connected and
    offered to the controller. Default OFF — external tool execution, ship dark.
    Opt in with CRUCIBLE_MCP_ENABLED=1."""
    return os.getenv("CRUCIBLE_MCP_ENABLED", "0").strip().lower() in _TRUTHY


def is_doc_write_enabled() -> bool:
    """Whether the controller offers write_doc (per-write-gated doc/data writes).
    Default OFF. Opt in with CRUCIBLE_DOC_WRITE_ENABLED=1."""
    return os.getenv("CRUCIBLE_DOC_WRITE_ENABLED", "0").strip().lower() in _TRUTHY


def warn_if_incoherent_flags(logger: logging.Logger) -> None:
    """Task-subsystem OFF only works when the controller is ON (the legacy ChatAgent's
    large_change branch has nowhere to go without create_task). Warn — do not fail."""
    if not is_task_subsystem_enabled() and not is_controller_enabled():
        logger.warning(
            "incoherent flags: CRUCIBLE_TASK_SUBSYSTEM is off but CRUCIBLE_CHAT_CONTROLLER "
            "is also off — large changes have no path. Set CRUCIBLE_CHAT_CONTROLLER=1."
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
        # CRUCIBLE_MEMORY_ENABLED). workspace_path enables consolidation (workspace scope).
        memory_harness = build_memory_harness(
            MemoryConfig.from_env(os.environ), transport, model, workspace_path=workspace_path)
        # Auto-inject the workspace AGENTS.md into the controller system prompt (default on;
        # mtime-cached so an edit self-updates without a restart). Frozen workspace_path.
        project_instructions_loader = (
            ProjectInstructionsLoader(workspace_path)
            if is_project_instructions_enabled()
            else None
        )
        # Discover agentskills.io SKILL.md skills for the controller catalog (default off;
        # mtime-cached so a skill add self-updates without a restart). Frozen workspace_path.
        from agentd.skills.loader import SkillCatalogLoader

        skill_catalog_loader = (
            SkillCatalogLoader(workspace_path) if is_skills_enabled() else None
        )
        # MCP servers (default off): the manager is CONSTRUCTED here (frozen
        # workspace_path, mirrors the other loaders) but CONNECTS in main.py's
        # startup event handler — this factory runs at module import with no
        # event loop, and the SDK's transports need one (spec §3.2/§3.6).
        mcp_manager = None
        if is_mcp_enabled():
            from agentd.mcp.client import McpConnectionManager
            from agentd.mcp.config import McpConfigLoader

            mcp_manager = McpConnectionManager(McpConfigLoader(workspace_path))
        return ChatController(
            workspace_path=workspace_path,
            reasoning_engine=DefaultReasoningEngine(
                model=model,
                transport=transport,
                project_instructions_loader=project_instructions_loader,
                skill_catalog_loader=skill_catalog_loader,
            ),
            thread_store=thread_store,
            orchestrator=orchestrator,
            broadcaster=broadcaster,
            retrieval_client=retrieval_client,
            shell_policy=shell_policy or ShellPolicy.ASK,
            command_decision_timeout_sec=command_decision_timeout_sec,
            memory_harness=memory_harness,
            mcp_manager=mcp_manager,
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
