"""Voice exchanges are saved in the visible chat, and stay out of every context reader:
the coding agent's history, the Auto approval judge, memory's closing summary, the
latest-user-message id and time, and the goal used when a gate card is claimed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentd.approvals.judge_context import build_judge_context
from agentd.chat.models import ChatMessage, is_voice_message
from agentd.chat.voice_agent import VoiceAgent, voice_history
from agentd.memory.recall_context import previous_closing
from tests.test_background_dispatch import _setup
from tests.test_conversation_steering import ANSWER, _gated_steps
from tests.test_voice_agent import FakeJson


def _voice(ctrl, reply: dict[str, object] | None = None, *, text: str = "") -> FakeJson:  # type: ignore[no-untyped-def]
    fake = FakeJson(reply, text=text)
    ctrl._voice = VoiceAgent(lambda: (fake, "m"))
    return fake


def _reply(say: str, action: str = "reply") -> dict[str, object]:
    return {"turn": "complete", "action": action, "say": say, "request": "", "basis": ""}


def _voice_user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text,
                       metadata={"voice": "exchange", "voice_decision": "d1"})


async def _exchange(ctrl, tid: str, heard: str, say: str) -> str:  # type: ignore[no-untyped-def]
    _voice(ctrl, _reply(say))
    decided = await ctrl.voice_decide(tid, heard, 800, False)
    ctrl.voice_commit(tid, str(decided["decision_id"]))
    return str(decided["decision_id"])


def test_is_voice_message_marks_exchanges_and_narration_only() -> None:
    assert is_voice_message(_voice_user("hi"))
    assert is_voice_message(
        ChatMessage(role="agent", content="Done.", metadata={"voice": "narration"}))
    # A spoken request that started work is an ordinary user message (it is the task's goal).
    assert not is_voice_message(
        ChatMessage(role="user", content="fix it", metadata={"voice": True}))
    assert not is_voice_message(ChatMessage(role="user", content="fix it"))


@pytest.mark.asyncio
async def test_reply_exchange_is_saved_as_voice_messages(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    decision_id = await _exchange(ctrl, tid, "how is it going", "Nothing is running.")
    heard, said = store.get_thread(tid).messages[-2:]  # type: ignore[union-attr]
    assert (heard.role, heard.content) == ("user", "how is it going")
    assert (said.role, said.content) == ("agent", "Nothing is running.")
    for message in (heard, said):
        assert message.metadata == {"voice": "exchange", "voice_decision": decision_id}


@pytest.mark.asyncio
async def test_clarify_and_steer_acknowledgement_are_saved(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    await _exchange(ctrl, tid, "change the colour", "Which colour?")
    assert [m.content for m in store.get_thread(tid).messages] == [  # type: ignore[union-attr]
        "change the colour", "Which colour?"]


@pytest.mark.asyncio
async def test_start_task_commit_writes_no_voice_message(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    _voice(ctrl, {**_reply("On it.", "start_task"), "request": "Add a README file to the project"})
    decided = await ctrl.voice_decide(tid, "add a readme", 800, False)
    ctrl.voice_commit(tid, str(decided["decision_id"]))
    assert store.get_thread(tid).messages == []  # type: ignore[union-attr]


def _voice_events(ctrl, tid: str) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    queue = ctrl._broadcaster.subscribe(f"chat:{tid}")
    events = []
    while not queue.empty():
        event = queue.get_nowait()
        if event.get("type") == "chat_voice_message":
            events.append(event["payload"]["message"])
    return events


@pytest.mark.asyncio
async def test_saved_voice_messages_are_pushed_live_with_their_ids(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A turn streaming on this thread shows the exchange now, not on its next reload.
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    await _exchange(ctrl, tid, "how is it going", "Step two of three.")
    _voice(ctrl, text="Fixed it.")
    await ctrl.voice_narrate(tid, "fix it", "Fixed.")
    saved = store.get_thread(tid).messages  # type: ignore[union-attr]
    pushed = _voice_events(ctrl, tid)
    assert [(m["id"], m["role"], m["content"]) for m in pushed] == [
        (m.id, m.role, m.content) for m in saved]
    assert [m["metadata"]["voice"] for m in pushed] == ["exchange", "exchange", "narration"]


@pytest.mark.asyncio
async def test_narration_is_saved_against_the_answer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    answer_id = store.append_message(
        tid, ChatMessage(role="agent", content="## Fixed\n- Tests pass."))
    _voice(ctrl, text="Fixed it, and the tests pass.")
    spoken = await ctrl.voice_narrate(tid, "fix it", "## Fixed\n- Tests pass.")
    narration = store.get_thread(tid).messages[-1]  # type: ignore[union-attr]
    assert spoken == narration.content == "Fixed it, and the tests pass."
    assert narration.role == "agent"
    assert narration.metadata == {"voice": "narration", "answer_id": answer_id}


def test_voice_messages_are_not_permission_for_the_auto_judge() -> None:
    context = build_judge_context([_voice_user("delete the build folder")], [], None)
    assert context.authorizations == ()
    assert context.authorization_total == 0


@pytest.mark.asyncio
async def test_voice_messages_do_not_change_latest_user_message_or_closing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    typed_id = store.append_message(tid, ChatMessage(role="user", content="add a readme"))
    store.append_message(tid, ChatMessage(role="agent", content="Added the README."))
    typed_at = store.last_user_message_at(tid)
    await _exchange(ctrl, tid, "how is it going", "All done.")
    store.append_message(tid, ChatMessage(role="agent", content="Spoken summary.",
                                          metadata={"voice": "narration", "answer_id": "x"}))
    assert ctrl.latest_user_message_id(tid) == typed_id
    assert store.last_user_message_at(tid) == typed_at
    messages = store.get_thread(tid).messages  # type: ignore[union-attr]
    assert previous_closing(
        {"role": m.role, "type": m.type, "content": m.content, "metadata": m.metadata}
        for m in reversed(messages)) == "Added the README."


@pytest.mark.asyncio
async def test_voice_messages_never_enter_controller_history(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [ANSWER], {})
    await _exchange(ctrl, tid, "how is the weather", "I only know about the code.")
    payloads: list[dict[str, object]] = []
    _gated_steps(monkeypatch, lambda _n, _h: None, payloads=payloads)
    await ctrl.handle_message(tid, "add a readme", channel_id=f"chat:{tid}")
    sent = json.dumps(payloads)
    assert "how is the weather" not in sent and "I only know about the code." not in sent
    saved = json.dumps(store.get_thread(tid).controller_conversation_history or [])  # type: ignore[union-attr]
    assert "how is the weather" not in saved
    # The thread is titled from its first typed message, not from small talk.
    assert store.get_thread(tid).title == "add a readme"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_voice_history_survives_restart(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, store, tid = _setup(tmp_path, monkeypatch, [], {})
    await _exchange(ctrl, tid, "should About have a contact form", "Yes, with name and email.")
    # A new VoiceAgent (a restarted backend) keeps nothing in memory; the store has it.
    fake = _voice(ctrl, _reply("Sure."))
    await ctrl.voice_decide(tid, "and a phone field?", 800, False)
    history = fake.calls[-1]["user_payload"]["history"]  # type: ignore[index]
    assert {"role": "user", "text": "should About have a contact form"} in history
    assert {"role": "voice", "text": "Yes, with name and email."} in history


def test_voice_history_reads_voice_messages_newest_kept() -> None:
    messages = [ChatMessage(role="user", content=f"typed {n}") for n in range(20)]
    messages += [_voice_user("spoken"), ChatMessage(
        role="agent", content="said", metadata={"voice": "exchange", "voice_decision": "d1"})]
    history = voice_history(messages)
    assert len(history) == 12
    assert history[-2:] == [{"role": "user", "text": "spoken"}, {"role": "voice", "text": "said"}]


@pytest.mark.asyncio
async def test_live_reports_message_count(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from tests.test_conversation_steering import _app

    ctrl, _store, tid = _setup(tmp_path, monkeypatch, [], {})
    await _exchange(ctrl, tid, "hello", "Hi.")
    transport = ASGITransport(app=_app(ctrl))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    assert live["message_count"] == 2
