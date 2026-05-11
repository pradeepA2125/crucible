from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, AsyncIterator

from agentd.chat.classifier import IntentClassifier
from agentd.chat.models import ChatEvent, ChatMessage, IntentType
from agentd.chat.storage import ChatThreadStore
from agentd.planning.registry import PlanningToolRegistry

logger = logging.getLogger(__name__)

_EXPLORE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "done"]},
        "tool": {"type": "string",
                 "enum": ["search_code", "list_directory", "read_file", "search_semantic"]},
        "args": {"type": "object"},
    },
    "required": ["action"],
}

_EXPLORE_PROMPT = """\
You are exploring a codebase to gather context before classifying a user request.
Use tools to find relevant files, symbols, and usages mentioned in the message and history.
When you have enough evidence to judge scope, emit action=done.

Tools: search_code (ripgrep), list_directory, read_file, search_semantic.
Cap: you will be stopped after a fixed number of calls regardless.
Never modify files.
"""

_QA_PROMPT = """\
You are an expert code assistant. Answer the user's question about the codebase.
Use the workspace context below — files and search results already gathered.
Be concise and specific. Name files and functions explicitly.
"""


class ChatAgent:
    def __init__(
        self,
        *,
        workspace_path: str,
        transport: Any,
        model: str,
        thread_store: ChatThreadStore,
        orchestrator: Any | None,
        max_explore_calls: int = 5,
    ) -> None:
        self._workspace_path = workspace_path
        self._transport = transport
        self._model = model
        self._store = thread_store
        self._orchestrator = orchestrator
        self._max_explore_calls = max_explore_calls
        self._registry = PlanningToolRegistry(real_path=Path(workspace_path))
        self._classifier = IntentClassifier(transport=transport, model=model)

    async def handle_message(self, thread_id: str, message: str) -> AsyncIterator[ChatEvent]:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id!r} not found")

        user_msg = ChatMessage(role="user", content=message)
        self._store.append_message(thread_id, user_msg)

        history = [{"role": m.role, "content": m.content} for m in thread.messages]

        # Explore phase — inlined so we can yield progress events at each step.
        # Without these the user sees nothing for several seconds and thinks the UI is frozen.
        context: list[dict[str, Any]] = []
        files_examined: list[str] = []

        yield ChatEvent(type="chat_agent_thinking", payload={"message": "Exploring workspace…"})

        for _ in range(self._max_explore_calls):
            try:
                step = await self._transport.generate_json(
                    model=self._model,
                    schema_name="explore_step",
                    schema=_EXPLORE_SCHEMA,
                    system_instructions=_EXPLORE_PROMPT,
                    user_payload={
                        "message": message,
                        "conversation_history": history[-10:],
                        "workspace_path": self._workspace_path,
                        "tool_results": context,
                    },
                )
            except Exception:
                logger.exception("Explore step failed — stopping early")
                break

            if step.get("action") == "done":
                break

            tool_name = step.get("tool", "")
            args = step.get("args") or {}

            yield ChatEvent(type="explore_tool_call",
                            payload={"tool": tool_name, "args": args})

            try:
                tool_output = await self._registry.execute(tool_name, args)
                context.append({"tool": tool_name, "result": tool_output.output, "is_error": tool_output.is_error})
            except Exception as exc:
                context.append({"tool": tool_name, "result": str(exc), "is_error": True})

            if tool_name in ("read_file", "list_directory"):
                path = args.get("path", "")
                if path and path not in files_examined:
                    files_examined.append(str(path))

        classification = await self._classifier.classify(
            message, context=context, history=history
        )
        yield ChatEvent(
            type="intent_classified",
            payload={
                "intent": classification.intent,
                "rationale": classification.rationale,
                "likely_targets": classification.likely_targets,
                "files_examined": files_examined,
            },
        )

        if classification.intent == IntentType.QA:
            async for event in self._handle_qa(thread_id, message, context, history):
                yield event
        else:
            # small_change and large_change wired in Plan 2
            yield ChatEvent(
                type="chat_response",
                payload={"chunk": f"[{classification.intent} routing — not yet wired]"},
            )
            yield ChatEvent(type="chat_done", payload={})

    async def _handle_qa(
        self,
        thread_id: str,
        message: str,
        context: list[dict[str, Any]],
        history: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]:
        try:
            response_text = await self._transport.generate_text(
                model=self._model,
                system_instructions=_QA_PROMPT,
                user_payload={
                    "workspace_path": self._workspace_path,
                    "conversation_history": history[-10:],
                    "workspace_context": context,  # already gathered — no re-read
                    "question": message,
                },
            )
        except Exception:
            logger.exception("Q&A LLM call failed")
            response_text = "Sorry, I couldn't answer that. Please try again."

        self._store.append_message(
            thread_id, ChatMessage(role="agent", content=response_text)
        )
        yield ChatEvent(type="chat_response", payload={"chunk": response_text})
        yield ChatEvent(type="chat_done", payload={})
