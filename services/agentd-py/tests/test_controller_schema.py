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
    # clarify is allowed in EDIT (ask when blocked mid-edit); propose_mode is not.
    assert set(edit) == {"tool_call", "edit", "clarify", "submit_changes"}
    # deep-copy: mutating the returned schema must not affect the module-level one
    decide.append("__probe__")
    assert "__probe__" not in CONTROLLER_RESPONSE_SCHEMA["properties"]["type"]["enum"]


def test_thread_live_state_turn_active_defaults_false():
    from agentd.chat.models import ThreadLiveState

    state = ThreadLiveState()
    assert state.turn_active is False
    # round-trips through model_dump (the /live route serializes with this)
    assert state.model_dump()["turn_active"] is False
    assert ThreadLiveState(turn_active=True).turn_active is True
