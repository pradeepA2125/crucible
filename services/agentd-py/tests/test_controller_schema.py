from agentd.chat.controller_prompts import (
    CONTROLLER_RESPONSE_SCHEMA,
    CONTROLLER_SYSTEM_PROMPT,
    controller_response_schema,
)


def _flat_patch_op_item() -> dict:
    return CONTROLLER_RESPONSE_SCHEMA["properties"]["patch_ops"]["items"]


def _tight_edit_patch_op_item() -> dict:
    tight = controller_response_schema(phase="EDIT", tight=True)
    edit = next(
        b for b in tight["oneOf"] if b["properties"]["type"]["const"] == "edit"
    )
    return edit["properties"]["patch_ops"]["items"]


def test_flat_schema_exposes_apply_diff_and_replace_range_ops():
    item = _flat_patch_op_item()
    ops = set(item["properties"]["op"]["enum"])
    assert {"apply_diff", "replace_range"} <= ops, ops
    # the op-specific fields must be present or the model cannot emit them
    assert "diff" in item["properties"]
    assert "anchor" in item["properties"]


def _tight_op_branches() -> dict[str, dict]:
    """{op_const: branch} for the tight patch-op-item oneOf."""
    item = _tight_edit_patch_op_item()
    return {b["properties"]["op"]["const"]: b for b in item["oneOf"]}


def test_tight_schema_exposes_apply_diff_and_replace_range_ops():
    branches = _tight_op_branches()
    assert {"create_file", "search_replace", "apply_diff", "replace_range"} <= set(branches)
    # each branch is a closed object (grammar can't bleed cross-op fields)
    for b in branches.values():
        assert b.get("additionalProperties") is False


def test_tight_schema_marks_required_fields_per_op():
    # Per-op oneOf so a constrained-grammar provider (tight path) FORCES each op's
    # own fields — the fix for a replace_range emitted without its required content.
    by_op = _tight_op_branches()
    assert "content" in by_op["replace_range"]["required"]
    assert "anchor" in by_op["replace_range"]["required"]
    assert "diff" in by_op["apply_diff"]["required"]
    assert "search" in by_op["search_replace"]["required"]
    assert "replace" in by_op["search_replace"]["required"]
    assert "content" in by_op["create_file"]["required"]
    # and a branch does NOT carry another op's field (closed per-op shape)
    assert "diff" not in by_op["create_file"]["properties"]
    assert "content" not in by_op["apply_diff"]["properties"]


def test_system_prompt_teaches_apply_diff_and_replace_range():
    assert "apply_diff" in CONTROLLER_SYSTEM_PROMPT
    assert "replace_range" in CONTROLLER_SYSTEM_PROMPT


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
