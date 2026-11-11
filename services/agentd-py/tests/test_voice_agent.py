"""The voice agent: turn completeness, the action it picks, narration and its history."""
from __future__ import annotations

import asyncio

import pytest

from agentd.chat.models import ChatMessage
from agentd.chat.voice_agent import (
    VOICE_CHECKIN_TEXT,
    VOICE_IDLE_STEER_TEXT,
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
async def test_steer_while_idle_asks_before_starting_work() -> None:
    # A correction for a turn that already ended must not silently become a new task.
    agent = _agent(FakeJson(_reply(action="steer", say="On it.", request="revert that")))
    decision = await agent.decide("t1", _ctx("stop, revert that", progress=None))
    assert decision.action == "clarify"
    assert decision.say == VOICE_IDLE_STEER_TEXT


@pytest.mark.asyncio
async def test_a_vague_referential_request_becomes_a_clarifying_question() -> None:
    # "Do it" with nothing resolved must never start work on a guess.
    agent = _agent(FakeJson(_reply(action="start_task", say="On it.", request="Do it.")))
    decision = await agent.decide("t1", _ctx("do it"))
    assert decision.action == "clarify"
    assert decision.say == "Which change should I make?"


@pytest.mark.asyncio
async def test_a_vague_referential_steer_becomes_a_clarifying_question() -> None:
    agent = _agent(FakeJson(_reply(action="steer", say="OK.", request="")))
    decision = await agent.decide("t1", _ctx("Go ahead!", progress=PROGRESS))
    assert decision.action == "clarify"
    assert decision.say == "Which change should I make?"


@pytest.mark.asyncio
async def test_a_resolved_referential_request_starts() -> None:
    raw = {**_reply(action="start_task", say="On it.",
                    request="Add a contact form to the About page"),
           "basis": "user: should About have a contact form?\nvoice: Yes, name and email."}
    decision = await _agent(FakeJson(raw)).decide("t1", _ctx("do it"))
    assert decision.action == "start_task"
    assert decision.request == "Add a contact form to the About page"
    assert decision.basis == "user: should About have a contact form?\nvoice: Yes, name and email."


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
        "history": [{"role": "user", "text": "hi"}], "progress": PROGRESS, "project": ""}
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
    assert history[1]["role"] == "agent" and len(history[1]["text"]) == 2000  # latest answer, kept
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


# --- provider-general behavior: model choice, effort, retries, correction, logging ---

from agentd.providers.claude_subscription import (  # noqa: E402
    ClaudeRequestError,
    ClaudeSubscriptionTransport,
)
from agentd.providers.reasoning_effort import (  # noqa: E402
    EFFORT_OVERRIDE,
    EffortSupport,
    ReasoningEffort,
)
from agentd.providers.side_calls import CLAUDE_SMALL_MODEL  # noqa: E402


