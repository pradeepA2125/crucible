"""No live inference: auxiliary requests must share routing across every retry."""
import pytest

from agentd.providers.usage import USAGE_OWNER
from tests.test_openai_transport import (
    SCHEMA,
    FakeResponses,
    RefreshingBearer,
    _call_done,
    _completed,
    _delta,
    _status_error,
    _transport,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["text", "strict_json", "native_json"])
@pytest.mark.parametrize("failure", ["transient", "refresh"])
async def test_auxiliary_retry_reuses_input_and_session(entry, failure):
    error = _status_error(401 if failure == "refresh" else 503, {"error": {
        "code": "server_error", "message": "temporary",
    }})
    events = ([_call_done("memory_result", {"ok": True}), _completed()]
              if entry == "native_json" else [_delta('{"ok": true}'), _completed()])
    fake = FakeResponses(error, events, events)
    bearer = RefreshingBearer(["old", "new"])
    transport = _transport(fake, plan_route=True, bearer=bearer,
                           native_tools=entry == "native_json")
    async def call():
        args = dict(model="m", system_instructions="memory instructions",
                    user_payload={"transcript": "unchanged transcript"})
        if entry == "text":
            return await transport.generate_text(**args)
        return await transport.generate_json(schema_name="memory_result", schema=SCHEMA, **args)

    token = USAGE_OWNER.set("thread:cache-regression")
    try:
        await call()  # transport retry
        await call()  # independent caller retry
    finally:
        USAGE_OWNER.reset(token)
    assert len(fake.calls) == 3
    assert fake.calls[0] == fake.calls[1] == fake.calls[2]
    assert fake.calls[0]["extra_headers"]["session_id"]
    assert bearer.refreshes == (1 if failure == "refresh" else 0)


@pytest.mark.asyncio
async def test_unowned_calls_are_stable_and_isolated_from_other_streams():
    events = [_delta("memory"), _completed()]
    fake = FakeResponses(events, events, events, events)
    first, second = (_transport(fake, plan_route=True) for _ in range(2))
    args = dict(model="m", system_instructions="summarize", user_payload={"transcript": "x"})
    await first.generate_text(**args)
    await first.generate_text(**args)
    await second.generate_text(**args)
    await first.generate_text(**{**args, "system_instructions": "consolidate"})
    headers = [call["extra_headers"] for call in fake.calls]
    assert headers[0] == headers[1]
    assert headers[0] != headers[2] and headers[0] != headers[3]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["text", "strict_json", "native_json"])
async def test_plan_usage_limit_is_never_retried(entry):
    from agentd.providers.plan_access import PlanUsageLimitReached

    fake = FakeResponses(_status_error(429, {"error": {
        "code": "subscription_sharing_usage_limit_exceeded", "message": "quota exhausted",
    }}))
    transport = _transport(fake, plan_route=True, native_tools=entry == "native_json")
    args = dict(model="m", system_instructions="memory", user_payload={})
    with pytest.raises(PlanUsageLimitReached):
        if entry == "text":
            await transport.generate_text(**args)
        else:
            await transport.generate_json(schema_name="memory", schema=SCHEMA, **args)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_compaction_uses_native_json_and_accounts_auxiliary_tokens():
    from agentd.memory.harness import make_engine_summarizer
    from agentd.providers.usage import METER

    events = [_call_done("compaction_summary", {
        "summary_markdown": "## Goal\nContinue the work.\n## Next step\nInspect the file.",
    }), _completed(input_tokens=172487, output_tokens=120)]
    fake = FakeResponses(events, events)
    summarize = make_engine_summarizer(_transport(fake, plan_route=True), "m", require_note=True)
    owner = "thread:summary-cache"
    token = USAGE_OWNER.set(owner)
    try:
        await summarize("", "same history")
        await summarize("", "same history")
    finally:
        USAGE_OWNER.reset(token)
    assert fake.calls[0] == fake.calls[1]
    assert fake.calls[0]["extra_headers"]["session_id"]
    assert fake.calls[0]["tool_choice"]["name"] == "compaction_summary"
    usage = METER.take(owner)
    assert usage.prompt_tokens == 344974 and usage.completion_tokens == 240
