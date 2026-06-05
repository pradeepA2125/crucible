import pytest
from agentd.chat.classifier import IntentClassifier
from agentd.chat.models import IntentType


class ScriptedTransport:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload) -> dict:
        return self._response


@pytest.mark.asyncio
async def test_plan_prefix_forces_large_change() -> None:
    classifier = IntentClassifier(transport=ScriptedTransport({}), model="test-model")
    result = await classifier.classify("/plan add a caching layer", context=[], history=[])
    assert result.intent == IntentType.LARGE_CHANGE


@pytest.mark.asyncio
async def test_classifier_returns_qa() -> None:
    classifier = IntentClassifier(
        transport=ScriptedTransport(
            {"intent": "qa", "rationale": "pure question", "likely_targets": []}
        ),
        model="test-model",
    )
    result = await classifier.classify("What does auth do?", context=[], history=[])
    assert result.intent == IntentType.QA


@pytest.mark.asyncio
async def test_classifier_returns_small_change() -> None:
    classifier = IntentClassifier(
        transport=ScriptedTransport(
            {"intent": "small_change", "rationale": "one file", "likely_targets": ["auth.py"]}
        ),
        model="test-model",
    )
    context = [{"tool": "search_code", "result": "auth.py:5: def authenticate"}]
    result = await classifier.classify("fix authenticate", context=context, history=[])
    assert result.intent == IntentType.SMALL_CHANGE
    assert result.likely_targets == ["auth.py"]


@pytest.mark.asyncio
async def test_classifier_receives_context_and_history_in_payload() -> None:
    received: list[dict] = []

    class CapturingTransport:
        async def generate_json(self, *, model, schema_name, schema,
                                system_instructions, user_payload) -> dict:
            received.append(user_payload)
            return {"intent": "small_change", "rationale": "ok", "likely_targets": []}

    classifier = IntentClassifier(transport=CapturingTransport(), model="test-model")
    context = [{"tool": "read_file", "result": "TIMEOUT = 10"}]
    history = [{"role": "user", "content": "look at config.py"}]
    await classifier.classify("fix that", context=context, history=history)
    assert received[0]["explore_context"] == context
    assert received[0]["conversation_history"] == history


@pytest.mark.asyncio
async def test_classifier_defaults_to_large_change_on_error() -> None:
    class FailingTransport:
        async def generate_json(self, **_) -> dict:
            raise RuntimeError("LLM down")

    classifier = IntentClassifier(transport=FailingTransport(), model="test-model")
    result = await classifier.classify("do something", context=[], history=[])
    assert result.intent == IntentType.LARGE_CHANGE