class Scripted(FakeJson):
    """Replies in order; records the model, effort override and retry hook per call."""

    def __init__(self, *replies: object, low: bool = False) -> None:
        super().__init__()
        self.replies = list(replies)
        self.low = low
        self.seen: list[tuple[str, object]] = []

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        return EffortSupport(supported=frozenset({ReasoningEffort.LOW}) if self.low
                             else frozenset())

    async def generate_json(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        self.seen.append((str(kwargs["model"]), EFFORT_OVERRIDE.get()))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        assert isinstance(reply, dict)
        return dict(reply)


class ScriptedClaude(ClaudeSubscriptionTransport, Scripted):
    def __init__(self, *replies: object) -> None:
        Scripted.__init__(self, *replies)

    async def generate_json(self, **kwargs: object) -> dict[str, object]:  # type: ignore[override]
        return await Scripted.generate_json(self, **kwargs)

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        return await Scripted.reasoning_effort_support(self, model)


def _on(transport: object) -> VoiceAgent:
    return VoiceAgent(lambda: (transport, "main-model"))


@pytest.mark.asyncio
async def test_claude_plan_decides_on_the_small_model() -> None:
    transport = ScriptedClaude(_reply())
    await _on(transport).decide("t1", _ctx())
    assert transport.seen[0][0] == CLAUDE_SMALL_MODEL


@pytest.mark.asyncio
async def test_voice_model_env_wins_over_the_small_model(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_VOICE_MODEL", "picked-model")
    transport = ScriptedClaude(_reply())
    await _on(transport).decide("t1", _ctx())
    assert transport.seen[0][0] == "picked-model"


@pytest.mark.asyncio
async def test_rejected_small_model_falls_back_to_main_and_is_remembered() -> None:
    transport = ScriptedClaude(ClaudeRequestError("no", status=404), _reply(), _reply())
    agent = _on(transport)
    await agent.decide("t1", _ctx())
    await agent.decide("t1", _ctx())
    assert [model for model, _ in transport.seen] == [
        CLAUDE_SMALL_MODEL, "main-model", "main-model"]


@pytest.mark.asyncio
async def test_rate_limit_is_not_a_model_rejection() -> None:
    transport = ScriptedClaude(ClaudeRequestError("slow down", status=429))
    with pytest.raises(VoiceAgentUnavailable):
        await _on(transport).decide("t1", _ctx())
    assert len(transport.seen) == 1


@pytest.mark.asyncio
async def test_decide_runs_at_low_effort_where_supported() -> None:
    low, default = Scripted(_reply(), low=True), Scripted(_reply())
    await _on(low).decide("t1", _ctx())
    await _on(default).decide("t1", _ctx())
    assert low.seen[0][1] == ReasoningEffort.LOW
    assert default.seen[0][1] is None


@pytest.mark.asyncio
async def test_transport_retries_are_cut_off() -> None:
    transport = Scripted(_reply())
    await _on(transport).decide("t1", _ctx())
    on_retry = transport.calls[0]["on_retry"]
    assert callable(on_retry)
    with pytest.raises(VoiceAgentUnavailable):
        on_retry(1, 4, "rate_limit", "429")


@pytest.mark.asyncio
async def test_unusable_reply_is_corrected_once() -> None:
    bad = {"turn": "maybe", "action": "dance", "say": "", "request": ""}
    transport = Scripted(bad, _reply(say="Hi."))
    decision = await _on(transport).decide("t1", _ctx())
    assert decision.say == "Hi."
    payload = transport.calls[1]["user_payload"]
    assert isinstance(payload, dict) and "correction" in payload
    assert "correction" not in transport.calls[0]["user_payload"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_decide_logs_model_turn_action_and_latency(
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="agentd.chat.voice_agent")
    await _on(Scripted(_reply())).decide("t1", _ctx())
    line = next(r.getMessage() for r in caplog.records if "[voice] decide" in r.getMessage())
    assert "model=main-model" in line and "turn=complete" in line
    assert "action=reply" in line and "ms=" in line
    assert "silence_ms=700" in line and "final=False" in line
    assert "heard='use sqlite'" in line and "say='Sure.'" in line


@pytest.mark.asyncio
async def test_decide_log_caps_long_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="agentd.chat.voice_agent")
    await _on(Scripted(_reply())).decide("t1", _ctx("word " * 200))
    line = next(r.getMessage() for r in caplog.records if "[voice] decide" in r.getMessage())
    assert len(line) < 900


@pytest.mark.asyncio
async def test_narrate_logs_what_it_said(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="agentd.chat.voice_agent")
    await _agent(FakeJson(text="Done. Tests pass.")).narrate("t1", "fix it", "Long answer")
    line = next(r.getMessage() for r in caplog.records if "[voice] narrate" in r.getMessage())
    assert "said='Done. Tests pass.'" in line and "ms=" in line


@pytest.mark.asyncio
async def test_decide_prompt_asks_for_the_object_only() -> None:
    transport = Scripted(_reply())
    await _on(transport).decide("t1", _ctx())
    assert "only" in str(transport.calls[0]["system_instructions"]).split("\n")[-1].lower()


@pytest.mark.asyncio
async def test_ended_sign_in_is_reported_in_the_providers_words_without_a_fallback() -> None:
    from agentd.providers.claude_subscription import ClaudeAccessStopped

    stopped = ClaudeAccessStopped("Connect again in Crucible settings.",
                                  kind="session_invalid", status=401, request_id=None)
    transport = ScriptedClaude(stopped)
    with pytest.raises(VoiceAgentUnavailable, match="Connect again"):
        await _on(transport).decide("t1", _ctx())
    assert len(transport.seen) == 1


class StrictText:
    """A transport whose generate_text takes no retry or usage hooks (the Claude plan's)."""

    async def generate_text(self, *, model: str, system_instructions: str,
                            user_payload: dict[str, object]) -> str:
        return "Done. The tests pass."


class PassThrough:
    """The rate limiter: accepts any keyword and forwards it to what it wraps."""

    def __init__(self, inner: object) -> None:
        self.wrapped = inner

    async def generate_text(self, **kwargs: object) -> object:
        return await self.wrapped.generate_text(**kwargs)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_narrate_passes_only_hooks_the_wrapped_transport_accepts() -> None:
    spoken = await _on(PassThrough(StrictText())).narrate("t1", "fix it", "Long answer")
    assert spoken == "Done. The tests pass."


@pytest.mark.asyncio
async def test_decide_payload_names_the_open_project() -> None:
    transport = FakeJson(_reply())
    ctx = VoiceContext(utterance="what does this repo do", silence_ms=700, final_call=False,
                       history=[], progress=None, project="pradeep-site")
    await _agent(transport).decide("t1", ctx)
    payload = transport.calls[-1]["user_payload"]
    assert isinstance(payload, dict) and payload["project"] == "pradeep-site"


def test_history_keeps_the_latest_answer_long_enough_to_explain() -> None:
    old, latest = "o" * 3000, "n" * 6000
    messages = [ChatMessage(role="agent", content=old), ChatMessage(role="user", content="explain"),
                ChatMessage(role="agent", content=latest)]
    history = voice_history(messages, [])
    assert len(history[0]["text"]) == 1200
    assert len(history[-1]["text"]) == 4000


def test_decide_prompt_covers_idle_follow_ups_final_call_and_mishearings() -> None:
    from agentd.chat.voice_agent import _DECIDE_SYSTEM

    prompt = _DECIDE_SYSTEM.lower()
    assert "never choose steer" in prompt
    assert "last answer" in prompt
    assert "final_call" in prompt and "did you mean" in prompt
    assert "misheard" in prompt and "project" in prompt


@pytest.mark.asyncio
async def test_final_call_payload_asks_for_a_specific_question() -> None:
    transport = FakeJson(_reply("thinking", "listen", ""), )
    await _agent(transport).decide("t1", _ctx("this rapport, this rapport, this", final_call=True))
    payload = transport.calls[-1]["user_payload"]
    assert isinstance(payload, dict)
    assert "question about what you heard" in str(payload["instruction"])
    await _agent(transport).decide("t1", _ctx("use sqlite"))
    assert "instruction" not in transport.calls[-1]["user_payload"]  # type: ignore[operator]


# --- plan 2, task 4: the ChatGPT voice model and a per-session override ---

from agentd.chat.voice_agent import CHATGPT_VOICE_MODEL  # noqa: E402
from agentd.providers.openai_transport import OpenAIJsonTransport  # noqa: E402
from agentd.providers.plan_access import PlanUnsupportedCapability  # noqa: E402


class ScriptedChatGPT(OpenAIJsonTransport, Scripted):
    def __init__(self, *replies: object) -> None:
        Scripted.__init__(self, *replies)
        self._plan_route = True

    async def generate_json(self, **kwargs: object) -> dict[str, object]:  # type: ignore[override]
        return await Scripted.generate_json(self, **kwargs)

    async def reasoning_effort_support(self, model: str) -> EffortSupport:  # type: ignore[override]
        return await Scripted.reasoning_effort_support(self, model)


def _ctx_with(override: str) -> VoiceContext:
    return VoiceContext(utterance="use sqlite", silence_ms=700, final_call=False, history=[],
                        progress=None, model_override=override)


@pytest.mark.asyncio
async def test_chatgpt_plan_decides_on_luna() -> None:
    transport = ScriptedChatGPT(_reply())
    await _on(transport).decide("t1", _ctx())
    assert CHATGPT_VOICE_MODEL == "gpt-6-luna"
    assert transport.seen[0][0] == "gpt-6-luna"


@pytest.mark.asyncio
async def test_voice_model_override_wins_over_the_default() -> None:
    transport = ScriptedChatGPT(_reply())
    await _on(transport).decide("t1", _ctx_with("gpt-6.1-terra"))
    assert transport.seen[0][0] == "gpt-6.1-terra"


@pytest.mark.asyncio
async def test_env_wins_over_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_VOICE_MODEL", "env-model")
    transport = ScriptedChatGPT(_reply())
    await _on(transport).decide("t1", _ctx_with("gpt-6.1-terra"))
    assert transport.seen[0][0] == "env-model"


@pytest.mark.asyncio
async def test_plan_refusing_the_voice_model_falls_back() -> None:
    refused = PlanUnsupportedCapability("The 'gpt-6-luna' model is not supported.", status=400)
    transport = ScriptedChatGPT(refused, _reply(), _reply())
    agent = _on(transport)
    await agent.decide("t1", _ctx())
    await agent.decide("t1", _ctx())
    assert [model for model, _ in transport.seen] == ["gpt-6-luna", "main-model", "main-model"]


@pytest.mark.asyncio
async def test_narrate_uses_the_override() -> None:
    transport = FakeJson(text="Done.")
    await _agent(transport).narrate("t1", "fix it", "Done.", model_override="picked-model")
    assert transport.calls[-1]["model"] == "picked-model"
