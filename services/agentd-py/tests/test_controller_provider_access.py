"""A main chat turn that hits a ChatGPT plan access stop ends at once with a
user-facing message, and /live carries the card until the next turn starts."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ShellPolicy
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.providers.plan_access import PlanUsageLimitReached


class _LimitThenAnswer:
    def __init__(self) -> None:
        self.calls = 0
        self.limited = True

    async def create_controller_step(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.limited:
            raise PlanUsageLimitReached(
                "usage limit", code="subscription_sharing_usage_limit_exceeded",
                status=429, request_id="req_9")
        return {"type": "answer", "thought": "t", "answer": "done"}


@pytest.mark.asyncio
async def test_usage_limit_ends_the_turn_with_a_card(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(ws), title="t")
    engine = _LimitThenAnswer()
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=None, broadcaster=EventBroadcaster(), retrieval_client=None,
        shell_policy=ShellPolicy.ASK)

    await ctrl.handle_message(thread.thread_id, "hello", channel_id="c1")

    assert engine.calls == 1  # no malformed-output corrections, no retries
    last = store.get_thread(thread.thread_id).messages[-1]  # type: ignore[union-attr]
    assert last.role == "agent"
    assert last.content.startswith("⚠️ Usage limit reached.")
    assert "turn failed" not in last.content
    card = ctrl.provider_access(thread.thread_id)
    assert card is not None and card["kind"] == "usage_limit"
    assert card["request_id"] == "req_9"

    engine.limited = False
    await ctrl.handle_message(thread.thread_id, "again", channel_id="c1")
    assert ctrl.provider_access(thread.thread_id) is None
