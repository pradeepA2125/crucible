from agentd.chat.controller_prompts import (
    CONTROLLER_RESPONSE_SCHEMA,
    controller_response_schema,
)


def test_schema_is_flat_not_oneof():
    assert "oneOf" not in CONTROLLER_RESPONSE_SCHEMA and "anyOf" not in CONTROLLER_RESPONSE_SCHEMA
    enum = CONTROLLER_RESPONSE_SCHEMA["properties"]["type"]["enum"]
    assert set(enum) == {"tool_call", "answer", "clarify", "propose_mode", "edit", "submit_changes"}
    # plan_sketch present for propose_mode
    assert "plan_sketch" in CONTROLLER_RESPONSE_SCHEMA["properties"]


def test_phase_gating_trims_type_enum():
    decide = controller_response_schema(phase="DECIDE")["properties"]["type"]["enum"]
    assert set(decide) == {"tool_call", "answer", "clarify", "propose_mode"}
    edit = controller_response_schema(phase="EDIT")["properties"]["type"]["enum"]
    assert set(edit) == {"tool_call", "edit", "submit_changes"}
    # deep-copy: mutating the returned schema must not affect the module-level one
    decide.append("__probe__")
    assert "__probe__" not in CONTROLLER_RESPONSE_SCHEMA["properties"]["type"]["enum"]
