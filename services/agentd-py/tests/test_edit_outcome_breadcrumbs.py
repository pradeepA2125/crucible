"""Edit-outcome breadcrumbs carry metadata.edit_outcome so the transcript can group them
with the edit they belong to, instead of matching their text."""
from __future__ import annotations

from agentd.chat.controller import ChatController


class _Store:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def append_message(self, thread_id: str, msg: object) -> str:
        self.messages.append(msg)
        return "id"


class _Broadcaster:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def broadcast(self, channel: str, event: dict) -> None:
        self.events.append(event)


class _Transcript:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def append(self, msg: object) -> None:
        self.messages.append(msg)


class _Child:
    def __init__(self) -> None:
        self.loop = None
        self.broadcaster = _Broadcaster()
        self.transcript = _Transcript()
        self.thread_id = "t"
        self.agent_id = "a1"


def _controller() -> ChatController:
    c = ChatController.__new__(ChatController)
    c._store = _Store()
    c._broadcaster = _Broadcaster()
    c._mark_pills_boundary = lambda thread_id: None  # type: ignore[method-assign]
    return c


def test_outcome_breadcrumb_is_flagged_persisted_and_live() -> None:
    c = _controller()
    c._write_breadcrumb("t", "chat:t", "✓ Edit accepted: a.py", edit_outcome=True)
    assert c._store.messages[-1].metadata == {"breadcrumb": True, "edit_outcome": True}
    assert c._broadcaster.events[-1]["payload"]["edit_outcome"] is True


def test_other_breadcrumbs_are_unchanged() -> None:
    c = _controller()
    c._write_breadcrumb("t", "chat:t", "✓ Implement chosen")
    assert c._store.messages[-1].metadata == {"breadcrumb": True}
    assert "edit_outcome" not in c._broadcaster.events[-1]["payload"]


def test_child_outcome_breadcrumb_is_flagged_persisted_and_live() -> None:
    c = _controller()
    child = _Child()
    c._child_breadcrumb(child, "✗ Not applied: stale", edit_outcome=True)  # type: ignore[arg-type]
    assert child.transcript.messages[-1].metadata == {"breadcrumb": True, "edit_outcome": True}
    assert child.broadcaster.events[-1]["payload"]["edit_outcome"] is True


def test_child_plain_breadcrumb_is_unchanged() -> None:
    c = _controller()
    child = _Child()
    c._child_breadcrumb(child, "▸ You chose: x")  # type: ignore[arg-type]
    assert child.transcript.messages[-1].metadata == {"breadcrumb": True}
    assert "edit_outcome" not in child.broadcaster.events[-1]["payload"]
