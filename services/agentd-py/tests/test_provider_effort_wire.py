import pytest

from agentd.providers.gemini_transport import GeminiJsonTransport
from agentd.providers.groq_transport import GroqJsonTransport
from agentd.providers.ollama_transport import OllamaJsonTransport
from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.openrouter_transport import OpenRouterJsonTransport
from agentd.providers.reasoning_effort import ReasoningEffort
from agentd.providers.turboquant_transport import PROFILES, TurboQuantTransport


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


def test_no_effort_field_for_a_non_reasoning_model():
    # reasoning_effort_support declares everything but OFF unsupported for a plain
    # chat model, so any picked rung clamps to OFF — which must NOT then send
    # reasoning_effort="none" to a model that has no such parameter (OpenAI's own
    # API rejects it).
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    body = t._build_extra_body("some-plain-chat-model", False, for_json=True)
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


def test_groq_unset_dial_preserves_the_env_configured_effort():
    # The env-fallback path must stay byte-identical to pre-feature behavior. Both
    # branches of _effort_wire_value return a str, so a reordering that made the
    # dial's default win would be silent — this is the only test that pins it.
    t = GroqJsonTransport(
        api_key="k", reasoning_effort="medium", completions_client=object()
    )
    assert t._effort_wire_value() == "medium"


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


def _ollama() -> OllamaJsonTransport:
    return OllamaJsonTransport(host="http://localhost:11434")


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        (ReasoningEffort.OFF, False),
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        (ReasoningEffort.MAX, "high"),
    ],
)
def test_ollama_think_values(level, wire):
    t = _ollama()
    t.set_reasoning_effort(level)
    assert t._effort_think_value() == wire


def test_ollama_unset_dial_preserves_the_env_configured_think():
    t = OllamaJsonTransport(host="http://localhost:11434", think="medium")
    assert t._effort_think_value() == "medium"


def test_ollama_think_rides_the_body_top_level_not_options():
    t = _ollama()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    # _build_body is KEYWORD-ONLY (verified against the real signature).
    body = t._build_body(
        model="qwen3", system="sys", user_content="user", json_format=None, num_predict=100
    )
    assert body["think"] == "low"
    assert "think" not in body["options"]


def test_ollama_omits_think_entirely_when_nothing_is_set():
    body = _ollama()._build_body(
        model="qwen3", system="sys", user_content="user", json_format=None, num_predict=100
    )
    assert "think" not in body


@pytest.mark.asyncio
async def test_ollama_supports_the_whole_ladder_except_max():
    support = await _ollama().reasoning_effort_support("qwen3")
    assert support.state(ReasoningEffort.OFF) == "supported"
    assert support.state(ReasoningEffort.MAX) == "unsupported"


def _gemini() -> GeminiJsonTransport:
    return GeminiJsonTransport(api_key="k", thinking_enabled=True)


@pytest.mark.parametrize(
    ("level", "expected_level"),
    [
        (ReasoningEffort.LOW, "minimal"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        (ReasoningEffort.MAX, "high"),
    ],
)
def test_gemini_sets_thinking_level(level, expected_level):
    t = _gemini()
    t.set_reasoning_effort(level)
    config = t._build_thinking_config()
    assert config["thinking_level"] == expected_level


def test_gemini_off_puts_an_explicit_zero_budget_on_the_wire():
    # Returning None would OMIT thinking_config, leaving the model on its own
    # default — which for the Gemini thinking families is thinking ON, making the
    # rung a placebo while support declares it verified. OFF must disable
    # explicitly.
    t = _gemini()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    assert t._build_thinking_config() == {"thinking_budget": 0}


def test_gemini_never_sends_level_and_budget_together():
    # Verified provider fact: Gemini 3 returns an error when both are present.
    t = GeminiJsonTransport(api_key="k", thinking_enabled=True, thinking_budget=8000)
    t.set_reasoning_effort(ReasoningEffort.HIGH)
    config = t._build_thinking_config()
    assert "thinking_level" in config
    assert "thinking_budget" not in config


@pytest.mark.asyncio
async def test_turboquant_declares_the_whole_ladder_unsupported():
    # TurboQuant applies its JSON-schema GBNF grammar ONLY when thinking is off
    # (_build_body gates on self._profile.thinking_budget == 0, working around
    # llama.cpp#20345). Raising effort there would silently disable grammar
    # enforcement on the one provider whose grammar is the main defense against
    # malformed edits, so v1 declares the dial unavailable and says why.
    t = TurboQuantTransport(profile=PROFILES["qwen3"])
    support = await t.reasoning_effort_support("qwen3")
    for level in ReasoningEffort:
        assert support.state(level) == "unsupported"
        assert "grammar" in support.unsupported[level]


def test_turboquant_has_no_effort_setter_so_nothing_is_ever_sent():
    # The runtime's setter call is getattr-guarded; absent means the transport is
    # never asked, which is exactly the intended no-op.
    assert not hasattr(TurboQuantTransport, "set_reasoning_effort")
