"""Model-call spans: what Phoenix needs for cost, plus queue wait (spec §6.1, §6.2)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentd.providers.rate_limit import RateLimitedTransport
from agentd.providers.usage import METER, USAGE_OWNER
from agentd.telemetry.context import CALL_PURPOSE


class _NoWaitLimiter:
    async def acquire(self, priority: str) -> float:
        return 0.25


class _FakeInner:
    async def generate_json(self, **kwargs: Any) -> dict[str, object]:
        METER.record(USAGE_OWNER.get(), prompt=120, completion=30)
        METER.record(USAGE_OWNER.get(), cached=100)
        return {"type": "answer", "text": "hi"}

    async def generate_text(self, **kwargs: Any) -> str:
        METER.record(USAGE_OWNER.get(), prompt=10, completion=5)
        return "hello"


class _Hangs:
    async def generate_json(self, **kwargs: Any) -> object:
        await asyncio.Event().wait()


_ARGS: dict[str, Any] = {"model": "gpt-5.6-luna", "schema_name": "controller_ACTIVE",
                         "schema": {}, "system_instructions": "sys",
                         "user_payload": {"goal": "x"}}


@pytest.mark.asyncio
async def test_llm_span_carries_phoenix_cost_attributes(test_exporter: Any) -> None:
    USAGE_OWNER.set("thread:t1")
    t = RateLimitedTransport(_FakeInner(), _NoWaitLimiter(), backend="openai")
    assert await t.generate_json(**_ARGS) == {"type": "answer", "text": "hi"}
    (span,) = test_exporter.get_finished_spans()
    a = span.attributes
    assert a["openinference.span.kind"] == "LLM"
    assert (a["llm.model_name"], a["llm.provider"]) == ("gpt-5.6-luna", "openai")
    assert (a["llm.token_count.prompt"], a["llm.token_count.completion"],
            a["llm.token_count.total"]) == (120, 30, 150)
    assert a["llm.token_count.prompt_details.cache_read"] == 100
    assert a["crucible.call_purpose"] == "agent"
    assert a["crucible.queue_wait_ms"] == 250
    assert "billing" not in "".join(a)
    assert "sys" in a["input.value"] and "hi" in a["output.value"]


@pytest.mark.asyncio
async def test_plan_backend_is_marked(test_exporter: Any) -> None:
    t = RateLimitedTransport(_FakeInner(), _NoWaitLimiter(), backend="chatgpt")
    await t.generate_json(**_ARGS)
    (span,) = test_exporter.get_finished_spans()
    assert span.attributes["crucible.billing"] == "plan"
    assert span.attributes["llm.provider"] == "openai"


@pytest.mark.asyncio
async def test_auto_judge_calls_are_labelled(test_exporter: Any) -> None:
    token = CALL_PURPOSE.set("auto_judge")
    try:
        await RateLimitedTransport(_FakeInner(), _NoWaitLimiter(), backend="openai") \
            .generate_text(model="m", system_instructions="s", user_payload={})
    finally:
        CALL_PURPOSE.reset(token)
    (span,) = test_exporter.get_finished_spans()
    assert span.attributes["crucible.call_purpose"] == "auto_judge"
    assert span.attributes["llm.token_count.total"] == 15


@pytest.mark.asyncio
async def test_content_off_drops_messages(test_exporter_no_content: Any) -> None:
    await RateLimitedTransport(_FakeInner(), _NoWaitLimiter(), backend="openai") \
        .generate_json(**_ARGS)
    (span,) = test_exporter_no_content.get_finished_spans()
    assert "input.value" not in span.attributes and "output.value" not in span.attributes
    assert span.attributes["llm.token_count.total"] == 150


@pytest.mark.asyncio
async def test_cancelled_call_ends_span_with_error(test_exporter: Any) -> None:
    t = RateLimitedTransport(_Hangs(), _NoWaitLimiter(), backend="openai")
    task = asyncio.create_task(t.generate_json(**_ARGS))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (span,) = test_exporter.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert span.status.description == "cancelled"


@pytest.mark.asyncio
async def test_tracing_off_is_a_passthrough() -> None:
    t = RateLimitedTransport(_FakeInner(), _NoWaitLimiter(), backend="openai")
    assert await t.generate_json(**_ARGS) == {"type": "answer", "text": "hi"}


@pytest.mark.parametrize("backend,provider", [
    ("openai", "openai"), ("chatgpt", "openai"), ("anthropic", "anthropic"),
    ("claude_subscription", "anthropic"), ("gemini", "google"), ("groq", "groq"),
    ("ollama", "ollama"), ("openai_compatible", "openai_compatible"),
])
def test_provider_for_backend(backend: str, provider: str) -> None:
    from agentd.telemetry.spans import provider_for_backend
    assert provider_for_backend(backend) == provider


def test_attribute_names_match_openinference() -> None:
    from openinference.semconv.trace import SpanAttributes

    from agentd.telemetry import spans
    assert spans.SPAN_KIND == SpanAttributes.OPENINFERENCE_SPAN_KIND
    assert spans.LLM_CACHE_READ == SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ
    assert spans.LLM_TOTAL == SpanAttributes.LLM_TOKEN_COUNT_TOTAL
    assert spans.TOOL_PARAMETERS == SpanAttributes.TOOL_PARAMETERS
