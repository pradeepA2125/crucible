"""OpenAI Responses transport: one streaming request shape for API-key and ChatGPT-plan
credentials, strict-schema encode/decode, and the plan route's error contract."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from agentd.providers.availability import is_provider_unavailable
from agentd.providers.openai_compatible_transport import TransientTransportError
from agentd.providers.openai_transport import OpenAIJsonTransport, StaticBearer
from agentd.providers.plan_access import (
    PlanNotEligible,
    PlanSessionInvalid,
    PlanUnsupportedCapability,
    PlanUsageLimitReached,
    ProviderAccessStopped,
)

# ---------------------------------------------------------------- fakes


def _delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _completed(input_tokens: int = 11, output_tokens: int = 7) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        output_tokens_details=SimpleNamespace(reasoning_tokens=3))
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage))


def _failed(code: str, message: str = "nope") -> SimpleNamespace:
    return SimpleNamespace(type="response.failed", response=SimpleNamespace(
        error=SimpleNamespace(code=code, message=message), id="resp_1"))


class FakeStream:
    def __init__(self, events: list[Any], raise_after: Exception | None = None) -> None:
        self._events = list(events)
        self._raise_after = raise_after
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> Any:
        if self._events:
            return self._events.pop(0)
        if self._raise_after is not None:
            exc, self._raise_after = self._raise_after, None
            raise exc
        raise StopAsyncIteration


class FakeResponses:
    """Each queued item is a list of events (a stream) or an exception to raise."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeStream):
            return outcome
        return FakeStream(outcome)


def _status_error(status: int, body: dict[str, Any]) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, json=body, request=request,
                              headers={"x-request-id": "req_123"})
    inner = body.get("error", body)
    return openai.APIStatusError("boom", response=response, body=inner)


class RefreshingBearer:
    def __init__(self, tokens: list[str], refresh_ok: bool = True) -> None:
        self._tokens = tokens
        self._refresh_ok = refresh_ok
        self.refreshes = 0

    async def bearer(self) -> str:
        return self._tokens[0]

    async def on_unauthorized(self) -> bool:
        self.refreshes += 1
        if self._refresh_ok and len(self._tokens) > 1:
            self._tokens.pop(0)
            return True
        return False


def _transport(fake: FakeResponses, **kwargs: Any) -> OpenAIJsonTransport:
    kwargs.setdefault("retry_base_delay", 0.0)
    return OpenAIJsonTransport(responses_client=fake, **kwargs)


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

# ---------------------------------------------------------------- request shape


@pytest.mark.asyncio
async def test_generate_json_streams_one_plan_safe_request() -> None:
    # The structured-text path (the API-key route, or native_tools=False).
    fake = FakeResponses([_delta('{"ok"'), _delta(": true}"), _completed()])
    payload = await _transport(fake, plan_route=True, native_tools=False).generate_json(
        model="m", schema_name="plan document!", schema=SCHEMA,
        system_instructions="sys", user_payload={"goal": "x"})

    assert payload == {"ok": True}
    call = fake.calls[0]
    assert call["stream"] is True and call["store"] is False
    assert call["instructions"] == "sys"
    assert call["input"] == [{"role": "user", "content": json.dumps({"goal": "x"})}]
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["name"] == "plan_document_"
    assert call["text"]["format"]["schema"]["additionalProperties"] is False
    assert "temperature" not in call and "max_output_tokens" not in call


@pytest.mark.asyncio
async def test_api_key_route_may_cap_output_but_plan_route_never_does() -> None:
    fake = FakeResponses([_delta('{"ok": true}'), _completed()],
                         [_delta('{"ok": true}'), _completed()])
    await _transport(fake, max_output_tokens=500).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    plan = _transport(fake, max_output_tokens=500, plan_route=True, native_tools=False)
    await plan.generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    assert fake.calls[0]["max_output_tokens"] == 500
    assert "max_output_tokens" not in fake.calls[1]


