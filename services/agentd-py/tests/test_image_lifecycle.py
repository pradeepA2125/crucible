from copy import deepcopy
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerOutcome
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.providers.image_capabilities import ImageCapability

REF = {"asset_id": "a" * 64, "name": "x.png", "media_type": "image/png",
       "bytes": 10, "width": 1, "height": 1, "estimated_tokens": 1536}


def build(root: Path, store=None):
    return ChatController(workspace_path=str(root), reasoning_engine=object(),
                          thread_store=store or ChatThreadStore(root / "chat.db"),
                          orchestrator=None, broadcaster=EventBroadcaster())


@pytest.mark.asyncio
async def test_origin_followup_reload_and_dedicated_queue(tmp_path, monkeypatch):
    async def supported(*args):
        return ImageCapability(True, "")
    monkeypatch.setattr("agentd.providers.image_capabilities.resolve_image_capability", supported)
    controller = build(tmp_path)
    store = controller._store
    thread = store.create_thread(str(tmp_path), "t")
    captured = []

    async def loop(thread_id, channel_id, goal, *, seed_history, **kwargs):
        captured.append(deepcopy(seed_history))
        store.set_controller_history(thread_id, seed_history)
        controller._histories[thread_id] = seed_history
        assert "data_url" not in str(seed_history)
        return ControllerOutcome("answer", text="ok", history=seed_history)
    monkeypatch.setattr(controller, "_run_loop", loop)
    await controller.handle_message(thread.thread_id, "image", "c", image_attachments=[REF])
    assert captured[-1][-1]["image_refs"] == [REF]
    assert store.get_thread(thread.thread_id).messages[0].metadata["image_attachments"] == [REF]
    await controller.handle_message(thread.thread_id, "followup", "c")
    assert sum(bool(e.get("image_refs")) for e in captured[-1]) == 1
    restarted = build(tmp_path, ChatThreadStore(tmp_path / "chat.db"))
    assert restarted._seed_for(thread.thread_id)[0]["image_refs"] == [REF]
    queued = controller.queue_message(thread.thread_id, "queued", message_id=None,
                                     step_review=None, plan_mode=None, mentioned_files=None,
                                     forced_skills=None, image_attachments=[REF])
    controller._queued.clear()
    await controller.handle_queued_message(thread.thread_id, queued)
    assert captured[-1][-1]["image_refs"] == [REF]
    assert "queued" in captured[-1][-1]["content"]


def test_normal_drain_recovers_persisted_refs(tmp_path):
    controller = build(tmp_path)
    thread = controller._store.create_thread(str(tmp_path), "t")
    message_id = controller.queue_message(thread.thread_id, "queued", message_id=None,
                                         step_review=None, plan_mode=None, mentioned_files=None,
                                         forced_skills=None, image_attachments=[REF])
    controller._queued.clear()
    item, = controller._drain_main(thread.thread_id, "turn")
    assert item.source_id == message_id
    assert item.image_refs == [REF]
    assert controller._drain_main(thread.thread_id, "turn") == []


class RecordingReasoning:
    def __init__(self):
        self.requests = []
        self.fail_once = False
        self.enqueue = None
        self._model = "vision"
        self._transport = object()

    async def create_controller_step(self, *, plan_context, history, image_assets=None, **kwargs):
        self.requests.append((deepcopy(history), deepcopy(image_assets or {})))
        assert "data:image" not in str(plan_context)
        assert "data:image" not in str(history)
        if self.enqueue:
            enqueue, self.enqueue = self.enqueue, None
            enqueue()
            return {"type": "progress", "thought": "continue", "message": "Continuing"}
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary malformed response")
        return {"type": "answer", "thought": "done", "answer": "ok"}


def real_controller(tmp_path, monkeypatch):
    from agentd.chat.rewind import RewindStore
    from agentd.images import ImageAssetStore
    from tests.test_api_image_validation import upload

    async def supported(transport, model):
        return ImageCapability(model == "vision", "test unsupported model")
    monkeypatch.setattr("agentd.providers.image_capabilities.resolve_image_capability", supported)
    store = ChatThreadStore(tmp_path / "real.db")
    reasoning = RecordingReasoning()
    controller = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=reasoning, thread_store=store,
        orchestrator=object(), broadcaster=EventBroadcaster(),
        rewind_store=RewindStore(store, tmp_path))
    ref, = ImageAssetStore(tmp_path).import_uploads([upload()])
    thread = store.create_thread(str(tmp_path), "real")
    return controller, reasoning, thread.thread_id, ref


