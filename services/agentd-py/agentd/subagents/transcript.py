"""A sub-agent's durable transcript (spec §5.5).

The same ChatMessage shapes and pills segmentation as a thread transcript, kept in memory
and persisted whole to `chat_agents.transcript_json` on every change. Each message records
the seq of the latest child-channel event when it was written: the UI backfills the
transcript, then skips replayed events at or below that cursor.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from agentd.chat.models import ChatMessage


class AgentTranscript:
    def __init__(
        self, persist: Callable[[list[ChatMessage]], None], current_seq: Callable[[], int],
    ) -> None:
        self._messages: list[ChatMessage] = []
        self._inflight: int | None = None  # index of the pills message still being updated
        self._persist = persist
        self._current_seq = current_seq

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def append(self, message: ChatMessage) -> None:
        self._messages.append(self._stamped(message))
        self._save()

    async def upsert_pills(
        self, tool_events: list[dict[str, object]], thinking_log: list[str],
    ) -> None:
        metadata: dict[str, object] = {"tool_events": tool_events}
        if thinking_log:
            metadata["thinking_log"] = thinking_log
        message = self._stamped(ChatMessage(role="agent", content="", metadata=metadata))
        if self._inflight is None:
            self._inflight = len(self._messages)
            self._messages.append(message)
        else:
            self._messages[self._inflight] = message.model_copy(
                update={"id": self._messages[self._inflight].id})
        self._save()

    def seal_pills(self) -> None:
        """A durable message landed: later pills start a fresh message after it."""
        self._inflight = None

    def last_seq(self) -> int:
        return max((int(m.metadata.get("seq", 0)) for m in self._messages), default=0)

    def _stamped(self, message: ChatMessage) -> ChatMessage:
        return message.model_copy(update={
            "id": message.id or uuid4().hex,
            "metadata": {**message.metadata, "seq": self._current_seq()},
        })

    def _save(self) -> None:
        self._persist(list(self._messages))