@pytest.mark.asyncio
async def test_root_union_and_free_form_args_round_trip() -> None:
    schema = {"anyOf": [{"type": "object", "required": ["type", "tool", "args"], "properties": {
        "type": {"type": "string", "enum": ["tool_call"]}, "tool": {"type": "string"},
        "args": {"type": "object"}}}]}
    reply = {"action": {"type": "tool_call", "tool": "read_file", "args": '{"path": "a.py"}'}}
    fake = FakeResponses([_delta(json.dumps(reply)), _completed()])
    result = await _transport(fake).generate_json(
        model="m", schema_name="controller_step_response", schema=schema,
        system_instructions="", user_payload={})
    assert result == {"type": "tool_call", "tool": "read_file", "args": {"path": "a.py"}}


@pytest.mark.asyncio
async def test_generate_text_returns_streamed_text_without_a_format() -> None:
    fake = FakeResponses([_delta("# Plan\n"), _delta("- step "), _completed()])
    out = await _transport(fake).generate_text(
        model="m", system_instructions="plan", user_payload={"t": 1})
    assert out == "# Plan\n- step"
    assert "text" not in fake.calls[0]


@pytest.mark.asyncio
async def test_progress_thinking_and_usage_are_reported() -> None:
    fake = FakeResponses([
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="hmm"),
        _delta('{"ok": true}'), _completed(input_tokens=40, output_tokens=9)])
    thinking: list[str] = []
    progress: list[tuple[int, int]] = []
    usage: list[tuple[int, int]] = []
    await _transport(fake).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={},
        on_thinking=thinking.append,
        on_progress=lambda t, o, **_: progress.append((t, o)),
        on_usage=lambda p, c: usage.append((p, c)))
    assert thinking == ["hmm"]
    assert progress[-1] == (3, 9)
    assert usage == [(40, 9)]


def _item_delta(item_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text, item_id=item_id)


def _message_done(item_id: str, phase: str | None) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(
        type="message", id=item_id, phase=phase))


@pytest.mark.asyncio
async def test_structured_output_takes_the_first_action_and_stops_reading() -> None:
    # Measured live on the ChatGPT plan route (gpt-5.6-terra): asked for ONE action, the
    # model plays out its own agent loop inside one response — the real next action as
    # a `commentary` message, then invented follow-ups (a `wait_agents` tool that does
    # not exist, ×10) with no results ever returned, then a `final_answer` that can
    # fabricate those results. The first complete message is the action we asked for.
    first, invented, final = '{"ok": true}', '{"ok": false}', '{"ok": false}'
    stream = FakeStream([
        _item_delta("m1", first[:5]), _item_delta("m1", first[5:]),
        _message_done("m1", "commentary"),
        _item_delta("m2", invented), _message_done("m2", "commentary"),
        _item_delta("m3", final), _message_done("m3", "final_answer"), _completed()])
    fake = FakeResponses(stream)
    assert await _transport(fake, plan_route=True, native_tools=False).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="",
        user_payload={}) == {"ok": True}
    assert stream.closed  # the rest is never read: it costs plan usage and means nothing
    assert len(stream._events) == 5


@pytest.mark.asyncio
async def test_plain_text_still_uses_the_final_answer() -> None:
    fake = FakeResponses([
        _item_delta("m1", "Let me think about this."), _message_done("m1", "commentary"),
        _item_delta("m2", "The answer."), _message_done("m2", "final_answer"), _completed()])
    assert await _transport(fake, plan_route=True).generate_text(
        model="m", system_instructions="", user_payload={}) == "The answer."


@pytest.mark.asyncio
async def test_a_first_message_that_is_not_json_does_not_stop_the_stream() -> None:
    fake = FakeResponses([
        _item_delta("m1", "thinking out loud"), _message_done("m1", "commentary"),
        _item_delta("m2", '{"ok": true}'), _message_done("m2", "final_answer"), _completed()])
    assert await _transport(fake, plan_route=True, native_tools=False).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="",
        user_payload={}) == {"ok": True}


