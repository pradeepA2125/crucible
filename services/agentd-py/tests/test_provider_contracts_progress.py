"""Finding 4 (final whole-branch review): TYPE_REQUIRED_FIELDS is a hand-maintained
mirror of controller_loop.py's _empty_action_correction (the module docstring says so
explicitly) — it went stale when `progress` was added there and gained a `note` guard,
but was never added here.

Effect of the gap: narrow_schema_for_type returned None (no narrowing needed) for a
`progress` result with an empty note, so every transport that retries with a narrowed
`required` list on a missing action field (anthropic_transport.py, openrouter_transport.py,
huggingface_transport.py, watsonx_transport.py) silently skipped that retry for `progress`
alone — the one type most likely to be emitted by a weak model still learning the new
action (this campaign's whole reason for existing).
"""
from __future__ import annotations

from agentd.providers.contracts import TYPE_REQUIRED_FIELDS, narrow_schema_for_type

_BASE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "thought": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["type", "thought"],
}


def test_progress_is_a_registered_required_field_type() -> None:
    assert TYPE_REQUIRED_FIELDS["progress"] == ["note"]


def test_narrow_schema_for_type_narrows_a_progress_result_missing_note() -> None:
    result = {"type": "progress", "thought": "narrating"}  # no 'note' at all
    narrowed = narrow_schema_for_type(_BASE_SCHEMA, result)
    assert narrowed is not None
    assert narrowed["required"] == ["type", "thought", "note"]


def test_narrow_schema_for_type_narrows_a_progress_result_with_empty_note() -> None:
    result = {"type": "progress", "thought": "narrating", "note": ""}
    narrowed = narrow_schema_for_type(_BASE_SCHEMA, result)
    assert narrowed is not None
    assert narrowed["required"] == ["type", "thought", "note"]


def test_narrow_schema_for_type_is_a_noop_for_a_complete_progress_result() -> None:
    result = {"type": "progress", "thought": "narrating", "note": "doing the thing"}
    assert narrow_schema_for_type(_BASE_SCHEMA, result) is None
