import pytest

from agentd.providers.groq_transport import GroqJsonTransport
from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.openrouter_transport import OpenRouterJsonTransport
from agentd.providers.reasoning_effort import ReasoningEffort


def _transport() -> OpenAICompatibleTransport:
    # completions_client short-circuits real client construction (see __init__).
    return OpenAICompatibleTransport(
        base_url="http://localhost:9/v1", completions_client=object()
    )


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        (ReasoningEffort.OFF, "none"),
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        # vLLM's ladder tops out at high; MAX is declared unsupported so the runtime
        # clamps before we get here, but the map must still be total.
        (ReasoningEffort.MAX, "high"),
    ],
)
def test_effort_rides_the_body_as_top_level_reasoning_effort(level, wire):
    t = _transport()
    t.set_reasoning_effort(level)
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=True)
    # extra_body is merged into the request body at top level by the OpenAI SDK,
    # which is exactly where vLLM reads reasoning_effort.
    assert body["reasoning_effort"] == wire


def test_no_effort_field_when_unset():
    t = _transport()
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=True)
    assert "reasoning_effort" not in body


def test_none_clears_a_previously_set_effort():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    t.set_reasoning_effort(None)
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=True)
    assert "reasoning_effort" not in body


def test_effort_is_sent_on_text_calls_too():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=False)
    assert body["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_support_marks_max_unsupported_and_leaves_the_rest_unknown():
    t = _transport()
    support = await t.reasoning_effort_support("nvidia/nemotron-3")
    assert support.state(ReasoningEffort.MAX) == "unsupported"
    # We cannot verify an arbitrary pasted endpoint — unknown, not supported.
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_non_reasoning_model_supports_only_off():
    t = _transport()
    support = await t.reasoning_effort_support("some-plain-chat-model")
    assert support.state(ReasoningEffort.OFF) == "supported"
    assert support.state(ReasoningEffort.HIGH) == "unsupported"


def _openrouter() -> OpenRouterJsonTransport:
    return OpenRouterJsonTransport(api_key="k", completions_client=object())


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (ReasoningEffort.OFF, {"enabled": False}),
        (ReasoningEffort.LOW, {"effort": "low"}),
        (ReasoningEffort.MEDIUM, {"effort": "medium"}),
        (ReasoningEffort.HIGH, {"effort": "high"}),
        (ReasoningEffort.MAX, {"effort": "max"}),
    ],
)
def test_openrouter_uses_the_nested_reasoning_object(level, expected):
    t = _openrouter()
    t.set_reasoning_effort(level)
    body = t._build_extra_body("deepseek/deepseek-r1", True, for_json=True)
    assert body["reasoning"] == expected
    # The base class's blanket {"enabled": True} must not survive alongside it.
    assert "reasoning_effort" not in body


@pytest.mark.asyncio
async def test_openrouter_supports_the_whole_ladder_for_a_reasoning_model():
    t = _openrouter()
    support = await t.reasoning_effort_support("deepseek/deepseek-r1")
    for level in ReasoningEffort:
        assert support.state(level) == "supported"


def _groq() -> GroqJsonTransport:
    return GroqJsonTransport(api_key="k", completions_client=object())


@pytest.mark.asyncio
async def test_groq_declares_off_unsupported_because_it_400s():
    t = _groq()
    support = await t.reasoning_effort_support("openai/gpt-oss-120b")
    assert support.state(ReasoningEffort.OFF) == "unsupported"
    assert "none" in support.unsupported[ReasoningEffort.OFF]
    assert support.state(ReasoningEffort.MAX) == "unsupported"
    for level in (ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH):
        assert support.state(level) == "supported"


@pytest.mark.asyncio
async def test_groq_off_clamps_upward_to_low():
    support = await _groq().reasoning_effort_support("openai/gpt-oss-120b")
    effective, note = support.resolve(ReasoningEffort.OFF)
    assert effective == ReasoningEffort.LOW
    assert note is not None


def test_groq_never_emits_none_on_the_wire():
    # Regression guard for a verified provider fact: Groq rejects
    # reasoning_effort="none" with a 400.
    t = _groq()
    for level in ReasoningEffort:
        t.set_reasoning_effort(level)
        assert t._effort_wire_value() != "none"


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        (ReasoningEffort.MAX, "high"),
        (ReasoningEffort.OFF, "low"),
    ],
)
def test_groq_wire_values(level, wire):
    t = _groq()
    t.set_reasoning_effort(level)
    assert t._effort_wire_value() == wire