@pytest.mark.asyncio
async def test_without_phases_the_last_message_wins() -> None:
    fake = FakeResponses([
        _item_delta("a", "first"), _message_done("a", None),
        _item_delta("b", "second"), _message_done("b", None), _completed()])
    assert await _transport(fake).generate_text(
        model="m", system_instructions="", user_payload={}) == "second"


@pytest.mark.asyncio
async def test_a_plan_route_admission_refusal_is_an_unsupported_capability() -> None:
    # Measured live: an unknown model is refused before the stream with a bare
    # {"detail": …} body. Retrying or correcting the model can't fix the request.
    fake = FakeResponses(_status_error(400, {"detail": "The 'x' model is not supported "
                                                       "when using Codex with a ChatGPT account."}))
    with pytest.raises(PlanUnsupportedCapability, match="model is not supported"):
        await _transport(fake, plan_route=True).generate_text(
            model="x", system_instructions="", user_payload={})
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- terminal events


@pytest.mark.asyncio
async def test_a_stream_without_response_completed_is_transient_and_retried() -> None:
    fake = FakeResponses([_delta('{"ok": tr')], [_delta('{"ok": true}'), _completed()])
    retries: list[str] = []
    result = await _transport(fake).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={},
        on_retry=lambda attempt, total, reason, msg: retries.append(reason))
    assert result == {"ok": True}
    assert len(fake.calls) == 2 and retries == ["network_error"]


@pytest.mark.asyncio
async def test_retries_are_bounded() -> None:
    fake = FakeResponses([_delta("x")], [_delta("x")])
    with pytest.raises(TransientTransportError, match="response.completed"):
        await _transport(fake, max_retries=1).generate_text(
            model="m", system_instructions="", user_payload={})


@pytest.mark.asyncio
async def test_response_incomplete_is_an_error_not_a_success() -> None:
    fake = FakeResponses([_delta('{"ok"'), SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(incomplete_details=SimpleNamespace(reason="max_output_tokens")))])
    with pytest.raises(RuntimeError, match="max_output_tokens"):
        await _transport(fake).generate_json(
            model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_usage_limit_mid_stream_stops_without_retry() -> None:
    fake = FakeResponses([_delta('{"o'), _failed("subscription_sharing_usage_limit_exceeded")])
    with pytest.raises(PlanUsageLimitReached) as info:
        await _transport(fake, plan_route=True).generate_json(
            model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    assert len(fake.calls) == 1
    assert info.value.kind == "usage_limit"
    assert not is_provider_unavailable(info.value)


@pytest.mark.asyncio
async def test_usage_unavailable_mid_stream_is_transient() -> None:
    fake = FakeResponses([_failed("subscription_sharing_usage_unavailable")],
                         [_delta('{"ok": true}'), _completed()])
    assert await _transport(fake, plan_route=True, native_tools=False).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="",
        user_payload={}) == {"ok": True}


# ---------------------------------------------------------------- HTTP errors


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code", "cls"), [
    (429, "subscription_sharing_usage_limit_exceeded", PlanUsageLimitReached),
    (403, "subscription_sharing_user_not_eligible", PlanNotEligible),
    (400, "subscription_sharing_unsupported_capability", PlanUnsupportedCapability),
])
async def test_plan_errors_before_the_stream_stop_without_retry(
    status: int, code: str, cls: type[ProviderAccessStopped]
) -> None:
    fake = FakeResponses(_status_error(status, {"error": {
        "code": code, "message": "m", "param": "temperature"}}))
    with pytest.raises(cls) as info:
        await _transport(fake, plan_route=True).generate_text(
            model="m", system_instructions="", user_payload={})
    assert len(fake.calls) == 1
    assert info.value.status == status and info.value.request_id == "req_123"
    if cls is PlanUnsupportedCapability:
        assert info.value.param == "temperature"


@pytest.mark.asyncio
async def test_a_generic_429_is_still_retried() -> None:
    fake = FakeResponses(_status_error(429, {"error": {"code": "rate_limit_exceeded"}}),
                         [_delta("hi"), _completed()])
    assert await _transport(fake).generate_text(
        model="m", system_instructions="", user_payload={}) == "hi"


