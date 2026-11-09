"""The voice agent: turn completeness, the action it picks, narration and its history."""
from __future__ import annotations

import asyncio

import pytest

from agentd.chat.models import ChatMessage
from agentd.chat.voice_agent import (
    VOICE_CHECKIN_TEXT,
    VoiceAgent,
    VoiceAgentUnavailable,
    VoiceContext,
    voice_history,
)

PROGRESS = {"phase": "ACTIVE", "recent_tools": [], "todos": [], "waiting_on": [],
            "agents_running": 0, "tool_calls_so_far": 3}


class FakeJson:
    """A transport whose JSON reply, text reply or stall the test chooses."""

    def __init__(self, reply: dict[str, object] | None = None, *, text: str = "",
                 gate: asyncio.Event | None = None) -> None:
        self.reply = reply or {}
        self.text = text
        self.gate = gate
        self.calls: list[dict[str, object]] = []

    async def generate_json(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        return dict(self.reply)

    async def generate_text(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        return self.text


def _agent(transport: FakeJson | None) -> VoiceAgent:
    return VoiceAgent((lambda: (transport, "main-model")) if transport is not None else None)


def _ctx(utterance: str = "use sqlite", *, final_call: bool = False,
         progress: dict[str, object] | None = None) -> VoiceContext:
    return VoiceContext(utterance=utterance, silence_ms=700, final_call=final_call,
                        history=[], progress=progress)


def _reply(turn: str = "complete", action: str = "reply", say: str = "Sure.",
           request: str = "") -> dict[str, object]:
    return {"turn": turn, "action": action, "say": say, "request": request}


@pytest.mark.asyncio
async def test_unfinished_turn_always_listens() -> None:
    agent = _agent(FakeJson(_reply("cut_off", "start_task", "ok", "x")))
    decision = await agent.decide("t1", _ctx())
    assert decision.action == "listen"
    assert decision.turn == "cut_off"


@pytest.mark.asyncio
async def test_final_call_turns_listen_into_checkin() -> None:
    agent = _agent(FakeJson(_reply("thinking", "listen", "")))
    decision = await agent.decide("t1", _ctx(final_call=True))
    assert decision.action == "clarify"
    assert decision.say == VOICE_CHECKIN_TEXT


@pytest.mark.asyncio
async def test_task_while_busy_becomes_steer() -> None:
    agent = _agent(FakeJson(_reply(action="start_task", say="On it.", request="use sqlite")))
    decision = await agent.decide("t1", _ctx(progress=PROGRESS))
    assert decision.action == "steer"


@pytest.mark.asyncio
async def test_steer_while_idle_becomes_task() -> None:
    agent = _agent(FakeJson(_reply(action="steer", say="On it.", request="use sqlite")))
    decision = await agent.decide("t1", _ctx(progress=None))
    assert decision.action == "start_task"


@pytest.mark.asyncio
async def test_empty_request_uses_utterance() -> None:
    agent = _agent(FakeJson(_reply(action="start_task", say="On it.", request="  ")))
    decision = await agent.decide("t1", _ctx("review the voice feature"))
    assert decision.request == "review the voice feature"


@pytest.mark.asyncio
async def test_reply_without_say_becomes_checkin() -> None:
    agent = _agent(FakeJson(_reply(action="reply", say="")))
    decision = await agent.decide("t1", _ctx())
    assert decision.action == "clarify"
    assert decision.say == VOICE_CHECKIN_TEXT


@pytest.mark.asyncio
async def test_say_is_capped_at_400() -> None:
    agent = _agent(FakeJson(_reply(say="word " * 200)))
    decision = await agent.decide("t1", _ctx())
    assert len(decision.say) <= 400


@pytest.mark.asyncio
async def test_unknown_values_are_unavailable() -> None:
    agent = _agent(FakeJson({"turn": "maybe", "action": "dance", "say": "", "request": ""}))
    with pytest.raises(VoiceAgentUnavailable):
        await agent.decide("t1", _ctx())


@pytest.mark.asyncio
async def test_decide_payload_carries_utterance_history_and_progress(
        monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeJson(_reply())
    agent = _agent(transport)
    ctx = VoiceContext(utterance="how is it going", silence_ms=900, final_call=False,
                       history=[{"role": "user", "text": "hi"}], progress=PROGRESS)
    await agent.decide("t1", ctx)
    call = transport.calls[-1]
    assert call["schema_name"] == "voice_decision"
    assert call["model"] == "main-model"
    assert call["user_payload"] == {
        "utterance": "how is it going", "silence_ms": 900, "final_call": False,
        "history": [{"role": "user", "text": "hi"}], "progress": PROGRESS}
    monkeypatch.setenv("CRUCIBLE_VOICE_MODEL", "fast-model")
    await agent.decide("t1", ctx)
    assert transport.calls[-1]["model"] == "fast-model"


@pytest.mark.asyncio
async def test_no_transport_or_timeout_raises_unavailable(
        monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(VoiceAgentUnavailable):
        await _agent(None).decide("t1", _ctx())
    monkeypatch.setenv("CRUCIBLE_VOICE_DECIDE_TIMEOUT_SEC", "0.05")
    stalled = _agent(FakeJson(_reply(), gate=asyncio.Event()))
    with pytest.raises(VoiceAgentUnavailable):
        await stalled.decide("t1", _ctx())


def test_history_skips_breadcrumbs_cards_and_empty() -> None:
    messages = [
        ChatMessage(role="user", content="build a tetris game"),
        ChatMessage(role="agent", content="✓ Edit accepted", metadata={"breadcrumb": True}),
        ChatMessage(role="agent", content="diff", type="diff_card"),
        ChatMessage(role="agent", content=""),
        ChatMessage(role="agent", content="x" * 2000),
    ]
    log = [{"role": "user", "text": "how is it going"}, {"role": "voice", "text": "Halfway."}]
    history = voice_history(messages, log)
    assert history[0] == {"role": "user", "text": "build a tetris game"}
    assert history[1]["role"] == "agent" and len(history[1]["text"]) == 1200
    assert history[2:] == log


def test_history_keeps_the_newest_12() -> None:
    messages = [ChatMessage(role="user", content=f"m{i}") for i in range(20)]
    history = voice_history(messages, [])
    assert len(history) == 12
    assert history[-1]["text"] == "m19"
    assert history[0]["text"] == "m8"


def test_record_keeps_last_24() -> None:
    agent = _agent(None)
    for i in range(20):
        agent.record("t1", f"heard {i}", f"said {i}")
    log = agent.log("t1")
    assert len(log) == 24
    assert log[-1] == {"role": "voice", "text": "said 19"}
    assert log[0] == {"role": "user", "text": "heard 8"}
    assert agent.log("other") == []


def test_record_without_said_logs_only_the_user() -> None:
    agent = _agent(None)
    agent.record("t1", "use sqlite", "")
    assert agent.log("t1") == [{"role": "user", "text": "use sqlite"}]


@pytest.mark.asyncio
async def test_narrate_returns_plain_speech() -> None:
    transport = FakeJson(text="  **Done.** Edited `a.py`.  ")
    spoken = await _agent(transport).narrate("t1", "fix it", "y" * 20000)
    assert spoken == "**Done.** Edited `a.py`."
    call = transport.calls[-1]
    assert "Never read code" in str(call["system_instructions"])
    payload = call["user_payload"]
    assert isinstance(payload, dict)
    assert payload["heard"] == "fix it"
    assert len(str(payload["answer"])) == 16000


@pytest.mark.asyncio
async def test_narrate_empty_reply_is_unavailable() -> None:
    with pytest.raises(VoiceAgentUnavailable):
        await _agent(FakeJson(text="   ")).narrate("t1", "fix it", "Done.")