def assert_visual_request(request, ref):
    history, assets = request
    assert list(assets) == [ref["asset_id"]]
    assert sum(len(entry.get("image_refs", [])) for entry in history) == 1
    assert assets[ref["asset_id"]]["data_url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_real_loop_retry_followup_reload_clarify_mode_and_rewind(tmp_path, monkeypatch):
    from agentd.chat.models import PendingGate
    from agentd.images import ImageAssetStore

    controller, reasoning, tid, ref = real_controller(tmp_path, monkeypatch)
    reasoning.fail_once = True
    await controller.handle_message(tid, "inspect", "c", image_attachments=[ref])
    assert len(reasoning.requests) == 2
    for request in reasoning.requests:
        assert_visual_request(request, ref)
    durable = controller._store.get_thread(tid)
    anchor = durable.messages[0].id
    assert "data:image" not in durable.model_dump_json()
    assert durable.controller_conversation_history[0]["image_refs"] == [ref]
    controller.forget_turn_caches(tid)
    await controller.handle_message(tid, "followup", "c")
    assert_visual_request(reasoning.requests[-1], ref)
    controller._store.add_controller_gate(tid, PendingGate(
        kind="clarify", payload={"question": "which?", "resume_phase": "ACTIVE"}))
    controller.forget_turn_caches(tid)
    await controller.resolve_clarify(tid, "that one", channel_id="c", goal="inspect")
    assert_visual_request(reasoning.requests[-1], ref)
    controller._store.add_controller_gate(tid, PendingGate(kind="mode", payload={}))
    controller.forget_turn_caches(tid)
    await controller.resolve_mode(tid, "implement", channel_id="c", goal="inspect")
    assert_visual_request(reasoning.requests[-1], ref)
    outcome = controller._rewind.restore(tid, anchor)
    assert outcome is not None
    controller.forget_turn_caches(tid)
    assert not any(entry.get("image_refs") for entry in controller._seed_for(tid))
    assert ImageAssetStore(tmp_path).read_asset(ref["asset_id"])
    await controller.handle_message(tid, "after rewind", "c")
    assert reasoning.requests[-1][1] == {}


@pytest.mark.asyncio
async def test_real_normal_drain_inserts_queued_refs_once(tmp_path, monkeypatch):
    controller, reasoning, tid, ref = real_controller(tmp_path, monkeypatch)
    queued_ids = []

    def enqueue():
        queued_ids.append(controller.queue_message(
            tid, "queued image", message_id=None, step_review=None, plan_mode=None,
            mentioned_files=None, forced_skills=None, image_attachments=[ref]))
    reasoning.enqueue = enqueue
    await controller.handle_message(tid, "start", "c")
    assert_visual_request(reasoning.requests[-1], ref)
    history = controller._store.get_thread(tid).controller_conversation_history
    origins = [entry for entry in history if entry.get("image_refs")]
    assert len(origins) == 1
    assert origins[0]["source_id"] == queued_ids[0]
    assert origins[0]["content"] == "queued image"


@pytest.mark.asyncio
async def test_model_switch_rechecks_admission_and_keeps_durable_refs(tmp_path, monkeypatch):
    controller, reasoning, tid, ref = real_controller(tmp_path, monkeypatch)
    await controller.handle_message(tid, "inspect", "c", image_attachments=[ref])
    calls = len(reasoning.requests)
    reasoning._model = "text-only"
    await controller.handle_message(tid, "historical followup", "c")
    assert len(reasoning.requests) == calls
    assert "cannot process images" in controller._store.get_thread(tid).messages[-1].content
    await controller.handle_message(tid, "inspect again", "c", image_attachments=[ref])
    assert len(reasoning.requests) == calls
    messages = controller._store.get_thread(tid).messages
    assert messages[-2].metadata["image_attachments"] == [ref]
    assert "cannot process images" in messages[-1].content