@pytest.mark.asyncio
async def test_401_refreshes_once_then_retries_with_the_new_token() -> None:
    bearer = RefreshingBearer(["old", "new"])
    fake = FakeResponses(_status_error(401, {"detail": "expired"}), [_delta("hi"), _completed()])
    transport = _transport(fake, plan_route=True, bearer=bearer)
    assert await transport.generate_text(model="m", system_instructions="",
                                         user_payload={}) == "hi"
    assert bearer.refreshes == 1


@pytest.mark.asyncio
async def test_401_that_refresh_cannot_fix_is_an_invalid_session() -> None:
    bearer = RefreshingBearer(["old"], refresh_ok=False)
    fake = FakeResponses(_status_error(401, {"detail": "The required signed identity…"}))
    with pytest.raises(PlanSessionInvalid, match="signed identity"):
        await _transport(fake, plan_route=True, bearer=bearer).generate_text(
            model="m", system_instructions="", user_payload={})


@pytest.mark.asyncio
async def test_api_key_401_is_not_dressed_up_as_a_plan_error() -> None:
    fake = FakeResponses(_status_error(401, {"error": {"code": "invalid_api_key"}}))
    with pytest.raises(openai.APIStatusError):
        await _transport(fake).generate_text(model="m", system_instructions="", user_payload={})


# ---------------------------------------------------------------- construction


def test_api_key_is_required_without_an_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIJsonTransport()


@pytest.mark.asyncio
async def test_static_bearer_never_refreshes() -> None:
    bearer = StaticBearer("sk-x")
    assert await bearer.bearer() == "sk-x"
    assert await bearer.on_unauthorized() is False


# ---------------------------------------------------------------- reasoning effort


@pytest.mark.asyncio
@pytest.mark.parametrize(("level", "wire"), [
    ("off", "none"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "max")])
async def test_effort_rides_the_request_on_both_routes(level: str, wire: str) -> None:
    from agentd.providers.reasoning_effort import ReasoningEffort

    fake = FakeResponses([_delta("hi"), _completed()])
    transport = _transport(fake, plan_route=True)
    transport.set_reasoning_effort(ReasoningEffort(level))
    await transport.generate_text(model="m", system_instructions="", user_payload={})
    assert fake.calls[0]["reasoning"] == {"summary": "auto", "effort": wire}


@pytest.mark.asyncio
async def test_no_effort_sends_no_reasoning_field() -> None:
    fake = FakeResponses([_delta("hi"), _completed()])
    await _transport(fake).generate_text(model="m", system_instructions="", user_payload={})
    assert "reasoning" not in fake.calls[0]


@pytest.mark.asyncio
async def test_a_rejected_rung_is_marked_and_the_call_retried_without_it() -> None:
    from agentd.providers.reasoning_effort import ReasoningEffort

    # The live plan route's answer to an unsupported rung (spike, gpt-5.6-terra).
    rejection = _status_error(400, {"error": {
        "message": "Unsupported value: 'max' is not supported with the 'm' model.",
        "type": "invalid_request_error", "param": "reasoning.effort",
        "code": "unsupported_value"}})
    fake = FakeResponses(rejection, [_delta("hi"), _completed()])
    transport = _transport(fake, plan_route=True)
    transport.set_reasoning_effort(ReasoningEffort.MAX)
    assert await transport.generate_text(model="m", system_instructions="",
                                         user_payload={}) == "hi"
    assert fake.calls[1]["reasoning"] == {"summary": "auto"}  # only the rung is dropped
    support = await transport.reasoning_effort_support("m")
    assert support.state(ReasoningEffort.MAX) == "unsupported"
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_other_400s_never_mark_a_rung() -> None:
    from agentd.providers.reasoning_effort import ReasoningEffort

    fake = FakeResponses(_status_error(400, {"error": {
        "message": "bad schema", "param": "text.format.schema", "code": "invalid_json_schema"}}))
    transport = _transport(fake)
    transport.set_reasoning_effort(ReasoningEffort.HIGH)
    with pytest.raises(openai.APIStatusError):
        await transport.generate_text(model="m", system_instructions="", user_payload={})
    assert (await transport.reasoning_effort_support("m")).unsupported == {}


