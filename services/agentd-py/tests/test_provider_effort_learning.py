import pytest

from agentd.providers.openai_compatible_transport import (
    OpenAICompatibleTransport,
    _is_probative_effort_rejection,
)
from agentd.providers.reasoning_effort import ReasoningEffort


def _transport() -> OpenAICompatibleTransport:
    return OpenAICompatibleTransport(
        base_url="http://localhost:9/v1", completions_client=object()
    )


class _Err(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status


def test_400_naming_the_parameter_is_probative():
    assert _is_probative_effort_rejection(
        _Err("400: unsupported value 'none' for reasoning_effort", 400)
    )


def test_rate_limit_is_not_probative():
    # A 429 that happened to mention the field must never permanently pin the
    # session to a lower rung — same rule the JSON-mode downgrade already follows.
    assert not _is_probative_effort_rejection(_Err("429 rate limited reasoning_effort", 429))


def test_server_error_is_not_probative():
    assert not _is_probative_effort_rejection(_Err("503 overloaded reasoning_effort", 503))


def test_400_about_something_else_is_not_probative():
    assert not _is_probative_effort_rejection(_Err("400: context length exceeded", 400))


def test_timeout_is_not_probative():
    assert not _is_probative_effort_rejection(TimeoutError("timed out"))


@pytest.mark.asyncio
async def test_a_rejection_marks_only_that_rung():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    assert t._note_effort_rejection(_Err("400 invalid reasoning_effort 'none'", 400)) is True

    support = await t.reasoning_effort_support("nvidia/nemotron-3")
    assert support.state(ReasoningEffort.OFF) == "unsupported"
    # Every other rung is untouched — the capability as a whole survives.
    assert support.state(ReasoningEffort.LOW) == "unknown"
    assert support.state(ReasoningEffort.HIGH) == "unknown"


def test_a_rejection_clears_the_effort_so_the_retry_omits_it():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    t._note_effort_rejection(_Err("400 invalid reasoning_effort 'none'", 400))
    assert "reasoning_effort" not in t._build_extra_body("m", True, for_json=True)


def test_a_transient_failure_records_nothing():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    assert t._note_effort_rejection(_Err("429 slow down", 429)) is False
    assert t._build_extra_body("m", True, for_json=True)["reasoning_effort"] == "none"


def test_nothing_recorded_when_no_effort_was_set():
    t = _transport()
    assert t._note_effort_rejection(_Err("400 invalid reasoning_effort", 400)) is False


# The fakes below deliberately duplicate the ones in
# tests/test_openai_compatible_transport.py rather than importing them: that file
# is its own regression net and must stay decoupled from this one (same reasoning
# as its own header comment about test_openrouter_transport.py).


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = None


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Records every create() call and replays a scripted list of contents.

    A scripted item may be an Exception (raised) or a str (wrapped in a
    _FakeResponse) — mirrors the fake in test_openai_compatible_transport.py.
    """

    def __init__(self, contents: list[object]) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        item = self._contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.mark.asyncio
async def test_generate_text_retries_once_after_a_probative_rejection():
    """The real seam: a probative 400 on the first call, a clean second call.

    Drives the actual generate_text call path (not _note_effort_rejection in
    isolation) — this is the seam the Critical finding lived in.
    """
    fake = _FakeCompletions(
        [_Err("400 invalid reasoning_effort 'none'", 400), "ok"]
    )
    t = OpenAICompatibleTransport(base_url="http://localhost:9/v1", completions_client=fake)
    t.set_reasoning_effort(ReasoningEffort.OFF)

    result = await t.generate_text(
        model="nvidia/nemotron-3", system_instructions="sys", user_payload={"goal": "x"}
    )

    assert result == "ok"
    assert len(fake.calls) == 2
    assert "reasoning_effort" not in fake.calls[1].get("extra_body", {})


@pytest.mark.asyncio
async def test_generate_text_wraps_a_second_failure_after_the_retry():
    """Regression guard for the Critical finding: when the retry ALSO fails, the
    raised error must still be a RuntimeError carrying the transport label — not
    the raw provider exception escaping unwrapped."""
    fake = _FakeCompletions(
        [_Err("400 invalid reasoning_effort 'none'", 400), RuntimeError("still broken")]
    )
    t = OpenAICompatibleTransport(
        base_url="http://localhost:9/v1", completions_client=fake, label="TestLabel"
    )
    t.set_reasoning_effort(ReasoningEffort.OFF)

    with pytest.raises(RuntimeError) as exc_info:
        await t.generate_text(
            model="nvidia/nemotron-3", system_instructions="sys", user_payload={"goal": "x"}
        )

    assert "TestLabel" in str(exc_info.value)
