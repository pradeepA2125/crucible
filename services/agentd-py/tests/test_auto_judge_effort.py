"""A per-call reasoning effort override (EFFORT_OVERRIDE) lets the Auto judge run at low
effort without touching the transport's own setting (plan Task 12)."""

from __future__ import annotations

from agentd.providers.claude_subscription import ClaudeSubscriptionTransport
from agentd.providers.openai_transport import OpenAIJsonTransport
from agentd.providers.reasoning_effort import EFFORT_OVERRIDE, ReasoningEffort


class _Exc(Exception):
    status_code = 400
    param = "reasoning.effort"


def test_openai_body_uses_the_override_for_this_call_only():
    transport = OpenAIJsonTransport(responses_client=object())
    transport.set_reasoning_effort(ReasoningEffort.HIGH)
    token = EFFORT_OVERRIDE.set(ReasoningEffort.LOW)
    try:
        assert transport._body("m", "sys", {"a": 1})["reasoning"]["effort"] == "low"
    finally:
        EFFORT_OVERRIDE.reset(token)
    assert transport._body("m", "sys", {"a": 1})["reasoning"]["effort"] == "high"


def test_rejected_override_is_dropped_without_marking_the_saved_effort():
    transport = OpenAIJsonTransport(responses_client=object())
    transport.set_reasoning_effort(ReasoningEffort.HIGH)
    token = EFFORT_OVERRIDE.set(ReasoningEffort.LOW)
    try:
        body = transport._body("m", "sys", {"a": 1})
        assert transport._note_effort_rejection(_Exc("bad effort"), body) is True
        assert "effort" not in body.get("reasoning", {})
    finally:
        EFFORT_OVERRIDE.reset(token)
    assert transport._effort == ReasoningEffort.HIGH
    assert ReasoningEffort.LOW not in transport._effort_rejected


def test_claude_body_uses_the_override():
    transport = ClaudeSubscriptionTransport(object(), registration_id="r", client=object())
    token = EFFORT_OVERRIDE.set(ReasoningEffort.LOW)
    try:
        body = transport._body("claude-haiku-5-5", "sys", [{"role": "user", "content": [
            {"type": "text", "text": "hi"}]}])
    finally:
        EFFORT_OVERRIDE.reset(token)
    assert body["output_config"] == {"effort": "low"}