# ---------------------------------------------------------------- errors inside a 200 stream


def _sdk_stream_error(code: str, message: str = "limit") -> openai.APIError:
    # What the SDK raises mid-iteration for an SSE `error` event (body = data["error"]).
    return openai.APIError(message, httpx.Request("POST", "https://api.openai.com/v1/responses"),
                           body={"code": code, "message": message, "type": "invalid_request_error"})


@pytest.mark.asyncio
async def test_a_usage_limit_inside_a_200_stream_is_an_access_stop() -> None:
    # Measured live (2026-10-07, a ChatGPT Go account at its limit): HTTP 200, then an
    # `error` event {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}
    # and response.failed. The SDK raises a plain APIError for the error event. Left
    # unclassified it went down the malformed-output path: 4 corrections per member,
    # then failed_transient and a re-queue against an exhausted plan.
    fake = FakeResponses(FakeStream([], raise_after=_sdk_stream_error(
        "subscription_sharing_usage_limit_exceeded",
        "The ChatGPT user has reached their Subscription Sharing usage limit.")))
    with pytest.raises(PlanUsageLimitReached, match="Subscription Sharing usage limit"):
        await _transport(fake, plan_route=True).generate_json(
            model="m", schema_name="s", schema=SCHEMA, system_instructions="", user_payload={})
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_transient_plan_code_inside_a_stream_is_retried() -> None:
    fake = FakeResponses(FakeStream([], raise_after=_sdk_stream_error(
        "subscription_sharing_usage_unavailable")), [_delta("hi"), _completed()])
    assert await _transport(fake, plan_route=True).generate_text(
        model="m", system_instructions="", user_payload={}) == "hi"


@pytest.mark.asyncio
async def test_an_error_event_with_a_nested_error_object_is_classified() -> None:
    event = SimpleNamespace(type="error", error=SimpleNamespace(
        code="subscription_sharing_usage_limit_exceeded", message="limit", param=None))
    fake = FakeResponses([event])
    with pytest.raises(PlanUsageLimitReached):
        await _transport(fake, plan_route=True).generate_text(
            model="m", system_instructions="", user_payload={})


# ---------------------------------------------------------------- reasoning summaries


@pytest.mark.asyncio
async def test_the_plan_route_asks_for_reasoning_summaries() -> None:
    # Live: every plan model defaults to default_reasoning_summary "none", so without
    # asking, the thinking pane stays empty for the whole turn.
    fake = FakeResponses([_delta("hi"), _completed()], [_delta("hi"), _completed()])
    await _transport(fake, plan_route=True).generate_text(
        model="m", system_instructions="", user_payload={})
    assert fake.calls[0]["reasoning"] == {"summary": "auto"}
    await _transport(fake).generate_text(model="m", system_instructions="", user_payload={})
    assert "reasoning" not in fake.calls[1]  # the API-key route may need org verification


@pytest.mark.asyncio
async def test_summary_parts_reach_thinking_as_separate_lines() -> None:
    from agentd.providers.reasoning_effort import ReasoningEffort

    fake = FakeResponses([
        SimpleNamespace(type="response.reasoning_summary_part.added"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="**Preparing kicks**"),
        SimpleNamespace(type="response.reasoning_summary_part.added"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="**Structuring data**"),
        _delta("hi"), _completed()])
    transport = _transport(fake, plan_route=True)
    transport.set_reasoning_effort(ReasoningEffort.HIGH)
    thinking: list[str] = []
    await transport.generate_text(model="m", system_instructions="", user_payload={},
                                  on_thinking=thinking.append)
    assert fake.calls[0]["reasoning"] == {"summary": "auto", "effort": "high"}
    assert "".join(thinking) == "**Preparing kicks**\n\n**Structuring data**"


# ---------------------------------------------------------------- native function calls


def _call_done(name: str, arguments: dict[str, object], *, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(
        type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id,
        namespace="crucible"))


