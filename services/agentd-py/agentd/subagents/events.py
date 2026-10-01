"""Sub-agent channel events (spec §5.5)."""
from __future__ import annotations

from typing import Any

from agentd.orchestrator.broadcaster import EventBroadcaster


class SequencedBroadcaster(EventBroadcaster):
    """Stamps a monotonic `seq` on every event broadcast to ONE channel (a child's).

    `call_index` resets at every pills boundary, so it cannot identify an event; `seq`
    can. The UI backfills a child's transcript (whose messages record the seq that
    produced them) and then skips replayed events at or below that cursor, so
    backfill-then-subscribe never renders a duplicate. Other channels pass through
    untouched: the parent's channel never gains a field."""

    def __init__(self, inner: EventBroadcaster, channel_id: str) -> None:
        super().__init__()
        self._inner = inner
        self._channel = channel_id
        self._seq = 0

    @property
    def last_seq(self) -> int:
        return self._seq

    def broadcast(self, channel_id: str, event: dict[str, Any]) -> None:
        if channel_id == self._channel:
            self._seq += 1
            event = {**event, "seq": self._seq}
        self._inner.broadcast(channel_id, event)

    def subscribe(self, channel_id: str) -> Any:
        return self._inner.subscribe(channel_id)

    def unsubscribe(self, channel_id: str, queue: Any) -> None:
        self._inner.unsubscribe(channel_id, queue)

    def clear_replay(self, channel_id: str) -> None:
        self._inner.clear_replay(channel_id)
