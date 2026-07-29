from agentd.chat.controller_prompts import (
    _PHASE_TYPES,
    _VARIANT_SPECS,
    CONTROLLER_RESPONSE_SCHEMA,
    controller_response_schema,
)


def test_progress_in_both_phase_types():
    assert "progress" in _PHASE_TYPES["ACTIVE"]
    assert "progress" in _PHASE_TYPES["PLAN"]


def test_progress_variant_spec_requires_note():
    spec = _VARIANT_SPECS["progress"]
    assert spec["required"] == ["note"]
    assert "note" in spec["properties"]


def test_flat_schema_exposes_note_and_progress_enum():
    assert "progress" in CONTROLLER_RESPONSE_SCHEMA["properties"]["type"]["enum"]
    assert "note" in CONTROLLER_RESPONSE_SCHEMA["properties"]


def test_flat_phase_schema_trims_progress_into_enum():
    schema = controller_response_schema(phase="ACTIVE")
    assert "progress" in schema["properties"]["type"]["enum"]


def test_tight_schema_has_progress_branch():
    schema = controller_response_schema(phase="ACTIVE", tight=True)
    branches = schema["oneOf"]
    progress = next(
        b for b in branches if b["properties"]["type"]["const"] == "progress"
    )
    assert "note" in progress["required"]
    assert progress["additionalProperties"] is False