CONTROLLER_UNION = {"anyOf": [
    {"type": "object", "required": ["type", "thought", "tool", "args"], "properties": {
        "type": {"type": "string", "const": "tool_call"}, "thought": {"type": "string"},
        "tool": {"type": "string"}, "args": {"type": "object"}}},
    {"type": "object", "required": ["type", "thought", "answer"], "properties": {
        "type": {"type": "string", "const": "answer"}, "thought": {"type": "string"},
        "answer": {"type": "string"}}},
]}


@pytest.mark.asyncio
async def test_the_plan_route_asks_for_one_native_function_call() -> None:
    fake = FakeResponses([_call_done("tool_call", {"thought": "t", "tool": "read_file",
                                                    "args": '{"path": "a.py"}'}), _completed()])
    payload = {"workspace_path": "/ws", "conversation_history": [
        {"role": "user", "content": "read a.py"}], "goal": "read a.py"}
    result = await _transport(fake, plan_route=True).generate_json(
        model="m", schema_name="controller_step_response", schema=CONTROLLER_UNION,
        system_instructions="sys", user_payload=payload)

    assert result == {"type": "tool_call", "thought": "t", "tool": "read_file",
                      "args": {"path": "a.py"}}
    call = fake.calls[0]
    assert "text" not in call
    assert call["tool_choice"] == "required" and call["parallel_tool_calls"] is False
    assert call["tools"][0]["type"] == "namespace"
    assert {f["name"] for f in call["tools"][0]["tools"]} == {"tool_call", "answer"}
    assert call["instructions"].startswith("sys") and "exactly one action" in call["instructions"]
    assert call["input"][0] == {"role": "user", "content": json.dumps({"workspace_path": "/ws"})}
    assert call["input"][1] == {"role": "user", "content": "read a.py"}


@pytest.mark.asyncio
async def test_a_preamble_before_the_call_is_shown_as_thinking() -> None:
    fake = FakeResponses([
        _item_delta("m1", "I'll read the file first."), _message_done("m1", "commentary"),
        _call_done("answer", {"thought": "t", "answer": "done"}), _completed()])
    thinking: list[str] = []
    result = await _transport(fake, plan_route=True).generate_json(
        model="m", schema_name="c", schema=CONTROLLER_UNION, system_instructions="",
        user_payload={}, on_thinking=thinking.append)
    assert result == {"type": "answer", "thought": "t", "answer": "done"}
    assert "I'll read the file first." in "".join(thinking)


@pytest.mark.asyncio
async def test_a_reply_without_a_function_call_is_a_correctable_error() -> None:
    fake = FakeResponses([_item_delta("m1", "just text"), _message_done("m1", "final_answer"),
                          _completed()])
    with pytest.raises(RuntimeError, match="without calling"):
        await _transport(fake, plan_route=True).generate_json(
            model="m", schema_name="c", schema=CONTROLLER_UNION, system_instructions="",
            user_payload={})


@pytest.mark.asyncio
async def test_a_flat_schema_is_a_forced_function() -> None:
    fake = FakeResponses([_call_done("s", {"ok": True}), _completed()])
    assert await _transport(fake, plan_route=True).generate_json(
        model="m", schema_name="s", schema=SCHEMA, system_instructions="",
        user_payload={}) == {"ok": True}
    assert fake.calls[0]["tool_choice"] == {"type": "function", "name": "s"}


@pytest.mark.asyncio
async def test_the_api_key_route_keeps_structured_text_output() -> None:
    fake = FakeResponses([_delta('{"ok": true}'), _completed()])
    await _transport(fake).generate_json(model="m", schema_name="s", schema=SCHEMA,
                                         system_instructions="", user_payload={})
    assert "tools" not in fake.calls[0] and fake.calls[0]["text"]["format"]["strict"] is True


# ---------------------------------------------------------------- prompt cache


def _completed_cached(input_tokens: int, cached: int, output_tokens: int = 5) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0))
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage))


