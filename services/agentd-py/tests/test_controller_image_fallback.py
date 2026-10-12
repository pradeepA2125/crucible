from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster


class _FailingReasoning:
    async def controller_step(self, **_kwargs):
        raise AssertionError("non-vision image turns must stop before provider invocation")


@pytest.mark.asyncio
async def test_non_vision_image_turn_returns_actionable_message(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), "t")
    controller = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=_FailingReasoning(),
        thread_store=store,
        orchestrator=None,
        broadcaster=EventBroadcaster(),
        vision_capable=False,
    )

    await controller.handle_message(
        thread.thread_id,
        "describe this",
        channel_id=f"chat:{thread.thread_id}",
        image_attachments=[{
            "name": "diagram.png",
            "media_type": "image/png",
            "asset_id": "a" * 64,
            "width": 1, "height": 1, "estimated_tokens": 1536,
            "bytes": 1,
        }],
    )

    messages = store.get_thread(thread.thread_id).messages  # type: ignore[union-attr]
    assert messages[-1].content == (
        "The selected model cannot process images; choose a vision-capable model "
        "or remove the image."
    )
