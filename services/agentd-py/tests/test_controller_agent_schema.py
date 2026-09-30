import pytest

from agentd.chat.controller_loop import _empty_action_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.controller_prompts import _PHASE_TYPES, controller_response_schema


def _branch_types(schema: dict) -> list[str]:
    key = "oneOf" if "oneOf" in schema else "anyOf"
    return [b["properties"]["type"]["const"] for b in schema[key]]


def test_agent_phase_types() -> None:
    assert _PHASE_TYPES["AGENT"] == ["tool_call", "edit", "progress", "report"]
    assert ControllerPhaseSM(start="AGENT").allowed_types() == _PHASE_TYPES["AGENT"]


def test_invalid_start_still_raises() -> None:
    with pytest.raises(ValueError):
        ControllerPhaseSM(start="EDIT")


@pytest.mark.parametrize("mode", ["tight", "anyof"])
def test_agent_tight_schema_requires_a_report_summary(mode: str) -> None:
    schema = controller_response_schema(phase="AGENT", **{mode: True})
    assert _branch_types(schema) == ["tool_call", "edit", "progress", "report"]
    key = "oneOf" if mode == "tight" else "anyOf"
    report = next(b for b in schema[key] if b["properties"]["type"]["const"] == "report")
    assert report["required"] == ["type", "thought", "summary"]
    assert report["additionalProperties"] is False


def test_flat_agent_schema_trims_the_enum() -> None:
    schema = controller_response_schema(phase="AGENT")
    assert schema["properties"]["type"]["enum"] == ["tool_call", "edit", "progress", "report"]


def test_allowed_types_override_the_phase_table() -> None:
    types = ["tool_call", "answer", "clarify", "edit", "submit_changes", "progress", "propose_mode"]
    flat = controller_response_schema(phase="ACTIVE", allowed_types=types)
    assert flat["properties"]["type"]["enum"] == types
    tight = controller_response_schema(phase="ACTIVE", allowed_types=types, tight=True)
    assert _branch_types(tight) == types
    only_report = controller_response_schema(phase="AGENT", allowed_types=["report"], tight=True)
    assert _branch_types(only_report) == ["report"]


def test_no_allowed_types_keeps_todays_schemas() -> None:
    for phase in ("PLAN", "ACTIVE"):
        assert (controller_response_schema(phase=phase)
                == controller_response_schema(phase=phase, allowed_types=_PHASE_TYPES[phase]))


def test_empty_report_summary_is_corrected() -> None:
    assert _empty_action_correction({"type": "report", "summary": "  "}, "report")
    assert _empty_action_correction({"type": "report", "summary": "done"}, "report") is None