@pytest.mark.asyncio
async def test_no_prompt_cache_key_is_sent() -> None:
    # Measured live (2026-10-07, plan route, identical requests): with our own
    # prompt_cache_key no call was ever served from cache (0 of 4, cache_write_tokens 0);
    # without one, repeats hit (9856 and 8832 of ~10k). The route assigns its own key —
    # a failed response echoed a server UUID we never sent — so a custom key only moves
    # requests into a partition that is never written.
    fake = FakeResponses([_call_done("answer", {"thought": "t", "answer": "a"}), _completed()],
                         [_delta("hi"), _completed()])
    t = _transport(fake, plan_route=True)
    await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                          system_instructions="s", user_payload={})
    await t.generate_text(model="m", system_instructions="s", user_payload={})
    assert all("prompt_cache_key" not in c for c in fake.calls)


@pytest.mark.asyncio
async def test_cached_tokens_are_recorded_for_the_current_owner() -> None:
    from agentd.providers.usage import METER, USAGE_OWNER

    fake = FakeResponses([_call_done("answer", {"thought": "t", "answer": "a"}),
                          _completed_cached(10000, 8832)])
    token = USAGE_OWNER.set("agent-cache-test")
    try:
        await _transport(fake, plan_route=True).generate_json(
            model="m", schema_name="c", schema=CONTROLLER_UNION, system_instructions="s",
            user_payload={})
    finally:
        USAGE_OWNER.reset(token)
    assert METER.take("agent-cache-test").cached_tokens == 8832


# ---------------------------------------------------------------- prompt cache (plan route)
# Measured live (2026-10-07, plan route): without a session_id header the route gives every
# request its own random prompt_cache_key — an exact repeat cached 0 of 21k tokens, and a
# prompt_cache_key in the body is overwritten. With session_id it becomes the key. The
# server's automatic breakpoint sits at the end of each request's input, so the next
# request must start with that whole input: replacing the per-turn message reused only the
# instructions+tools block (8.8k of 21k); keeping it and appending reused 91–95%.

def _owner(name: str):
    from agentd.providers.usage import USAGE_OWNER
    return USAGE_OWNER.set(name)


def _payload(history: list[dict[str, object]], step: int) -> dict[str, object]:
    return {"workspace_path": "/ws", "conversation_history": history,
            "instruction": f"step {step}"}


ACTION = {"role": "assistant", "content": json.dumps(
    {"type": "tool_call", "thought": "t", "tool": "read_file", "args": {"path": "a.py"}})}
RESULT = {"role": "tool_result", "tool": "read_file", "content": "def a(): ..."}
ANSWER = _call_done("answer", {"thought": "t", "answer": "a"})


async def _two_steps(t: OpenAIJsonTransport, first: list, second: list, *,
                     schema_names: tuple[str, str] = ("c", "c")) -> None:
    await t.generate_json(model="m", schema_name=schema_names[0], schema=CONTROLLER_UNION,
                          system_instructions="s", user_payload=_payload(first, 1))
    await t.generate_json(model="m", schema_name=schema_names[1], schema=CONTROLLER_UNION,
                          system_instructions="s", user_payload=_payload(second, 2))


@pytest.mark.asyncio
async def test_a_session_id_header_names_the_owner_and_stays_stable() -> None:
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()], [ANSWER, _completed()])
    t = _transport(fake, plan_route=True)
    token = _owner("agent-1")
    try:
        go = [{"role": "user", "content": "go"}]
        await _two_steps(t, go, go)
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                          system_instructions="s", user_payload={})   # no owner
    first, second, unowned = (c.get("extra_headers") for c in fake.calls)
    assert first == second and set(first) == {"session_id"}
    assert unowned is None
    assert all("prompt_cache_key" not in c for c in fake.calls)


