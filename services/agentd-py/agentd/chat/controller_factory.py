"""Flag-select the chat handler: the new ChatController vs the legacy ChatAgent.

`AI_EDITOR_CHAT_CONTROLLER` is a TEMPORARY migration flag (not a long-term setting):
ship the controller behind it, default off until smoke-verified, flip to on, then
delete the legacy explore→classify→route pipeline at the `=0` retirement (Phase K).
Both handlers expose the same surface: handle_message(thread_id, message, channel_id,
step_review=None) plus `_store`/`_broadcaster` attrs the route reads.
"""
from __future__ import annotations

import os
from typing import Any

_FLAG_ENV = "AI_EDITOR_CHAT_CONTROLLER"
_TRUTHY = {"1", "true", "yes", "on"}


def is_controller_enabled() -> bool:
    return os.getenv(_FLAG_ENV, "0").strip().lower() in _TRUTHY


def select_chat_handler(
    *,
    workspace_path: str,
    transport: Any,
    model: str,
    thread_store: Any,
    orchestrator: Any | None,
    broadcaster: Any,
    retrieval_client: Any | None = None,
) -> Any:
    """Return the flag-selected chat handler. The controller wraps transport+model
    in a ReasoningEngineImpl (it drives the loop through the engine seam, scriptable);
    the legacy agent takes the raw transport+model directly."""
    if is_controller_enabled():
        from agentd.chat.controller import ChatController
        from agentd.reasoning.engine import DefaultReasoningEngine

        return ChatController(
            workspace_path=workspace_path,
            reasoning_engine=DefaultReasoningEngine(model=model, transport=transport),
            thread_store=thread_store,
            orchestrator=orchestrator,
            broadcaster=broadcaster,
            retrieval_client=retrieval_client,
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
