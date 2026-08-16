from agentd.chat.controller_loop import _parse_failure_guidance


def test_truncation_guidance_beats_the_symptom_matchers():
    """A truncated response stops mid-string, so the decoder's own message names a
    quoting symptom. The truncation marker must win — otherwise the model is told to
    fix escaping in JSON that was correct but unfinished, and re-sends the same
    oversized response, which is cut at the same point. Observed live as an
    unrecoverable loop."""
    msg = (
        "OpenAI-compatible output for controller_step_response was TRUNCATED — the "
        "response hit the output token budget before the JSON was finished "
        "(Unterminated string starting at: line 1 column 44 (char 43))."
    )
    guidance = _parse_failure_guidance(msg)
    assert "SMALLER" in guidance
    assert "quote" not in guidance.lower()


def test_a_real_quoting_error_still_gets_quoting_advice():
    msg = "output is not valid JSON (Unterminated string starting at: line 1 column 9)"
    assert "double quote" in _parse_failure_guidance(msg)