@pytest.mark.asyncio
async def test_the_next_request_starts_with_the_whole_previous_input() -> None:
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()])
    t = _transport(fake, plan_route=True)
    goal = {"role": "user", "content": "read a.py"}
    token = _owner("agent-1")
    try:
        await _two_steps(t, [goal], [goal, ACTION, RESULT])
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    one, two = fake.calls[0]["input"], fake.calls[1]["input"]
    assert two[:len(one)] == one                      # the old per-turn message stays
    assert [i.get("type", i.get("role")) for i in two[len(one):]] == [
        "function_call", "function_call_output", "user"]
    assert "step 2" in two[-1]["content"] and "step 1" in one[-1]["content"]
    assert two[-1]["content"].startswith("Current step")   # the newest one says it is current


@pytest.mark.asyncio
async def test_a_rewritten_history_starts_over() -> None:
    # Compaction (or any rewrite) means the old input is no longer a prefix.
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()])
    t = _transport(fake, plan_route=True)
    token = _owner("agent-1")
    try:
        await _two_steps(t, [{"role": "user", "content": "a"}, ACTION, RESULT],
                         [{"role": "user", "content": "[MEMORY] summary"}, ACTION, RESULT])
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    two = fake.calls[1]["input"]
    assert sum(1 for i in two if i.get("role") == "user" and "step" in str(i.get("content"))) == 1


@pytest.mark.asyncio
async def test_another_schema_under_the_same_owner_has_its_own_input() -> None:
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()])
    t = _transport(fake, plan_route=True)
    goal = {"role": "user", "content": "go"}
    token = _owner("agent-1")
    try:
        await _two_steps(t, [goal], [goal, ACTION, RESULT], schema_names=("loop", "summary"))
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    assert "step 1" not in json.dumps(fake.calls[1]["input"])


@pytest.mark.asyncio
async def test_a_failed_request_does_not_advance_the_session() -> None:
    fake = FakeResponses([ANSWER, _completed()],
                         _status_error(400, {"error": {"message": "bad", "code": "x"}}),
                         [ANSWER, _completed()])
    t = _transport(fake, plan_route=True, max_retries=0)
    goal = {"role": "user", "content": "go"}
    token = _owner("agent-1")
    try:
        await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                              system_instructions="s", user_payload=_payload([goal], 1))
        with pytest.raises(openai.APIStatusError):
            await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                                  system_instructions="s",
                                  user_payload=_payload([goal, ACTION, RESULT], 2))
        await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                              system_instructions="s",
                              user_payload=_payload([goal, ACTION, RESULT], 3))
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    three = json.dumps(fake.calls[2]["input"])
    assert "step 1" in three and "step 2" not in three   # the failed step's message is not kept


@pytest.mark.asyncio
async def test_a_payload_without_history_is_never_appended_to() -> None:
    fake = FakeResponses([ANSWER, _completed()], [ANSWER, _completed()])
    t = _transport(fake, plan_route=True)
    token = _owner("agent-1")
    try:
        for step in (1, 2):
            await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                                  system_instructions="s", user_payload={"goal": f"g{step}"})
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    assert len(fake.calls[1]["input"]) == 1


@pytest.mark.asyncio
async def test_each_prompt_stream_of_an_owner_gets_its_own_session_id() -> None:
    # membrane#77 (same plan backend): one id per prompt stream missed 1 in 20 calls, one id
    # shared across interleaved prompts 3 in 13. An owner's other calls (e.g. the memory
    # consolidator under the thread) send different instructions, so a different id.
    fake = FakeResponses(*([ANSWER, _completed()] for _ in range(3)))
    t = _transport(fake, plan_route=True)
    goal = {"role": "user", "content": "go"}
    token = _owner("agent-1")
    try:
        for instructions, history in (("loop", [goal]), ("loop", [goal, ACTION, RESULT]),
                                      ("consolidate", [goal])):
            await t.generate_json(model="m", schema_name="c", schema=CONTROLLER_UNION,
                                  system_instructions=instructions,
                                  user_payload=_payload(history, 1))
    finally:
        from agentd.providers.usage import USAGE_OWNER
        USAGE_OWNER.reset(token)
    loop1, loop2, other = (c["extra_headers"]["session_id"] for c in fake.calls)
    assert loop1 == loop2          # a growing history keeps its stream's id
    assert other != loop1          # another prompt under the same owner does not share it
